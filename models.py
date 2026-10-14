"""Domain models shared across adapters and pipeline stages.

These are deliberately source-agnostic: adapters translate FHIR R4 (Synthea or
athenahealth) into these types, and nothing downstream sees raw FHIR.
"""

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

# FHIR v3-ActCode encounter classes that count as a hospital stay or ER visit.
HOSPITAL_CLASSES = frozenset({"IMP", "EMER"})


def idempotency_key(encounter_id: str, discharge_time: datetime) -> str:
    """Encounter id + discharge time (UTC, second precision)."""
    return f"{encounter_id}|{discharge_time.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Coding(_Frozen):
    system: str | None = None
    code: str | None = None
    display: str | None = None


class Address(_Frozen):
    lines: tuple[str, ...] = ()
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
    phones: tuple[str, ...] = ()
    deceased: bool = False
    deceased_at: datetime | None = None


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
    discharge_disposition: Coding | None = None

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
        return idempotency_key(self.id, self.discharge_time)


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


class MedChangeKind(StrEnum):
    STARTED = "started"  # ordered during the episode and still active
    CONTINUED = "continued"  # active before the episode, not re-ordered during it
    INPATIENT_ONLY = "inpatient_only"  # ordered during the episode, not continued


class MedChange(_Frozen):
    name: str
    dosage: str | None = None
    change: MedChangeKind


class DischargeDestination(StrEnum):
    HOME = "home"
    SNF = "snf"
    OTHER = "other"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class DischargeNotice(_Frozen):
    """One discharge episode. An ER visit that led to admission is folded into the IMP stay."""

    patient_id: str
    encounter_id: str  # the IMP encounter, or the ER encounter for a standalone ER visit
    encounter_class: str
    admit_time: datetime
    discharge_time: datetime
    destination: DischargeDestination = DischargeDestination.UNKNOWN
    linked_er_encounter_id: str | None = None
    reasons: tuple[Coding, ...] = ()
    # Patient flagged deceased (deceasedBoolean) with no date: we can't tell whether the
    # death relates to this stay, so a human confirms before anyone is contacted.
    deceased_date_unknown: bool = False

    @property
    def idempotency_key(self) -> str:
        return idempotency_key(self.encounter_id, self.discharge_time)

    @property
    def requires_patient_follow_up(self) -> bool:
        """False when no patient-call tasks or AI drafts may be created.

        Expired -> informational close-the-loop case. Deceased with unknown date ->
        single RN review task to confirm status first.
        """
        return self.destination is not DischargeDestination.EXPIRED and not (
            self.deceased_date_unknown
        )


# --- Triage (Phase 5) ----------------------------------------------------------------


class RiskLevel(StrEnum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


class CaseRoute(StrEnum):
    PATIENT_FOLLOW_UP = "patient_follow_up"  # inpatient -> home (or unverified)
    FACILITY_COORDINATION = "facility_coordination"  # SNF: coordinate with facility
    CLOSE_THE_LOOP = "close_the_loop"  # expired: informational, care team lead
    ER_OUTREACH = "er_outreach"  # ER-only visit
    DECEASED_STATUS_REVIEW = "deceased_status_review"  # deceased flag, no date: RN confirms


class TaskKind(StrEnum):
    PATIENT_CONTACT = "patient_contact"
    FOLLOW_UP_VISIT = "follow_up_visit"
    FACILITY_COORDINATION = "facility_coordination"
    ER_OUTREACH = "er_outreach"
    RN_REVIEW = "rn_review"


class FiredRule(_Frozen):
    """One explicit rule that fired, so the RN can see why."""

    id: str
    detail: str


class ReviewFlag(_Frozen):
    """Something the RN must check by hand (rules, not AI)."""

    id: str
    detail: str


class CharlsonResult(_Frozen):
    score: int
    categories: tuple[str, ...]  # after hierarchy (e.g. complicated diabetes supersedes)


class LaceScore(_Frozen):
    length_of_stay_days: int
    l_points: int
    emergent: bool
    a_points: int
    charlson: CharlsonResult
    c_points: int
    ed_visits_prior_6_months: int
    e_points: int

    @property
    def total(self) -> int:
        return self.l_points + self.a_points + self.c_points + self.e_points


class TaskSpec(_Frozen):
    kind: TaskKind
    due_date: date  # in the practice's local time zone
    basis: str  # how the due date was computed, for the RN
    contacts_patient: bool


class TriageResult(_Frozen):
    patient_id: str
    encounter_id: str
    idempotency_key: str
    pathway: str  # "inpatient" | "er"
    route: CaseRoute
    risk_level: RiskLevel | None  # None when not scored (expired)
    lace: LaceScore | None = None
    charlson: CharlsonResult | None = None
    prior_ed_visit_ids: tuple[str, ...] = ()
    fired_rules: tuple[FiredRule, ...] = ()
    review_flags: tuple[ReviewFlag, ...] = ()
    tasks: tuple[TaskSpec, ...] = ()
    discharge_local_date: date
    timezone: str
    ai_draft_allowed: bool
    disclaimer: str = "Illustrative rules (LACE / Charlson) pending clinical review."
