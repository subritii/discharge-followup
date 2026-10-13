"""Due dates in the practice's local time zone, skipping weekends and holidays."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from config import Settings


@dataclass(frozen=True)
class BusinessCalendar:
    timezone: ZoneInfo
    holidays: frozenset[date] = frozenset()

    @classmethod
    def from_settings(cls, settings: Settings) -> "BusinessCalendar":
        return cls(ZoneInfo(settings.practice_timezone), load_holidays(settings.holidays_file))

    def local_date(self, moment: datetime) -> date:
        """Calendar date of `moment` in the practice's time zone (e.g. a 03:30Z Saturday
        discharge is still Friday night in Chicago)."""
        if moment.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return moment.astimezone(self.timezone).date()

    def covers(self, day: date) -> bool:
        """True if the holiday list includes `day`'s year (else holidays are unknown)."""
        return day.year in {h.year for h in self.holidays}

    def is_business_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def add_business_days(self, start: date, days: int) -> date:
        """The `days`-th business day AFTER `start` (start itself never counts).

        Discharged Friday, 2 business days -> Tuesday.
        """
        if days < 1:
            raise ValueError("days must be >= 1")
        current, counted = start, 0
        while counted < days:
            current += timedelta(days=1)
            if self.is_business_day(current):
                counted += 1
        return current

    def within_calendar_days(self, start: date, days: int) -> date:
        """Last business day on or before start + `days`.

        For "visit within N days" the deadline must not slide past N, so a deadline
        landing on a weekend or holiday moves EARLIER.
        """
        due = start + timedelta(days=days)
        while not self.is_business_day(due):
            due -= timedelta(days=1)
        return due


def load_holidays(path: Path) -> frozenset[date]:
    data = yaml.safe_load(path.read_text()) or {}
    holidays = data.get("holidays") or []
    if not all(isinstance(d, date) for d in holidays):
        raise ValueError(f"{path}: holidays must be YYYY-MM-DD dates")
    return frozenset(holidays)
