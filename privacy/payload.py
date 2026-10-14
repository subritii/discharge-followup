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

from pydantic import BaseModel, ConfigDict, Field

from models import Condition, DischargeDestination, DischargeNotice, MedicationRequest


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MedicationLine(_Strict):
    name: str
    dosage: str | None = None  # free text


class LLMPayload(_Strict):
    encounter_class: Literal["IMP", "EMER"]
    destination: DischargeDestination
    length_of_stay_days: float = Field(ge=0)
    diagnoses: tuple[str, ...] = ()
    medications: tuple[MedicationLine, ...] = ()
    instructions: str | None = None  # free text


# Reviewed allowlist. Changing LLMPayload must also change this (see test_payload.py).
ALLOWED_FIELDS = frozenset(
    {
        "encounter_class",
        "destination",
        "length_of_stay_days",
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
    medications: Iterable[MedicationRequest],
    instructions: str | None = None,
) -> LLMPayload:
    """Select clinical fields only. Callers choose which conditions/meds are relevant."""
    if not notice.requires_patient_follow_up:
        reason = "deceased date unknown" if notice.deceased_date_unknown else "expired"
        raise NoAIDraftError(f"no AI draft: {reason}")
    stay = notice.discharge_time - notice.admit_time
    meds = {
        (m.medication.display, m.dosage_text): MedicationLine(
            name=m.medication.display, dosage=m.dosage_text
        )
        for m in medications
        if m.medication.display
    }
    return LLMPayload(
        encounter_class=notice.encounter_class,  # type: ignore[arg-type]
        destination=notice.destination,
        length_of_stay_days=round(max(stay.total_seconds(), 0) / 86400, 1),
        diagnoses=_unique(c.code.display for c in diagnoses),
        medications=tuple(meds.values()),
        instructions=instructions,
    )
