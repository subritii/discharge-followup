"""Data minimization: the ONLY shape of data allowed to reach an LLM.

LLMPayload is the allowlist. It forbids extra fields, and `build_llm_payload`
never receives a Patient, so names, DOB, address, phone, MRN, and record ids
can't enter the LLM path through structured fields. Dates are reduced to a
length of stay, since calendar dates are identifiers under HIPAA Safe Harbor.

Free-text fields (`instructions`, `MedicationLine.dosage`) still need redaction
(privacy/redactor.py), and the whole payload is checked by privacy/guard.py.
"""

import re
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
        encounter_class=notice.encounter_class,  # type: ignore[arg-type]
        destination=notice.destination,
        length_of_stay_days=round(max(stay.total_seconds(), 0) / 86400, 1),
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


class TaskLine(_Strict):
    kind: str
    basis: str  # e.g. "2 business days after discharge" -- relative, never a date
    contacts_patient: bool


class TriageContext(_Strict):
    """The parts of a TriageResult the LLM may see: route, tasks, and non-risk notes.

    No ids, no calendar dates, and no risk tier / LACE / Charlson: the risk explanation
    is built deterministically (agent/risk_explanation.py), so the model has nothing to
    misstate.
    """

    route: CaseRoute
    notes: tuple[RuleLine, ...] = ()  # e.g. DESTINATION_UNVERIFIED
    tasks: tuple[TaskLine, ...] = ()


TRIAGE_CONTEXT_FIELDS = frozenset({"route", "notes", "tasks"})

# Rules that explain risk; kept out of the LLM input.
RISK_RULE_PREFIXES = ("LACE_", "ER_")
# Task wording like "within 14 calendar days (moderate risk)" would leak the tier.
_RISK_IN_TEXT = re.compile(r"\s*\((?:high|moderate|low) risk\)", re.IGNORECASE)


def triage_context(result: TriageResult) -> TriageContext:
    return TriageContext(
        route=result.route,
        notes=tuple(
            RuleLine(id=r.id, detail=r.detail)
            for r in result.fired_rules
            if not r.id.startswith(RISK_RULE_PREFIXES)
        ),
        tasks=tuple(
            TaskLine(
                kind=t.kind.value,
                basis=_RISK_IN_TEXT.sub("", t.basis),
                contacts_patient=t.contacts_patient,
            )
            for t in result.tasks
        ),
    )
