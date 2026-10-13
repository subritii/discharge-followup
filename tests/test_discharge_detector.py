from datetime import UTC, datetime, timedelta

import pytest

from detector.discharge import (
    DISPOSITION_SYSTEM,
    destination_from,
    detect_discharges,
    is_discharge,
)
from models import Coding, DischargeDestination, Encounter

T0 = datetime(2025, 5, 21, 12, 0, tzinfo=UTC)


def enc(
    id: str,
    class_code: str,
    start: datetime,
    end: datetime | None,
    status: str = "finished",
    disposition: str | None = None,
) -> Encounter:
    return Encounter(
        id=id,
        patient_id="p1",
        status=status,
        class_code=class_code,
        period_start=start,
        period_end=end,
        discharge_disposition=(
            Coding(system=DISPOSITION_SYSTEM, code=disposition) if disposition else None
        ),
    )


def er_visit(start: datetime = T0, hours: float = 1) -> Encounter:
    return enc("er", "EMER", start, start + timedelta(hours=hours))


def admission(start: datetime, days: float = 2, **kw: object) -> Encounter:
    return enc("imp", "IMP", start, start + timedelta(days=days), **kw)  # type: ignore[arg-type]


# --- Detection rule: class + status + period.end ---------------------------------


@pytest.mark.parametrize(
    ("encounter", "expected"),
    [
        (enc("a", "IMP", T0, T0 + timedelta(days=1)), True),
        (enc("b", "EMER", T0, T0 + timedelta(hours=1)), True),
        (enc("c", "AMB", T0, T0 + timedelta(hours=1)), False),
        (enc("d", "HH", T0, T0 + timedelta(hours=1)), False),
        (enc("e", "IMP", T0, None, status="in-progress"), False),
        (enc("f", "IMP", T0, None, status="finished"), False),  # finished but no end
        (enc("g", "IMP", T0, T0 + timedelta(days=1), status="cancelled"), False),
        (enc("h", "IMP", T0, T0 + timedelta(days=1), status="entered-in-error"), False),
    ],
    ids=lambda v: v.id if isinstance(v, Encounter) else str(v),
)
def test_is_discharge(encounter: Encounter, expected: bool) -> None:
    assert is_discharge(encounter) is expected


def test_detection_does_not_need_disposition() -> None:
    [notice] = detect_discharges([admission(T0)])
    assert notice.destination == DischargeDestination.UNKNOWN


# --- ER -> IMP merge ---------------------------------------------------------------


def test_merge_when_overlapping() -> None:
    er = er_visit(T0, hours=1)
    imp = admission(T0 + timedelta(minutes=25))  # starts while ER still open
    [notice] = detect_discharges([er, imp])
    assert notice.encounter_id == "imp"
    assert notice.encounter_class == "IMP"
    assert notice.linked_er_encounter_id == "er"
    assert notice.discharge_time == imp.discharge_time
    assert notice.idempotency_key == imp.idempotency_key


def test_merge_when_gap_within_window() -> None:
    er = er_visit(T0, hours=1)
    imp = admission(T0 + timedelta(hours=1 + 5))  # 5h after ER ends, window 6h
    [notice] = detect_discharges([er, imp], merge_window=timedelta(hours=6))
    assert notice.encounter_id == "imp"
    assert notice.linked_er_encounter_id == "er"


def test_merge_at_exact_window_boundary() -> None:
    er = er_visit(T0, hours=1)
    imp = admission(T0 + timedelta(hours=1 + 6))
    [notice] = detect_discharges([er, imp], merge_window=timedelta(hours=6))
    assert notice.linked_er_encounter_id == "er"


def test_no_merge_when_gap_outside_window() -> None:
    er = er_visit(T0, hours=1)
    imp = admission(T0 + timedelta(hours=1 + 7))
    notices = detect_discharges([er, imp], merge_window=timedelta(hours=6))
    assert [(n.encounter_id, n.linked_er_encounter_id) for n in notices] == [
        ("er", None),
        ("imp", None),
    ]
    assert notices[0].idempotency_key != notices[1].idempotency_key


def test_window_is_configurable() -> None:
    er = er_visit(T0, hours=1)
    imp = admission(T0 + timedelta(hours=1 + 5))
    assert len(detect_discharges([er, imp], merge_window=timedelta(hours=6))) == 1
    assert len(detect_discharges([er, imp], merge_window=timedelta(hours=1))) == 2


def test_er_only_stays_separate() -> None:
    [notice] = detect_discharges([er_visit()])
    assert notice.encounter_id == "er"
    assert notice.encounter_class == "EMER"
    assert notice.linked_er_encounter_id is None


def test_imp_before_er_is_not_merged() -> None:
    imp = enc("imp", "IMP", T0, T0 + timedelta(days=2))
    er = enc("er", "EMER", T0 + timedelta(days=3), T0 + timedelta(days=3, hours=1))
    assert len(detect_discharges([imp, er])) == 2


def test_each_er_links_to_one_admission_only() -> None:
    er = er_visit(T0, hours=1)
    imp1 = enc("imp1", "IMP", T0 + timedelta(minutes=30), T0 + timedelta(hours=3))
    imp2 = enc("imp2", "IMP", T0 + timedelta(hours=4), T0 + timedelta(days=2))
    notices = detect_discharges([er, imp1, imp2])
    assert [(n.encounter_id, n.linked_er_encounter_id) for n in notices] == [
        ("imp1", "er"),
        ("imp2", None),
    ]


def test_closest_preceding_er_is_linked() -> None:
    er1 = enc("er1", "EMER", T0, T0 + timedelta(hours=1))
    er2 = enc("er2", "EMER", T0 + timedelta(hours=2), T0 + timedelta(hours=3))
    imp = admission(T0 + timedelta(hours=4))
    notices = detect_discharges([er1, er2, imp])
    assert [(n.encounter_id, n.linked_er_encounter_id) for n in notices] == [
        ("er1", None),
        ("imp", "er2"),
    ]


def test_unfinished_er_does_not_merge() -> None:
    er = enc("er", "EMER", T0, None, status="in-progress")
    [notice] = detect_discharges([er, admission(T0 + timedelta(minutes=30))])
    assert notice.linked_er_encounter_id is None


def test_notices_sorted_by_discharge_time() -> None:
    late = enc("late", "EMER", T0 + timedelta(days=10), T0 + timedelta(days=10, hours=1))
    early = enc("early", "EMER", T0, T0 + timedelta(hours=1))
    assert [n.encounter_id for n in detect_discharges([late, early])] == ["early", "late"]


# --- Destination -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("home", DischargeDestination.HOME),
        ("alt-home", DischargeDestination.HOME),
        ("snf", DischargeDestination.SNF),
        ("rehab", DischargeDestination.OTHER),
        ("hosp", DischargeDestination.OTHER),
        ("oth", DischargeDestination.OTHER),
        ("made-up", DischargeDestination.UNKNOWN),
    ],
)
def test_destination_from_hl7_code(code: str, expected: DischargeDestination) -> None:
    assert destination_from(Coding(system=DISPOSITION_SYSTEM, code=code)) == expected


def test_destination_missing_or_foreign_system_is_unknown() -> None:
    assert destination_from(None) == DischargeDestination.UNKNOWN
    assert destination_from(Coding(display="Home")) == DischargeDestination.UNKNOWN
    # e.g. a NUBC code "03" (SNF) is not mapped yet, so it must not be guessed
    assert destination_from(Coding(system="https://www.nubc.org", code="03")) == (
        DischargeDestination.UNKNOWN
    )


def test_merged_notice_uses_imp_disposition() -> None:
    er = er_visit(T0, hours=1)
    imp = admission(T0 + timedelta(minutes=30), disposition="snf")
    [notice] = detect_discharges([er, imp])
    assert notice.destination == DischargeDestination.SNF
