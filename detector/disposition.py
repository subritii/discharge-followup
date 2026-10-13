"""Discharge disposition -> destination, driven by library/disposition_map.yaml."""

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


def resolve_destination(
    disposition: Coding | None,
    patient: Patient | None = None,
    mapping: DispositionMap | None = None,
) -> DischargeDestination:
    """Destination for a discharge. A deceased patient is always EXPIRED."""
    if patient is not None and patient.deceased:
        return DischargeDestination.EXPIRED
    return (mapping or default_disposition_map()).destination(disposition)
