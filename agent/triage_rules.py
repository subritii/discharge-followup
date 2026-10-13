"""Explicit triage and routing rules (ILLUSTRATIVE, pending clinical review).

Rules decide risk, route and deadlines. The LLM only explains them later (Phase 6).
Every decision is recorded as a FiredRule, so the RN can see why.

Pathways:
  inpatient (IMP, incl. ER->IMP merged): LACE index -> high / moderate / low
  er (standalone EMER visit):            simple rules -> high / low, never LACE

Hard rule (not configurable): expired -> close_the_loop, no tasks, no AI draft.
"""

from collections.abc import Iterable
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from agent.charlson import CharlsonMap, charlson_for_stay
from agent.deadlines import BusinessCalendar
from agent.lace import compute_lace, prior_ed_visits
from models import (
    CaseRoute,
    Condition,
    DischargeDestination,
    DischargeNotice,
    Encounter,
    FiredRule,
    RiskLevel,
    TaskKind,
    TaskSpec,
    TriageResult,
)

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "library" / "triage_rules.yaml"

_ROUTABLE = {"home", "unknown", "other", "snf"}  # "expired" is handled in code


class _Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LaceRules(_Config):
    high_risk_min: int
    moderate_risk_min: int
    ed_lookback_days: int


class ErRules(_Config):
    frequent_ed_visits_min: int
    charlson_min: int
    ed_lookback_days: int


class Deadlines(_Config):
    patient_contact_business_days: int
    follow_up_visit_calendar_days: dict[RiskLevel, int]
    facility_coordination_business_days: int
    er_outreach_business_days: int


class TriageRules(_Config):
    illustrative: bool
    review_status: str
    lace: LaceRules
    er: ErRules
    routes: dict[str, dict[str, CaseRoute]]
    deadlines: Deadlines

    def route_for(self, pathway: str, destination: DischargeDestination) -> CaseRoute:
        return self.routes[pathway][destination.value]

    @model_validator(mode="after")
    def _complete(self) -> "TriageRules":
        for pathway in ("inpatient", "er"):
            if set(self.routes.get(pathway, {})) != _ROUTABLE:
                raise ValueError(f"routes.{pathway} must map exactly {sorted(_ROUTABLE)}")
        if set(self.deadlines.follow_up_visit_calendar_days) != set(RiskLevel):
            raise ValueError("follow_up_visit_calendar_days needs high, moderate and low")
        return self


def load_triage_rules(path: Path = DEFAULT_RULES_PATH) -> TriageRules:
    return TriageRules.model_validate(yaml.safe_load(path.read_text()))


@lru_cache
def default_triage_rules() -> TriageRules:
    return load_triage_rules()


def _lace_risk(total: int, rules: LaceRules) -> RiskLevel:
    if total >= rules.high_risk_min:
        return RiskLevel.HIGH
    if total >= rules.moderate_risk_min:
        return RiskLevel.MODERATE
    return RiskLevel.LOW


def _tasks_for(
    route: CaseRoute,
    risk: RiskLevel,
    discharge_day: date,
    calendar: BusinessCalendar,
    deadlines: Deadlines,
) -> tuple[TaskSpec, ...]:
    tz = calendar.timezone.key

    def business(kind: TaskKind, days: int, contacts_patient: bool) -> TaskSpec:
        return TaskSpec(
            kind=kind,
            due_date=calendar.add_business_days(discharge_day, days),
            basis=f"{days} business days after discharge ({tz}, weekends/holidays skipped)",
            contacts_patient=contacts_patient,
        )

    if route is CaseRoute.PATIENT_FOLLOW_UP:
        visit_days = deadlines.follow_up_visit_calendar_days[risk]
        return (
            business(TaskKind.PATIENT_CONTACT, deadlines.patient_contact_business_days, True),
            TaskSpec(
                kind=TaskKind.FOLLOW_UP_VISIT,
                due_date=calendar.within_calendar_days(discharge_day, visit_days),
                basis=(
                    f"within {visit_days} calendar days ({risk.value} risk); moved earlier "
                    "if day falls on a weekend/holiday"
                ),
                contacts_patient=True,
            ),
        )
    if route is CaseRoute.FACILITY_COORDINATION:
        days = deadlines.facility_coordination_business_days
        return (business(TaskKind.FACILITY_COORDINATION, days, False),)
    if route is CaseRoute.ER_OUTREACH:
        return (business(TaskKind.ER_OUTREACH, deadlines.er_outreach_business_days, True),)
    return ()  # CLOSE_THE_LOOP: informational case only


def triage(
    notice: DischargeNotice,
    encounters: Iterable[Encounter],
    conditions: Iterable[Condition],
    calendar: BusinessCalendar,
    rules: TriageRules | None = None,
    charlson_map: CharlsonMap | None = None,
) -> TriageResult:
    """Apply the explicit rules to one discharge. `encounters`/`conditions` = patient history."""
    rules = rules or default_triage_rules()
    encounters, conditions = list(encounters), list(conditions)
    pathway = "inpatient" if notice.encounter_class == "IMP" else "er"
    discharge_day = calendar.local_date(notice.discharge_time)
    base = {
        "patient_id": notice.patient_id,
        "encounter_id": notice.encounter_id,
        "idempotency_key": notice.idempotency_key,
        "pathway": pathway,
        "discharge_local_date": discharge_day,
        "timezone": calendar.timezone.key,
    }

    if notice.destination is DischargeDestination.EXPIRED:
        return TriageResult(
            **base,
            route=CaseRoute.CLOSE_THE_LOOP,
            risk_level=None,
            fired_rules=(
                FiredRule(
                    id="ROUTE_EXPIRED",
                    detail="Patient expired: informational close-the-loop case for the care "
                    "team lead; no patient contact, no AI draft.",
                ),
            ),
            ai_draft_allowed=False,
        )

    fired: list[FiredRule] = []
    # The episode starts at ER arrival when the admission came through the ER.
    linked_er = next((e for e in encounters if e.id == notice.linked_er_encounter_id), None)
    episode_start = (linked_er.period_start if linked_er else None) or notice.admit_time
    charlson = charlson_for_stay(conditions, episode_start, notice.discharge_time, charlson_map)
    lookback = timedelta(
        days=rules.lace.ed_lookback_days if pathway == "inpatient" else rules.er.ed_lookback_days
    )
    prior_ed = prior_ed_visits(
        encounters,
        before=episode_start,
        lookback=lookback,
        exclude_ids=(notice.encounter_id, notice.linked_er_encounter_id),
    )

    lace = None
    if pathway == "inpatient":
        los = (discharge_day - calendar.local_date(notice.admit_time)).days
        lace = compute_lace(los, linked_er is not None, charlson, len(prior_ed))
        risk = _lace_risk(lace.total, rules.lace)
        fired.append(
            FiredRule(
                id=f"LACE_{risk.value.upper()}",
                detail=(
                    f"LACE {lace.total} = L{lace.l_points} (LOS {los}d) + A{lace.a_points} "
                    f"({'emergent via ER' if lace.emergent else 'no linked ER visit'}) + "
                    f"C{lace.c_points} (Charlson {charlson.score}) + E{lace.e_points} "
                    f"({len(prior_ed)} ED visits in prior {lookback.days}d); "
                    f"high >= {rules.lace.high_risk_min}, moderate >= "
                    f"{rules.lace.moderate_risk_min}"
                ),
            )
        )
    else:
        risk = RiskLevel.LOW
        if len(prior_ed) >= rules.er.frequent_ed_visits_min:
            risk = RiskLevel.HIGH
            fired.append(
                FiredRule(
                    id="ER_FREQUENT_USE",
                    detail=f"{len(prior_ed)} prior ED visits in {lookback.days}d "
                    f"(threshold {rules.er.frequent_ed_visits_min})",
                )
            )
        if charlson.score >= rules.er.charlson_min:
            risk = RiskLevel.HIGH
            fired.append(
                FiredRule(
                    id="ER_COMORBIDITY",
                    detail=f"Charlson {charlson.score} ({', '.join(charlson.categories)}) "
                    f"(threshold {rules.er.charlson_min})",
                )
            )
        if risk is RiskLevel.LOW:
            fired.append(FiredRule(id="ER_STANDARD", detail="No ER risk rule fired"))

    route = rules.route_for(pathway, notice.destination)
    fired.append(
        FiredRule(
            id=f"ROUTE_{route.value.upper()}",
            detail=f"{pathway} discharge, destination {notice.destination.value}",
        )
    )
    if notice.destination in (DischargeDestination.UNKNOWN, DischargeDestination.OTHER):
        fired.append(
            FiredRule(
                id="DESTINATION_UNVERIFIED",
                detail=f"Discharge destination is '{notice.destination.value}'; RN to confirm "
                "before contacting the patient.",
            )
        )

    tasks = _tasks_for(route, risk, discharge_day, calendar, rules.deadlines)
    uncovered = sorted(
        {d.year for d in (discharge_day, *(t.due_date for t in tasks)) if not calendar.covers(d)}
    )
    if uncovered:
        fired.append(
            FiredRule(
                id="HOLIDAY_CALENDAR_MISSING",
                detail=f"No practice holidays configured for {uncovered}; due dates skip "
                "weekends only. Update the holidays file.",
            )
        )

    return TriageResult(
        **base,
        route=route,
        risk_level=risk,
        lace=lace,
        charlson=charlson,
        prior_ed_visit_ids=tuple(e.id for e in prior_ed),
        fired_rules=tuple(fired),
        tasks=tasks,
        ai_draft_allowed=True,
    )
