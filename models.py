"""Domain models shared across adapters and pipeline stages.

These are deliberately source-agnostic: adapters translate FHIR R4 (Synthea or
athenahealth) into these types, and nothing downstream sees raw FHIR.
"""

from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, field_validator

# FHIR v3-ActCode encounter classes that count as a hospital stay or ER visit.
HOSPITAL_CLASSES = frozenset({"IMP", "EMER"})


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Coding(_Frozen):
    system: str | None = None
    code: str | None = None
    display: str | None = None


class Address(_Frozen):
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


class Patient(_Frozen):
    id: str
    mrn: str | None = None
    given_names: tuple[str, ...] = ()
    family_name: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    address: Address | None = None


class Encounter(_Frozen):
    id: str
    patient_id: str
    status: str
    class_code: str
    types: tuple[Coding, ...] = ()
    reasons: tuple[Coding, ...] = ()
    period_start: datetime | None = None
    period_end: datetime | None = None
    service_provider: str | None = None

    @field_validator("period_start", "period_end")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("encounter period timestamps must include a timezone offset")
        return value

    @property
    def is_hospital_class(self) -> bool:
        return self.class_code in HOSPITAL_CLASSES

    @property
    def discharge_time(self) -> datetime | None:
        """End of the encounter period, normalized to UTC."""
        return self.period_end.astimezone(UTC) if self.period_end else None

    @property
    def idempotency_key(self) -> str | None:
        """Stable key: encounter id + discharge time (UTC, second precision).

        UTC normalization means the same instant reported with different offsets
        (e.g. Synthea's -07:00 vs an API's Z) yields the same key.
        """
        if self.discharge_time is None:
            return None
        return f"{self.id}|{self.discharge_time.strftime('%Y-%m-%dT%H:%M:%SZ')}"


class Condition(_Frozen):
    id: str
    patient_id: str
    encounter_id: str | None = None
    code: Coding
    clinical_status: str | None = None
    onset: datetime | None = None
    abatement: datetime | None = None


class MedicationRequest(_Frozen):
    id: str
    patient_id: str
    encounter_id: str | None = None
    status: str
    intent: str
    medication: Coding
    authored_on: datetime | None = None
    dosage_text: str | None = None
