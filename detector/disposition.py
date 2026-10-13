"""Discharge disposition -> destination, driven by library/disposition_map.yaml."""

from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from models import Coding, DischargeDestination, Patient

DEFAULT_MAP_PATH = Path(__file__).resolve().parent.parent / "library" / "disposition_map.yaml"


class DispositionMap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_system: str | None = None
    systems: dict[str, dict[str, DischargeDestination]]

    def destination(self, disposition: Coding | None) -> DischargeDestination:
        """Missing, unknown system, or unknown code -> UNKNOWN. Never guesses."""
        if disposition is None or disposition.code is None:
            return DischargeDestination.UNKNOWN
        system = disposition.system or self.default_system
        if system is None:
            return DischargeDestination.UNKNOWN
        codes = self.systems.get(system, {})
        return codes.get(disposition.code, DischargeDestination.UNKNOWN)


def load_disposition_map(path: Path = DEFAULT_MAP_PATH) -> DispositionMap:
    return DispositionMap.model_validate(yaml.safe_load(path.read_text()))


@lru_cache
def default_disposition_map() -> DispositionMap:
    return load_disposition_map()


# A death this soon after discharge still closes the loop instead of a patient call.
DEATH_WINDOW_AFTER_DISCHARGE = timedelta(days=3)


def died_during_episode(patient: Patient | None, start: datetime, end: datetime) -> bool:
    """deceasedDateTime within [start, end + 3 days].

    deceasedBoolean without a date doesn't count: we can't place the death relative to
    this stay (e.g. a death years later must not relabel an old discharge).
    """
    if patient is None or patient.deceased_at is None:
        return False
    return start <= patient.deceased_at <= end + DEATH_WINDOW_AFTER_DISCHARGE


def resolve_destination(
    disposition: Coding | None,
    mapping: DispositionMap | None = None,
    *,
    patient: Patient | None = None,
    episode_start: datetime | None = None,
    discharge_time: datetime | None = None,
) -> DischargeDestination:
    """EXPIRED if disposition is `exp` or the patient died during the episode or within
    3 days of discharge; otherwise the mapped disposition."""
    if (
        episode_start is not None
        and discharge_time is not None
        and died_during_episode(patient, episode_start, discharge_time)
    ):
        return DischargeDestination.EXPIRED
    return (mapping or default_disposition_map()).destination(disposition)
