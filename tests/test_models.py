from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from models import Encounter


def make_encounter(**overrides: object) -> Encounter:
    fields: dict[str, object] = {
        "id": "enc-1",
        "patient_id": "pat-1",
        "status": "finished",
        "class_code": "IMP",
        "period_start": "2026-01-18T07:49:51-07:00",
        "period_end": "2026-02-10T08:04:51-07:00",
    }
    fields.update(overrides)
    return Encounter(**fields)


def test_discharge_time_is_utc() -> None:
    enc = make_encounter()
    assert enc.discharge_time == datetime(2026, 2, 10, 15, 4, 51, tzinfo=UTC)
    assert enc.discharge_time.utcoffset() == timedelta(0)


def test_idempotency_key_format() -> None:
    assert make_encounter().idempotency_key == "enc-1|2026-02-10T15:04:51Z"


def test_idempotency_key_same_instant_different_offsets() -> None:
    a = make_encounter(period_end="2026-02-10T08:04:51-07:00")
    b = make_encounter(period_end="2026-02-10T15:04:51Z")
    c = make_encounter(
        period_end=datetime(2026, 2, 10, 10, 4, 51, tzinfo=timezone(-timedelta(hours=5)))
    )
    assert a.idempotency_key == b.idempotency_key == c.idempotency_key


def test_idempotency_key_differs_by_encounter_and_time() -> None:
    base = make_encounter()
    assert make_encounter(id="enc-2").idempotency_key != base.idempotency_key
    assert (
        make_encounter(period_end="2026-02-10T08:05:51-07:00").idempotency_key
        != base.idempotency_key
    )


def test_no_period_end_means_no_discharge() -> None:
    enc = make_encounter(status="in-progress", period_end=None)
    assert enc.discharge_time is None
    assert enc.idempotency_key is None


def test_naive_timestamps_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        make_encounter(period_end="2026-02-10T08:04:51")


@pytest.mark.parametrize(
    ("class_code", "expected"), [("IMP", True), ("EMER", True), ("AMB", False), ("HH", False)]
)
def test_is_hospital_class(class_code: str, expected: bool) -> None:
    assert make_encounter(class_code=class_code).is_hospital_class is expected


def test_models_are_immutable() -> None:
    with pytest.raises(ValidationError):
        make_encounter().status = "cancelled"  # type: ignore[misc]
