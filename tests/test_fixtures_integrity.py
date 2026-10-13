"""Guards on the fixture files themselves, so a bad regeneration fails loudly."""

import json
from pathlib import Path

import pytest

from adapters.fhir_parse import ref_id
from tests.conftest import SYNTHEA_FIXTURES

FIXTURE_FILES = sorted(SYNTHEA_FIXTURES.glob("*.json"))
ALLOWED_TYPES = {"Patient", "Encounter", "Condition", "Medication", "MedicationRequest"}
MAX_BYTES = 100 * 1024


def test_expected_fixtures_present() -> None:
    assert {f.stem for f in FIXTURE_FILES} == {
        "inpatient",
        "er_visit",
        "med_changes",
        "no_hospital",
    }


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_fixture_small_and_scoped(path: Path) -> None:
    assert path.stat().st_size < MAX_BYTES
    resources = [e["resource"] for e in json.loads(path.read_text())["entry"]]
    assert {r["resourceType"] for r in resources} <= ALLOWED_TYPES


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_internal_references_resolve(path: Path) -> None:
    resources = [e["resource"] for e in json.loads(path.read_text())["entry"]]
    ids = {r["id"] for r in resources}
    for r in resources:
        for field in ("subject", "encounter", "medicationReference"):
            ref = r.get(field, {}).get("reference")
            if ref and ref.startswith("urn:uuid:"):
                assert ref_id(ref) in ids, f"{r['resourceType']}/{r['id']} {field} -> {ref}"
