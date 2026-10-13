"""Last check before an LLM call. It reduces risk; it CANNOT guarantee detection.

Two layers:
1. Known identifiers (deterministic). We know this patient's name, DOB, MRN, phone,
   address, and record ids, so we search the serialized payload for each of them.
   This reliably catches leaks of THIS patient's identifiers in the formats we check.
2. Presidio re-scan of the free-text fields after redaction. Anything still flagged
   fails the guard.

What it can miss: identifiers of other people (family members, other clinicians) that
NER doesn't recognize, identifiers in formats we don't normalize (e.g. a DOB written
as "the fifteenth of January"), and nicknames or misspellings. Data minimization
(privacy/payload.py) is the primary control; this is defence in depth.

Errors never include the identifier values, only their kind and field, so logs and
the retry folder don't receive PHI.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from models import DischargeNotice, Patient
from privacy.payload import LLMPayload
from privacy.redactor import Redactor

MIN_NAME_LENGTH = 3


class UnredactedIdentifierError(RuntimeError):
    def __init__(self, findings: list[str]) -> None:
        self.findings = findings
        super().__init__(f"LLM payload failed privacy guard: {', '.join(findings)}")


def _date_formats(d: date) -> set[str]:
    month = d.strftime("%B")
    return {
        d.isoformat(),
        f"{d.month:02d}/{d.day:02d}/{d.year}",
        f"{d.month}/{d.day}/{d.year}",
        f"{d.month:02d}-{d.day:02d}-{d.year}",
        f"{month} {d.day}, {d.year}",
        f"{month} {d.day} {d.year}",
        f"{d.day} {month} {d.year}",
        f"{month[:3]} {d.day}, {d.year}",
    }


def _name_variants(name: str) -> set[str]:
    """Synthea appends digits to names ('Agnes294'); check with and without them."""
    variants = {name, re.sub(r"\d+$", "", name)}
    return {v for v in variants if len(v) >= MIN_NAME_LENGTH}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


@dataclass(frozen=True)
class KnownIdentifiers:
    terms: dict[str, str] = field(default_factory=dict)  # value -> kind
    phone_digits: frozenset[str] = frozenset()

    @classmethod
    def from_patient(
        cls, patient: Patient, notice: DischargeNotice | None = None
    ) -> "KnownIdentifiers":
        terms: dict[str, str] = {}

        def add(kind: str, *values: str | None) -> None:
            for v in values:
                if v and v.strip():
                    terms[v.strip()] = kind

        for name in (*patient.given_names, patient.family_name or ""):
            add("name", *_name_variants(name))
        add("patient_id", patient.id)
        add("mrn", patient.mrn)
        if patient.birth_date:
            add("birth_date", *_date_formats(patient.birth_date))
        if patient.address:
            add("address", *patient.address.lines, patient.address.city)
            add("postal_code", patient.address.postal_code)
        if notice:
            add("encounter_id", notice.encounter_id, notice.linked_er_encounter_id)
        phones = frozenset(d[-10:] for d in map(_digits, patient.phones) if len(d) >= 7)
        return cls(terms=terms, phone_digits=phones)

    def find(self, text: str) -> list[str]:
        """Kinds of known identifiers present in `text` (values are not returned)."""
        found = {
            kind
            for value, kind in self.terms.items()
            if re.search(rf"(?<![\w]){re.escape(value)}(?![\w])", text, re.IGNORECASE)
        }
        for candidate in re.findall(r"\+?[\d(][\d\s().-]{5,}\d", text):
            if self.phone_digits and _digits(candidate)[-10:] in self.phone_digits:
                found.add("phone")
        return sorted(found)


def _free_text_fields(payload: LLMPayload) -> dict[str, str]:
    fields = {"instructions": payload.instructions or ""}
    for i, med in enumerate(payload.medications):
        fields[f"medications[{i}].dosage"] = med.dosage or ""
    return {k: v for k, v in fields.items() if v}


def assert_redacted(
    payload: LLMPayload,
    known: KnownIdentifiers,
    redactor: Redactor | None = None,
    allow_list: list[str] | None = None,
) -> None:
    """Raise UnredactedIdentifierError if the payload looks like it contains identifiers.

    Passing is not proof that the payload is clean (see module docstring).
    """
    findings = [f"known:{kind}" for kind in known.find(payload.model_dump_json())]
    if redactor is not None:
        for name, text in _free_text_fields(payload).items():
            # Placeholders like <PERSON> are the redactor's own output, not leaks.
            stripped = re.sub(r"<[A-Z_]+>", " ", text)
            types = sorted({r.entity_type for r in redactor.analyze(stripped, allow_list)})
            findings.extend(f"presidio:{name}:{t}" for t in types)
    if findings:
        raise UnredactedIdentifierError(findings)
