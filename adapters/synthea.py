"""Reads Synthea FHIR R4 patient bundles (one JSON file per patient) from a directory."""

import json
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from pathlib import Path

from adapters.base import NotFoundError
from adapters.fhir_parse import (
    parse_condition,
    parse_encounter,
    parse_medication_request,
    parse_patient,
)
from models import Condition, Encounter, MedicationRequest, Patient


@dataclass(frozen=True)
class _PatientRecord:
    patient: Patient
    encounters: tuple[Encounter, ...]
    conditions: tuple[Condition, ...]
    medication_requests: tuple[MedicationRequest, ...]


def _load_bundle(path: Path) -> _PatientRecord:
    bundle = json.loads(path.read_text())
    resources = [entry["resource"] for entry in bundle.get("entry", [])]
    by_type: dict[str, list[dict]] = {}
    for resource in resources:
        by_type.setdefault(resource["resourceType"], []).append(resource)

    patients = by_type.get("Patient", [])
    if len(patients) != 1:
        raise ValueError(f"{path.name}: expected exactly 1 Patient, found {len(patients)}")
    medications = {m["id"]: m for m in by_type.get("Medication", [])}

    return _PatientRecord(
        patient=parse_patient(patients[0]),
        encounters=tuple(parse_encounter(r) for r in by_type.get("Encounter", [])),
        conditions=tuple(parse_condition(r) for r in by_type.get("Condition", [])),
        medication_requests=tuple(
            parse_medication_request(r, medications) for r in by_type.get("MedicationRequest", [])
        ),
    )


class SyntheaAdapter:
    """Loads every bundle in `data_dir` on first access and serves lookups from memory.

    Fine for fixtures and a few hundred synthetic patients; not meant for large exports.
    """

    def __init__(self, data_dir: Path) -> None:
        if not data_dir.is_dir():
            raise FileNotFoundError(f"Synthea data directory not found: {data_dir}")
        self._data_dir = data_dir

    @cached_property
    def _records(self) -> dict[str, _PatientRecord]:
        records: dict[str, _PatientRecord] = {}
        for path in sorted(self._data_dir.glob("*.json")):
            record = _load_bundle(path)
            if record.patient.id in records:
                raise ValueError(f"duplicate patient id {record.patient.id} in {path.name}")
            records[record.patient.id] = record
        return records

    @cached_property
    def _encounters_by_id(self) -> dict[str, Encounter]:
        return {e.id: e for record in self._records.values() for e in record.encounters}

    def _record(self, patient_id: str) -> _PatientRecord:
        try:
            return self._records[patient_id]
        except KeyError:
            raise NotFoundError(f"Patient {patient_id}") from None

    def patient_ids(self) -> list[str]:
        """All patients in the directory (Synthea only; FHIR servers don't list patients)."""
        return list(self._records)

    def get_patient(self, patient_id: str) -> Patient:
        return self._record(patient_id).patient

    def search_patients(
        self,
        *,
        mrn: str | None = None,
        family_name: str | None = None,
        birth_date: date | None = None,
    ) -> list[Patient]:
        if mrn is None and family_name is None and birth_date is None:
            raise ValueError("search_patients needs at least one criterion")
        return [
            p
            for p in (r.patient for r in self._records.values())
            if (mrn is None or p.mrn == mrn)
            and (family_name is None or (p.family_name or "").lower() == family_name.lower())
            and (birth_date is None or p.birth_date == birth_date)
        ]

    def get_encounter(self, encounter_id: str) -> Encounter:
        try:
            return self._encounters_by_id[encounter_id]
        except KeyError:
            raise NotFoundError(f"Encounter {encounter_id}") from None

    def get_encounters(self, patient_id: str) -> list[Encounter]:
        return list(self._record(patient_id).encounters)

    def get_medication_requests(self, patient_id: str) -> list[MedicationRequest]:
        return list(self._record(patient_id).medication_requests)

    def get_conditions(self, patient_id: str) -> list[Condition]:
        return list(self._record(patient_id).conditions)
