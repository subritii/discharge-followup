"""Build the full Phase 2 -> 6 input for one fixture patient (shared by tests)."""

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from adapters.synthea import SyntheaAdapter
from agent.deadlines import BusinessCalendar, load_holidays
from agent.med_diff import med_diff
from agent.triage_rules import triage
from detector.discharge import detect_for_patient
from models import DischargeNotice, Patient, TriageResult
from privacy.guard import KnownIdentifiers
from privacy.payload import LLMPayload
from privacy.prepare import prepare_llm_payload
from privacy.redactor import Redactor

CALENDAR = BusinessCalendar(
    ZoneInfo("America/Chicago"), load_holidays(Path("library/holidays.yaml"))
)


@dataclass(frozen=True)
class Episode:
    notice: DischargeNotice
    patient: Patient
    triage: TriageResult
    payload: LLMPayload
    known: KnownIdentifiers


def build_episode(
    adapter: SyntheaAdapter, patient_id: str, redactor: Redactor, instructions: str | None = None
) -> Episode:
    [notice] = detect_for_patient(adapter, patient_id)
    patient = adapter.get_patient(patient_id)
    encounters = adapter.get_encounters(patient_id)
    conditions = adapter.get_conditions(patient_id)
    episode_ids = {notice.encounter_id, notice.linked_er_encounter_id}
    payload = prepare_llm_payload(
        notice,
        patient,
        [c for c in conditions if c.encounter_id in episode_ids],
        med_diff(notice, adapter.get_medication_requests(patient_id), encounters),
        redactor,
        instructions=instructions,
    )
    return Episode(
        notice=notice,
        patient=patient,
        triage=triage(notice, encounters, conditions, CALENDAR),
        payload=payload,
        known=KnownIdentifiers.from_patient(patient, notice),
    )
