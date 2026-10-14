"""Data minimization: only allowlisted clinical fields can reach the LLM."""

import inspect
import re
import typing
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from adapters.synthea import SyntheaAdapter
from agent.med_diff import med_diff
from detector.discharge import detect_for_patient
from models import (
    Coding,
    Condition,
    DischargeDestination,
    DischargeNotice,
    MedChange,
    MedChangeKind,
    Patient,
)
from privacy.guard import KnownIdentifiers
from privacy.payload import (
    ALLOWED_FIELDS,
    TRIAGE_CONTEXT_FIELDS,
    LLMPayload,
    MedicationLine,
    NoAIDraftError,
    TriageContext,
    build_llm_payload,
    triage_context,
)
from tests.conftest import ER_ID, INPATIENT_ID, MED_CHANGES_ID

T0 = datetime(2025, 5, 21, 18, 20, 59, tzinfo=UTC)


def notice(**overrides: object) -> DischargeNotice:
    fields: dict[str, object] = {
        "patient_id": "p1",
        "encounter_id": "e1",
        "encounter_class": "IMP",
        "admit_time": T0,
        "discharge_time": T0 + timedelta(days=1),
    }
    fields.update(overrides)
    return DischargeNotice(**fields)


def episode_payload(adapter: SyntheaAdapter, patient_id: str) -> tuple[LLMPayload, Patient]:
    [n] = detect_for_patient(adapter, patient_id)
    encounter_ids = {n.encounter_id, n.linked_er_encounter_id}
    conditions = [c for c in adapter.get_conditions(patient_id) if c.encounter_id in encounter_ids]
    changes = med_diff(
        n, adapter.get_medication_requests(patient_id), adapter.get_encounters(patient_id)
    )
    return build_llm_payload(n, conditions, changes), adapter.get_patient(patient_id)


# --- The allowlist itself ---------------------------------------------------------


def test_allowlist_is_exactly_the_approved_fields() -> None:
    approved = {
        "encounter_class",
        "destination",
        "length_of_stay_days",
        "diagnoses",
        "medications",
        "instructions",
    }
    assert set(LLMPayload.model_fields) == ALLOWED_FIELDS == approved
    assert set(MedicationLine.model_fields) == {"name", "dosage", "change"}


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        LLMPayload(
            encounter_class="IMP",
            destination=DischargeDestination.HOME,
            length_of_stay_days=1,
            patient_name="Jane Doe",  # type: ignore[call-arg]
        )


def test_builder_never_receives_a_patient() -> None:
    hints = typing.get_type_hints(build_llm_payload)
    assert Patient not in hints.values()
    assert "patient" not in inspect.signature(build_llm_payload).parameters


# --- Real fixtures: identifiers never appear --------------------------------------


@pytest.mark.parametrize("patient_id", [INPATIENT_ID, ER_ID, MED_CHANGES_ID])
def test_no_identifiers_in_payload_from_fixtures(synthea: SyntheaAdapter, patient_id: str) -> None:
    payload, patient = episode_payload(synthea, patient_id)
    [n] = detect_for_patient(synthea, patient_id)
    dumped = payload.model_dump_json()

    assert KnownIdentifiers.from_patient(patient, n).find(dumped) == []
    raw_values = [
        *patient.given_names,
        patient.family_name,
        patient.id,
        patient.mrn,
        str(patient.birth_date),
        *patient.phones,
        *(patient.address.lines if patient.address else ()),
        patient.address.city if patient.address else None,
        n.encounter_id,
        n.linked_er_encounter_id,
    ]
    for value in filter(None, raw_values):
        assert value not in dumped
    # No calendar dates or record ids of any kind.
    assert not re.search(r"\d{4}-\d{2}-\d{2}", dumped)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", dumped)


def test_med_changes_payload_content(synthea: SyntheaAdapter) -> None:
    payload, _ = episode_payload(synthea, MED_CHANGES_ID)
    assert payload.encounter_class == "IMP"
    assert payload.destination == DischargeDestination.UNKNOWN
    assert payload.length_of_stay_days == 1.0
    assert "Acute ST segment elevation myocardial infarction (disorder)" in payload.diagnoses
    started = [m.name for m in payload.medications if m.change == "started"]
    assert "aspirin 81 MG Oral Tablet" in started
    assert "sacubitril 24 MG / valsartan 26 MG Oral Tablet [Entresto]" in started


# --- Builder behaviour ------------------------------------------------------------


def test_expired_gets_no_ai_draft() -> None:
    with pytest.raises(NoAIDraftError):
        build_llm_payload(notice(destination=DischargeDestination.EXPIRED), [], [])


def test_deceased_date_unknown_gets_no_ai_draft() -> None:
    with pytest.raises(NoAIDraftError):
        build_llm_payload(notice(deceased_date_unknown=True), [], [])


def test_length_of_stay_rounded_days() -> None:
    n = notice(discharge_time=T0 + timedelta(days=2, hours=7))
    assert build_llm_payload(n, [], []).length_of_stay_days == 2.3


def test_diagnoses_deduplicated_and_meds_passed_through() -> None:
    cond = Condition(id="c", patient_id="p1", code=Coding(display="Heart failure"))
    change = MedChange(name="furosemide 40 MG", dosage="daily", change=MedChangeKind.STARTED)
    payload = build_llm_payload(notice(), [cond, cond], [change])
    assert payload.diagnoses == ("Heart failure",)
    assert payload.medications == (
        MedicationLine(name="furosemide 40 MG", dosage="daily", change=MedChangeKind.STARTED),
    )


def test_triage_context_has_no_ids_or_dates(synthea: SyntheaAdapter) -> None:
    from pathlib import Path as _P
    from zoneinfo import ZoneInfo

    from agent.deadlines import BusinessCalendar, load_holidays
    from agent.triage_rules import triage

    cal = BusinessCalendar(ZoneInfo("America/Chicago"), load_holidays(_P("library/holidays.yaml")))
    [n] = detect_for_patient(synthea, MED_CHANGES_ID)
    result = triage(
        n, synthea.get_encounters(MED_CHANGES_ID), synthea.get_conditions(MED_CHANGES_ID), cal
    )
    ctx = triage_context(result)
    assert set(TriageContext.model_fields) == TRIAGE_CONTEXT_FIELDS
    dumped = ctx.model_dump_json()
    for value in (n.patient_id, n.encounter_id, n.linked_er_encounter_id, result.idempotency_key):
        assert value not in dumped  # type: ignore[operator]
    assert not re.search(r"\d{4}-\d{2}-\d{2}", dumped)
    assert "lace" not in dumped.lower() and "moderate" not in dumped
    assert [n.id for n in ctx.notes] == ["ROUTE_PATIENT_FOLLOW_UP", "DESTINATION_UNVERIFIED"]
    assert [t.kind for t in ctx.tasks] == ["patient_contact", "follow_up_visit"]
