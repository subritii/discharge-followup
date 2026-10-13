"""Triage on the real (trimmed) Synthea fixtures."""

from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from adapters.synthea import SyntheaAdapter
from agent.deadlines import BusinessCalendar, load_holidays
from agent.triage_rules import triage
from detector.discharge import detect_for_patient
from models import CaseRoute, RiskLevel, TaskKind, TriageResult
from tests.conftest import (
    ER_ID,
    HIGH_RISK_ER_2026_APR,
    HIGH_RISK_ER_2026_MAY,
    HIGH_RISK_ID,
    HIGH_RISK_IMP_ENCOUNTER,
    HIGH_RISK_INDEX_ER,
    HIGH_RISK_PRIOR_ER,
    INPATIENT_ID,
    MED_CHANGES_ID,
)

CAL = BusinessCalendar(ZoneInfo("America/Chicago"), load_holidays(Path("library/holidays.yaml")))


def triage_all(adapter: SyntheaAdapter, patient_id: str) -> list[TriageResult]:
    encounters, conditions = adapter.get_encounters(patient_id), adapter.get_conditions(patient_id)
    return [triage(n, encounters, conditions, CAL) for n in detect_for_patient(adapter, patient_id)]


def due(result: TriageResult, kind: TaskKind) -> date:
    [t] = [t for t in result.tasks if t.kind is kind]
    return t.due_date


@pytest.fixture(scope="module")
def high_risk(synthea: SyntheaAdapter) -> list[TriageResult]:
    return triage_all(synthea, HIGH_RISK_ID)


def test_real_high_risk_stay(high_risk: list[TriageResult]) -> None:
    stay = next(r for r in high_risk if r.encounter_id == HIGH_RISK_IMP_ENCOUNTER)
    assert stay.lace is not None
    assert stay.lace.total == 11
    assert (stay.lace.l_points, stay.lace.a_points, stay.lace.c_points, stay.lace.e_points) == (
        7,
        3,
        0,
        1,
    )
    assert stay.lace.length_of_stay_days == 15
    assert stay.prior_ed_visit_ids == (HIGH_RISK_PRIOR_ER,)  # Aug 2023, not the index ER
    assert HIGH_RISK_INDEX_ER not in stay.prior_ed_visit_ids
    assert stay.risk_level is RiskLevel.HIGH
    # Discharged Sun 2023-12-17: contact Tue; visit day 7 = Sun 12-24 -> Fri 12-22.
    assert stay.discharge_local_date == date(2023, 12, 17)
    assert due(stay, TaskKind.PATIENT_CONTACT) == date(2023, 12, 19)
    assert due(stay, TaskKind.FOLLOW_UP_VISIT) == date(2023, 12, 22)
    # Holidays file starts in 2025, so the RN is told the calendar has a gap.
    assert "HOLIDAY_CALENDAR_MISSING" in [r.id for r in stay.fired_rules]


def test_real_er_visit_counts_only_window(high_risk: list[TriageResult]) -> None:
    may = next(r for r in high_risk if r.encounter_id == HIGH_RISK_ER_2026_MAY)
    assert may.pathway == "er"
    assert may.prior_ed_visit_ids == (HIGH_RISK_ER_2026_APR,)  # the 2023 visits are too old
    assert may.risk_level is RiskLevel.LOW  # 1 prior visit < threshold 2
    assert may.discharge_local_date == date(2026, 5, 10)  # 03:29 MDT = Sun 04:29 CDT
    assert due(may, TaskKind.ER_OUTREACH) == date(2026, 5, 13)


def test_er_admit_with_memorial_day(synthea: SyntheaAdapter) -> None:
    [result] = triage_all(synthea, MED_CHANGES_ID)
    assert result.lace is not None
    assert result.lace.total == 5  # L1 + A3 (via ER) + C1 (STEMI) + E0
    assert result.lace.charlson.categories == ("myocardial_infarction",)
    assert result.risk_level is RiskLevel.MODERATE
    # Discharged Thu 2025-05-22; Mon 05-26 is Memorial Day -> Fri=1, Tue=2.
    assert due(result, TaskKind.PATIENT_CONTACT) == date(2025, 5, 27)
    assert due(result, TaskKind.FOLLOW_UP_VISIT) == date(2025, 6, 5)


def test_long_stay_unknown_destination(synthea: SyntheaAdapter) -> None:
    [result] = triage_all(synthea, INPATIENT_ID)
    assert result.lace is not None
    assert result.lace.total == 7  # L7 (23 days), not emergent, no comorbidities
    assert result.route is CaseRoute.PATIENT_FOLLOW_UP
    assert "DESTINATION_UNVERIFIED" in [r.id for r in result.fired_rules]


def test_er_only_visit(synthea: SyntheaAdapter) -> None:
    [result] = triage_all(synthea, ER_ID)
    assert result.pathway == "er" and result.lace is None
    assert result.route is CaseRoute.ER_OUTREACH
    assert due(result, TaskKind.ER_OUTREACH) == date(2025, 7, 9)  # Sat -> Mon, Tue, Wed
