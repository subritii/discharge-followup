from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent.deadlines import BusinessCalendar, load_holidays
from config import Settings

HOLIDAYS = load_holidays(Path("library/holidays.yaml"))
CHICAGO = BusinessCalendar(ZoneInfo("America/Chicago"), HOLIDAYS)
UTC_CAL = BusinessCalendar(ZoneInfo("UTC"), HOLIDAYS)
CDT = timezone(timedelta(hours=-5))
CST = timezone(timedelta(hours=-6))


def contact_due(cal: BusinessCalendar, discharged: datetime, days: int = 2) -> date:
    return cal.add_business_days(cal.local_date(discharged), days)


# --- Local time zone ----------------------------------------------------------------


def test_friday_night_is_friday_in_chicago_not_saturday() -> None:
    discharged = datetime(2026, 10, 17, 3, 30, tzinfo=UTC)  # Fri 22:30 CDT
    assert CHICAGO.local_date(discharged) == date(2026, 10, 16)
    assert UTC_CAL.local_date(discharged) == date(2026, 10, 17)


def test_friday_night_discharge_due_tuesday() -> None:
    discharged = datetime(2026, 10, 16, 22, 30, tzinfo=CDT)  # Friday night
    assert contact_due(CHICAGO, discharged) == date(2026, 10, 20)  # Mon=1, Tue=2


def test_time_zone_changes_the_due_date() -> None:
    discharged = datetime(2026, 10, 20, 3, 30, tzinfo=UTC)  # Mon 22:30 CDT / Tue in UTC
    assert contact_due(CHICAGO, discharged) == date(2026, 10, 21)  # Wed
    assert contact_due(UTC_CAL, discharged) == date(2026, 10, 22)  # Thu


@pytest.mark.parametrize("day", [date(2026, 10, 17), date(2026, 10, 18)])
def test_weekend_discharge_due_tuesday(day: date) -> None:
    assert CHICAGO.add_business_days(day, 2) == date(2026, 10, 20)


def test_dst_fall_back_night() -> None:
    discharged = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 01:30 local, repeated hour
    assert CHICAGO.local_date(discharged) == date(2026, 11, 1)  # Sunday
    assert contact_due(CHICAGO, discharged) == date(2026, 11, 3)


# --- Holidays -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("discharged", "due", "why"),
    [
        (datetime(2026, 11, 25, 15, 0, tzinfo=CST), date(2026, 11, 30), "Thanksgiving Thu"),
        (datetime(2026, 11, 26, 9, 0, tzinfo=CST), date(2026, 11, 30), "discharged on holiday"),
        (datetime(2026, 12, 23, 12, 0, tzinfo=CST), date(2026, 12, 28), "Christmas Fri"),
        (datetime(2027, 1, 15, 22, 45, tzinfo=CST), date(2027, 1, 20), "Fri night + MLK Mon"),
        (datetime(2026, 9, 4, 23, 0, tzinfo=CDT), date(2026, 9, 9), "Fri night + Labor Day"),
        (datetime(2026, 7, 2, 10, 0, tzinfo=CDT), date(2026, 7, 7), "Jul 4 observed Fri Jul 3"),
        (datetime(2023, 9, 1, 10, 0, tzinfo=CDT), date(2023, 9, 6), "Labor Day 2023 (Mon 9/4)"),
        (datetime(2024, 11, 27, 10, 0, tzinfo=CST), date(2024, 12, 2), "Thanksgiving 2024"),
        (
            datetime(2023, 12, 22, 22, 0, tzinfo=CST),
            date(2023, 12, 27),
            "Fri night + Christmas Mon",
        ),
    ],
)
def test_holiday_discharges(discharged: datetime, due: date, why: str) -> None:
    assert contact_due(CHICAGO, discharged) == due, why


def test_holiday_list_is_configurable() -> None:
    no_holidays = BusinessCalendar(ZoneInfo("America/Chicago"))
    wed_before_thanksgiving = date(2026, 11, 25)
    assert no_holidays.add_business_days(wed_before_thanksgiving, 2) == date(2026, 11, 27)
    assert CHICAGO.add_business_days(wed_before_thanksgiving, 2) == date(2026, 11, 30)


def test_from_settings(tmp_path: Path) -> None:
    holidays = tmp_path / "h.yaml"
    holidays.write_text("holidays:\n  - 2026-10-19\n")
    settings = Settings(
        agent_model="a",
        judge_model="j",
        practice_timezone="America/New_York",
        holidays_file=holidays,
        _env_file=None,
    )
    cal = BusinessCalendar.from_settings(settings)
    assert cal.timezone.key == "America/New_York"
    assert cal.add_business_days(date(2026, 10, 16), 2) == date(2026, 10, 21)  # Mon off


def test_invalid_holidays_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("holidays:\n  - next tuesday\n")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        load_holidays(bad)


def test_holiday_coverage() -> None:
    assert CHICAGO.covers(date(2023, 1, 1)) and CHICAGO.covers(date(2024, 7, 1))
    assert CHICAGO.covers(date(2026, 5, 1))
    assert not CHICAGO.covers(date(2028, 5, 1))


# --- "Within N calendar days" -------------------------------------------------------


def test_visit_deadline_on_weekday_unchanged() -> None:
    assert CHICAGO.within_calendar_days(date(2026, 10, 14), 7) == date(2026, 10, 21)


def test_visit_deadline_on_weekend_moves_earlier() -> None:
    saturday = date(2026, 10, 17)
    assert CHICAGO.within_calendar_days(saturday, 7) == date(2026, 10, 23)  # Fri, not Mon


def test_visit_deadline_on_holiday_moves_earlier() -> None:
    assert CHICAGO.within_calendar_days(date(2026, 11, 19), 7) == date(2026, 11, 25)


# --- Guards -------------------------------------------------------------------------


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        CHICAGO.local_date(datetime(2026, 10, 16, 22, 30))


def test_zero_business_days_rejected() -> None:
    with pytest.raises(ValueError):
        CHICAGO.add_business_days(date(2026, 10, 16), 0)
