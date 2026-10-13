"""End to end: minimize -> redact -> guard, on a real Synthea fixture."""

import pytest

from adapters.synthea import SyntheaAdapter
from detector.discharge import detect_for_patient
from models import DischargeDestination
from privacy.guard import KnownIdentifiers, UnredactedIdentifierError
from privacy.payload import NoAIDraftError
from privacy.prepare import prepare_llm_payload
from privacy.redactor import RedactionResult, Redactor
from tests.conftest import MED_CHANGES_ID


class NoOpRedactor(Redactor):
    """Simulates a redactor that misses everything, to prove the guard still trips."""

    def redact(self, text: str, allow_list: list[str] | None = None) -> RedactionResult:
        return RedactionResult(text=text, entity_types=())

    def analyze(self, text: str, allow_list: list[str] | None = None) -> list:  # type: ignore[type-arg]
        return []


def episode(adapter: SyntheaAdapter):  # type: ignore[no-untyped-def]
    [n] = detect_for_patient(adapter, MED_CHANGES_ID)
    ids = {n.encounter_id, n.linked_er_encounter_id}
    patient = adapter.get_patient(MED_CHANGES_ID)
    conditions = [c for c in adapter.get_conditions(MED_CHANGES_ID) if c.encounter_id in ids]
    meds = [m for m in adapter.get_medication_requests(MED_CHANGES_ID) if m.encounter_id in ids]
    return n, patient, conditions, meds


def instructions_with_identifiers(adapter: SyntheaAdapter, synthea_style: bool = False) -> str:
    """Discharge text naming the fixture patient.

    Real names have no digits, so by default the Synthea suffixes are stripped
    ("Nichelle912" -> "Nichelle").
    """
    p = adapter.get_patient(MED_CHANGES_ID)
    given, family = p.given_names[0], p.family_name or ""
    if not synthea_style:
        given, family = given.rstrip("0123456789"), family.rstrip("0123456789")
    return (
        f"{given} {family} (DOB {p.birth_date:%m/%d/%Y}) discharged home. "
        f"Call {p.phones[0]} with questions. Follow up with Dr. Maria Okafor on 06/03/2025. "
        "Take aspirin 81 MG Oral Tablet daily and sacubitril 24 MG / valsartan 26 MG Oral "
        "Tablet [Entresto] twice daily for 30 days."
    )


def test_identifiers_removed_clinical_kept(synthea: SyntheaAdapter, redactor: Redactor) -> None:
    n, patient, conditions, meds = episode(synthea)
    text = instructions_with_identifiers(synthea)
    payload = prepare_llm_payload(n, patient, conditions, meds, redactor, instructions=text)

    assert payload.instructions is not None
    assert KnownIdentifiers.from_patient(patient, n).find(payload.model_dump_json()) == []
    assert "Okafor" not in payload.instructions
    assert "06/03/2025" not in payload.instructions
    # Clinical content survives, including the coded brand name (allow-listed).
    assert "aspirin 81 MG Oral Tablet" in payload.instructions
    assert "[Entresto]" in payload.instructions
    assert "for 30 days" in payload.instructions


def test_guard_trips_when_redaction_misses(synthea: SyntheaAdapter) -> None:
    n, patient, conditions, meds = episode(synthea)
    text = instructions_with_identifiers(synthea)
    with pytest.raises(UnredactedIdentifierError) as exc:
        prepare_llm_payload(n, patient, conditions, meds, NoOpRedactor(), instructions=text)
    assert {"known:name", "known:birth_date", "known:phone"} <= set(exc.value.findings)


def test_guard_blocks_synthea_style_name_that_ner_misses(
    synthea: SyntheaAdapter, redactor: Redactor
) -> None:
    # spaCy doesn't tag "Nichelle912 Kovacek682" as a PERSON (digits), so redaction
    # leaves it in place. The known-identifier layer must block the payload.
    n, patient, conditions, meds = episode(synthea)
    text = instructions_with_identifiers(synthea, synthea_style=True)
    assert patient.given_names[0] in redactor.redact(text).text
    with pytest.raises(UnredactedIdentifierError, match="known:name"):
        prepare_llm_payload(n, patient, conditions, meds, redactor, instructions=text)


def test_expired_patient_never_prepared(synthea: SyntheaAdapter, redactor: Redactor) -> None:
    n, patient, conditions, meds = episode(synthea)
    expired = n.model_copy(update={"destination": DischargeDestination.EXPIRED})
    with pytest.raises(NoAIDraftError):
        prepare_llm_payload(expired, patient, conditions, meds, redactor)


def test_no_instructions_is_fine(synthea: SyntheaAdapter, redactor: Redactor) -> None:
    n, patient, conditions, meds = episode(synthea)
    payload = prepare_llm_payload(n, patient, conditions, meds, redactor)
    assert payload.instructions is None
    assert payload.medications
