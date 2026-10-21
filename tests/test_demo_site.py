"""Tests for scripts/build_demo_site.py (offline: saved drafts, no Ollama, no Salesforce)."""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_demo_site as site  # noqa: E402

from adapters.synthea import SyntheaAdapter  # noqa: E402

TEMPLATE = f"<script>{site.DATA_MARKER}</script>"


def test_render_escapes_script_close() -> None:
    html = site.render({"text": "</script><b>x</b>"}, TEMPLATE)
    assert html.count("</script>") == 1  # only the template's own
    assert json.loads(html[len("<script>") : -len("</script>")].replace("<\\/", "</")) == {
        "text": "</script><b>x</b>"
    }


def test_render_requires_one_marker() -> None:
    with pytest.raises(ValueError):
        site.render({}, "<p>no marker</p>")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> str:
    out = tmp_path_factory.mktemp("site") / "index.html"
    assert site.main(["--out", str(out)]) == 0
    return out.read_text()


def embedded(html: str) -> dict:
    match = re.search(r'<script id="demo-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    return json.loads(match.group(1).replace("<\\/", "</"))


def test_site_has_every_discharge(built: str) -> None:
    data = embedded(built)
    assert len(data["patients"]) == 5
    discharges = [d for p in data["patients"] for d in p["discharges"]]
    assert len(discharges) == 7
    assert all(d["case_payload"]["RN_Approved__c"] is False for d in discharges)
    assert all(d["draft"] for d in discharges)  # every demo discharge has a saved draft
    assert data["patients"][0]["scenario"] == site.SCENARIOS["inpatient"]


def test_site_is_self_contained(built: str) -> None:
    assert not re.search(r"(src|href)\s*=", built)
    assert "http://" not in built and "https://" not in built


def test_site_has_no_demographics(built: str, synthea: SyntheaAdapter) -> None:
    for pid in synthea.patient_ids():
        patient = synthea.get_patient(pid)
        # Synthea's MRN equals the FHIR id, which the Case carries by design for lookup.
        mrn = patient.mrn if patient.mrn != patient.id else None
        for value in (*patient.given_names, patient.family_name, mrn, *patient.phones):
            if value:
                assert value not in built
        if patient.birth_date:
            assert patient.birth_date.isoformat() not in built


def test_committed_site_is_current(built: str) -> None:
    assert site.DEFAULT_OUT.read_text() == built, "run: uv run python scripts/build_demo_site.py"
