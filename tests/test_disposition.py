from pathlib import Path

import pytest
from pydantic import ValidationError

from detector.disposition import (
    DEFAULT_MAP_PATH,
    DispositionMap,
    load_disposition_map,
    resolve_destination,
)
from models import Coding, DischargeDestination, Patient
from tests.conftest import DISPOSITION_SYSTEM


@pytest.fixture(scope="module")
def mapping() -> DispositionMap:
    return load_disposition_map()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("home", DischargeDestination.HOME),
        ("alt-home", DischargeDestination.HOME),
        ("snf", DischargeDestination.SNF),
        ("rehab", DischargeDestination.OTHER),
        ("hosp", DischargeDestination.OTHER),
        ("oth", DischargeDestination.OTHER),
        ("exp", DischargeDestination.EXPIRED),
        ("made-up", DischargeDestination.UNKNOWN),
    ],
)
def test_hl7_codes(mapping: DispositionMap, code: str, expected: DischargeDestination) -> None:
    assert mapping.destination(Coding(system=DISPOSITION_SYSTEM, code=code)) == expected


def test_missing_system_uses_default(mapping: DispositionMap) -> None:
    assert mapping.destination(Coding(code="snf")) == DischargeDestination.SNF


def test_missing_or_foreign_is_unknown(mapping: DispositionMap) -> None:
    assert mapping.destination(None) == DischargeDestination.UNKNOWN
    assert mapping.destination(Coding(display="Home")) == DischargeDestination.UNKNOWN
    # NUBC "03" (SNF) is not configured yet, so it must not be guessed.
    nubc = Coding(system="https://www.nubc.org/patient-discharge", code="03")
    assert mapping.destination(nubc) == DischargeDestination.UNKNOWN


def test_new_system_is_config_only(tmp_path: Path) -> None:
    # What Phase 11 will do for NUBC: add a system to the YAML, no code change.
    extended = DEFAULT_MAP_PATH.read_text() + (
        "  https://www.nubc.org/patient-discharge:\n"
        "    '01': home\n"
        "    '03': snf\n"
        "    '20': expired\n"
    )
    path = tmp_path / "map.yaml"
    path.write_text(extended)
    m = load_disposition_map(path)
    nubc = "https://www.nubc.org/patient-discharge"
    assert m.destination(Coding(system=nubc, code="03")) == DischargeDestination.SNF
    assert m.destination(Coding(system=nubc, code="20")) == DischargeDestination.EXPIRED
    assert (
        m.destination(Coding(system=DISPOSITION_SYSTEM, code="home")) == DischargeDestination.HOME
    )


def test_invalid_destination_in_yaml_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("systems:\n  x:\n    a: hospice-ish\n")
    with pytest.raises(ValidationError):
        load_disposition_map(path)


def test_deceased_overrides_everything(mapping: DispositionMap) -> None:
    dead = Patient(id="p", deceased=True)
    home = Coding(system=DISPOSITION_SYSTEM, code="home")
    assert resolve_destination(home, dead, mapping) == DischargeDestination.EXPIRED
    assert resolve_destination(None, dead, mapping) == DischargeDestination.EXPIRED
    assert resolve_destination(home, Patient(id="p"), mapping) == DischargeDestination.HOME
