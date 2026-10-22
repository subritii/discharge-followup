"""Data minimization: the ONLY shape of data allowed to reach an LLM.

LLMPayload is the allowlist. It forbids extra fields, and `build_llm_payload`
never receives a Patient, so names, DOB, address, phone, MRN, and record ids
can't enter the LLM path through structured fields. Dates are reduced to a
length of stay, since calendar dates are identifiers under HIPAA Safe Harbor.

Free-text fields (`instructions`, `MedicationLine.dosage`) still need redaction
(privacy/redactor.py), and the whole payload is checked by privacy/guard.py.
"""

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from models import (
    CaseRoute,
    Condition,
    DischargeDestination,
    DischargeNotice,
    MedChange,
    MedChangeKind,
    TriageResult,
)


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MedicationLine(_Strict):
    name: str
    dosage: str | None = None  # free text
    change: MedChangeKind  # from agent/med_diff.py (deterministic)


# Plain wording instead of codes: an 8B model read "EMER" as a hospital stay and
# 0.6 days as "6 days".
VisitType = Literal["ER visit (not admitted)", "hospital stay (admitted)"]
VISIT_TYPES: dict[str, VisitType] = {
    "EMER": "ER visit (not admitted)",
    "IMP": "hospital stay (admitted)",
}


def format_length_of_stay(seconds: float) -> str:
    """Hours under a day ("15 hours"), whole days after that ("23 days")."""
    hours = max(round(seconds / 3600), 1)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = round(seconds / 86400)
    return f"{days} day{'s' if days != 1 else ''}"


class LLMPayload(_Strict):
    visit_type: VisitType
    destination: DischargeDestination
    length_of_stay: str
    diagnoses: tuple[str, ...] = ()
    medications: tuple[MedicationLine, ...] = ()
    instructions: str | None = None  # free text


# Reviewed allowlist. Changing LLMPayload must also change this (see test_payload.py).
ALLOWED_FIELDS = frozenset(
    {
        "visit_type",
        "destination",
        "length_of_stay",
        "diagnoses",
        "medications",
        "instructions",
    }
)
FREE_TEXT_FIELDS = frozenset({"instructions", "medications.dosage"})


class NoAIDraftError(ValueError):
    """Raised when a discharge must not get an AI draft (e.g. the patient expired)."""


def _unique(values: Iterable[str | None]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v.strip() for v in values if v and v.strip()))


def build_llm_payload(
    notice: DischargeNotice,
    diagnoses: Iterable[Condition],
    med_changes: Iterable[MedChange],
    instructions: str | None = None,
) -> LLMPayload:
    """Select clinical fields only. Callers choose which conditions are relevant;
    `med_changes` comes from agent.med_diff."""
    if not notice.requires_patient_follow_up:
        reason = "deceased date unknown" if notice.deceased_date_unknown else "expired"
        raise NoAIDraftError(f"no AI draft: {reason}")
    stay = notice.discharge_time - notice.admit_time
    return LLMPayload(
        visit_type=VISIT_TYPES[notice.encounter_class],
        destination=notice.destination,
        length_of_stay=format_length_of_stay(max(stay.total_seconds(), 0)),
        diagnoses=_unique(c.code.display for c in diagnoses),
        medications=tuple(
            MedicationLine(name=m.name, dosage=m.dosage, change=m.change) for m in med_changes
        ),
        instructions=instructions,
    )


# --- TriageResult, minus identifiers -----------------------------------------------


class RuleLine(_Strict):
    id: str
    detail: str


class TriageContext(_Strict):
    """The parts of a TriageResult the LLM may see: route and non-risk notes.

    No ids, no calendar dates, and no risk tier / LACE / Charlson: the risk explanation
    is built deterministically (agent/risk_explanation.py), so the model has nothing to
    misstate. Tasks and deadlines are left out for the same reason: given them, the
    model turned an ER outreach call into "a follow-up visit". They are on the Case as
    rule-based fields (Next_Action_Due__c, Visit_Due_Date__c).
    """

    route: CaseRoute
    notes: tuple[RuleLine, ...] = ()  # e.g. DESTINATION_UNVERIFIED


TRIAGE_CONTEXT_FIELDS = frozenset({"route", "notes"})

# Rules that explain risk; kept out of the LLM input.
RISK_RULE_PREFIXES = ("LACE_", "ER_")


def triage_context(result: TriageResult) -> TriageContext:
    return TriageContext(
        route=result.route,
        notes=tuple(
            RuleLine(id=r.id, detail=r.detail)
            for r in result.fired_rules
            if not r.id.startswith(RISK_RULE_PREFIXES)
        ),
    )
