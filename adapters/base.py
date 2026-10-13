"""The one data-adapter interface. SyntheaAdapter and AthenaAdapter both implement it."""

from datetime import date
from typing import Protocol, runtime_checkable

from models import Condition, Encounter, MedicationRequest, Patient


class NotFoundError(LookupError):
    """Requested resource does not exist in the data source."""


@runtime_checkable
class PatientDataAdapter(Protocol):
    def get_patient(self, patient_id: str) -> Patient: ...

    def search_patients(
        self,
        *,
        mrn: str | None = None,
        family_name: str | None = None,
        birth_date: date | None = None,
    ) -> list[Patient]: ...

    def get_encounter(self, encounter_id: str) -> Encounter: ...

    def get_encounters(self, patient_id: str) -> list[Encounter]: ...

    def get_medication_requests(self, patient_id: str) -> list[MedicationRequest]: ...

    def get_conditions(self, patient_id: str) -> list[Condition]: ...
