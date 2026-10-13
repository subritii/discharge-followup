"""Routing and risk rules on constructed discharges."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from pydantic import ValidationError

from agent.deadlines import BusinessCalendar, load_holidays
from agent.triage_rules import DEFAULT_RULES_PATH, load_triage_rules, triage
from models import (
    CaseRoute,
    Coding,
    Condition,
    DischargeDestination,
    DischargeNotice,
    Encounter,
    RiskLevel,
    TaskKind,
)

CAL = BusinessCalendar(ZoneInfo("America/Chicago"), load_holidays(Path("library/holidays.yaml")))
CDT = timezone(timedelta(hours=-5))
DISCHARGE = datetime(2026, 10, 14, 11, 0, tzinfo=CDT)  # Wednesday
SNOMED = "http://snomed.info/sct"


def enc(id: str, cls: str, start: datetime, end: datetime) -> Encounter:
    return Encounter(
        id=id, patient_id="p", status="finished", class_code=cls, period_start=start, period_end=end
    )


def prior_eds(n: int, before: datetime = DISCHARGE) -> list[Encounter]:
    return [
        enc(
            f"ed{i}",
            "EMER",
            before - timedelta(days=20 * (i + 1)),
            before - timedelta(days=20 * (i + 1), hours=-2),
        )
        for i in range(n)
    ]


def chronic(*codes: str) -> list[Condition]:
    return [Condition(id=c, patient_id="p", code=Coding(system=SNOMED, code=c)) for c in codes]


def inpatient(
    los_days: int,
    emergent: bool = False,
    destination: DischargeDestination = DischargeDestination.HOME,
) -> tuple[DischargeNotice, list[Encounter]]:
    admit = DISCHARGE - timedelta(days=los_days)
    encounters = [enc("imp", "IMP", admit, DISCHARGE)]
    linked = None
    if emergent:
        encounters.append(enc("er", "EMER", admit - timedelta(hours=3), admit))
        linked = "er"
    notice = DischargeNotice(
        patient_id="p",
        encounter_id="imp",
        encounter_class="IMP",
        admit_time=admit,
        discharge_time=DISCHARGE,
        destination=destination,
        linked_er_encounter_id=linked,
    )
    return notice, encounters


def er_only(destination: DischargeDestination = DischargeDestination.HOME) -> DischargeNotice:
    return DischargeNotice(
        patient_id="p",
        encounter_id="er",
        encounter_class="EMER",
        admit_time=DISCHARGE - timedelta(hours=4),
        discharge_time=DISCHARGE,
        destination=destination,
    )


def rule_ids(result) -> list[str]:  # type: ignore[no-untyped-def]
    return [r.id for r in result.fired_rules]


def task(result, kind: TaskKind):  # type: ignore[no-untyped-def]
    [t] = [t for t in result.tasks if t.kind is kind]
    return t


# --- Inpatient: LACE ----------------------------------------------------------------


def test_high_risk_home_gets_contact_and_7_day_visit() -> None:
    # L7 (14d) + A3 (emergent) = 10 -> high (threshold is inclusive)
    notice, encounters = inpatient(14, emergent=True)
    result = triage(notice, encounters, [], CAL)
    assert result.pathway == "inpatient"
    assert result.risk_level is RiskLevel.HIGH
    assert result.lace is not None and result.lace.total == 10
    assert result.route is CaseRoute.PATIENT_FOLLOW_UP
    assert task(result, TaskKind.PATIENT_CONTACT).due_date == date(2026, 10, 16)  # Fri
    assert task(result, TaskKind.FOLLOW_UP_VISIT).due_date == date(2026, 10, 21)  # +7
    assert all(t.contacts_patient for t in result.tasks)
    assert result.ai_draft_allowed


def test_lace_9_is_moderate_with_14_day_visit() -> None:
    # L5 (7d) + A3 + C1 (MI) = 9
    notice, encounters = inpatient(7, emergent=True)
    result = triage(notice, encounters, chronic("22298006"), CAL)
    assert result.lace is not None and result.lace.total == 9
    assert result.risk_level is RiskLevel.MODERATE
    assert task(result, TaskKind.FOLLOW_UP_VISIT).due_date == date(2026, 10, 28)


def test_low_risk_uses_configured_visit_days() -> None:
    notice, encounters = inpatient(1)
    result = triage(notice, encounters, [], CAL)
    assert result.risk_level is RiskLevel.LOW
    assert task(result, TaskKind.FOLLOW_UP_VISIT).due_date == date(2026, 10, 28)  # low: 14


def test_lace_components_recorded_for_the_rn() -> None:
    notice, encounters = inpatient(5, emergent=True)
    history = encounters + prior_eds(2)
    result = triage(notice, history, chronic("88805009", "431857002"), CAL)
    lace = result.lace
    assert lace is not None
    assert (lace.length_of_stay_days, lace.l_points) == (5, 4)
    assert (lace.emergent, lace.a_points) == (True, 3)
    assert lace.charlson.categories == ("congestive_heart_failure", "renal_disease_moderate_severe")
    assert (lace.charlson.score, lace.c_points) == (3, 3)
    assert (lace.ed_visits_prior_6_months, lace.e_points) == (2, 2)
    assert result.prior_ed_visit_ids == ("ed0", "ed1")
    [lace_rule] = [r for r in result.fired_rules if r.id.startswith("LACE_")]
    assert lace_rule.id == "LACE_HIGH"
    assert "LACE 12 = L4 (LOS 5d) + A3" in lace_rule.detail


def test_linked_er_is_acuity_not_a_prior_ed_visit() -> None:
    notice, encounters = inpatient(3, emergent=True)
    result = triage(notice, encounters, [], CAL)
    assert result.lace is not None
    assert result.lace.ed_visits_prior_6_months == 0
    assert result.lace.a_points == 3


def test_unknown_destination_flagged_for_rn() -> None:
    notice, encounters = inpatient(3, destination=DischargeDestination.UNKNOWN)
    result = triage(notice, encounters, [], CAL)
    assert result.route is CaseRoute.PATIENT_FOLLOW_UP
    assert "DESTINATION_UNVERIFIED" in rule_ids(result)


# --- SNF and expired ----------------------------------------------------------------


def test_snf_gets_facility_coordination_only() -> None:
    notice, encounters = inpatient(14, emergent=True, destination=DischargeDestination.SNF)
    result = triage(notice, encounters, [], CAL)
    assert result.route is CaseRoute.FACILITY_COORDINATION
    assert [t.kind for t in result.tasks] == [TaskKind.FACILITY_COORDINATION]
    assert not any(t.contacts_patient for t in result.tasks)
    assert result.lace is not None and result.risk_level is RiskLevel.HIGH  # still scored
    assert "DESTINATION_UNVERIFIED" not in rule_ids(result)


@pytest.mark.parametrize("notice", [inpatient(14, emergent=True)[0], er_only()])
def test_expired_gets_close_the_loop_only(notice: DischargeNotice) -> None:
    expired = notice.model_copy(update={"destination": DischargeDestination.EXPIRED})
    result = triage(expired, [], [], CAL)
    assert result.route is CaseRoute.CLOSE_THE_LOOP
    assert result.tasks == ()
    assert result.ai_draft_allowed is False
    assert result.risk_level is None and result.lace is None
    assert rule_ids(result) == ["ROUTE_EXPIRED"]


# --- ER-only: simple rules, not LACE --------------------------------------------------


def test_er_standard() -> None:
    result = triage(er_only(), [], [], CAL)
    assert result.pathway == "er"
    assert result.lace is None
    assert result.risk_level is RiskLevel.LOW
    assert rule_ids(result)[0] == "ER_STANDARD"
    outreach = task(result, TaskKind.ER_OUTREACH)
    assert outreach.due_date == date(2026, 10, 19)  # Wed + 3 business days = Mon
    assert outreach.contacts_patient


def test_er_frequent_use_is_high() -> None:
    result = triage(er_only(), prior_eds(2), [], CAL)
    assert result.risk_level is RiskLevel.HIGH
    assert "ER_FREQUENT_USE" in rule_ids(result)
    assert result.lace is None


def test_er_comorbidity_is_high() -> None:
    result = triage(er_only(), prior_eds(1), chronic("431857002"), CAL)  # CKD4 = 2
    assert result.risk_level is RiskLevel.HIGH
    assert rule_ids(result)[:1] == ["ER_COMORBIDITY"]


def test_er_snf_goes_to_facility() -> None:
    result = triage(er_only(DischargeDestination.SNF), [], [], CAL)
    assert result.route is CaseRoute.FACILITY_COORDINATION


def test_er_outreach_deadline_configurable(tmp_path: Path) -> None:
    data = yaml.safe_load(DEFAULT_RULES_PATH.read_text())
    data["deadlines"]["er_outreach_business_days"] = 1
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data))
    result = triage(er_only(), [], [], CAL, rules=load_triage_rules(path))
    assert task(result, TaskKind.ER_OUTREACH).due_date == date(2026, 10, 15)


# --- Rules file -----------------------------------------------------------------------


def test_rules_labelled_illustrative() -> None:
    rules = load_triage_rules()
    assert rules.illustrative is True
    assert "pending clinical review" in rules.review_status
    assert triage(er_only(), [], [], CAL).disclaimer.startswith("Illustrative")


def test_expired_route_cannot_be_configured(tmp_path: Path) -> None:
    data = yaml.safe_load(DEFAULT_RULES_PATH.read_text())
    data["routes"]["inpatient"]["expired"] = "patient_follow_up"
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError, match="must map exactly"):
        load_triage_rules(path)


def test_missing_route_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(DEFAULT_RULES_PATH.read_text())
    del data["routes"]["er"]["unknown"]
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError):
        load_triage_rules(path)


def test_holiday_calendar_gap_is_flagged() -> None:
    no_coverage = BusinessCalendar(ZoneInfo("America/Chicago"))
    result = triage(er_only(), [], [], no_coverage)
    assert "HOLIDAY_CALENDAR_MISSING" in rule_ids(result)
    assert "HOLIDAY_CALENDAR_MISSING" not in rule_ids(triage(er_only(), [], [], CAL))
