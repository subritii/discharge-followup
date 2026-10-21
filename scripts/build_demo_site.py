"""Build the static demo site (docs/index.html) from the pipeline's offline dry run.

  uv run python scripts/build_demo_site.py

Same run as the Docker demo (run.py --dry-run --offline --drafts demo/drafts): saved
drafts, no Ollama, no Salesforce, no secrets. The results are embedded as JSON in one
self-contained HTML file (no external requests), ready for GitHub Pages from /docs.
Only FHIR ids and clinical fields are shown; never names or other demographics.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.synthea import SyntheaAdapter  # noqa: E402
from agent.deadlines import BusinessCalendar  # noqa: E402
from agent.draft_cache import DraftCache  # noqa: E402
from agent.risk_explanation import explain_risk  # noqa: E402
from config import Settings  # noqa: E402
from pipeline import Outcome, Pipeline  # noqa: E402
from privacy.redactor import Redactor  # noqa: E402
from run import DEFAULT_FIXTURES, DEFAULT_SINCE, offline_llm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "demo_site_template.html"
DEFAULT_OUT = ROOT / "docs" / "index.html"
DEMO_DRAFTS = ROOT / "demo" / "drafts"
DEMO_ENV = ROOT / "demo" / "demo.env"  # non-secret settings; .env is never read
DATA_MARKER = "/*__DEMO_DATA__*/null"

# Plain-language labels for the fixture files (tests/fixtures/synthea/<stem>.json).
SCENARIOS = {
    "inpatient": "Hospital stay, moderate risk",
    "med_changes": "Hospital stay with medication changes",
    "high_risk": "Frequent ER visits, high risk",
    "er_visit": "Single ER visit",
    "no_hospital": "No hospital visits (nothing to do)",
}


def scenario_by_patient(fixtures: Path) -> dict[str, str]:
    """Patient id -> fixture file stem (each fixture bundle holds one patient)."""
    out: dict[str, str] = {}
    for path in sorted(fixtures.glob("*.json")):
        bundle = json.loads(path.read_text())
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") == "Patient":
                out[resource["id"]] = path.stem
    return out


def discharge_view(outcome: Outcome, adapter: SyntheaAdapter) -> dict[str, Any]:
    """Everything one discharge's steps need, JSON-ready. No demographics."""
    notice = outcome.notice
    encounter = next(
        e for e in adapter.get_encounters(notice.patient_id) if e.id == notice.encounter_id
    )
    triage = outcome.triage
    return {
        "detection": {
            "encounter_id": encounter.id,
            "class_code": encounter.class_code,
            "status": encounter.status,
            "period_start": encounter.period_start.isoformat() if encounter.period_start else None,
            "period_end": encounter.period_end.isoformat() if encounter.period_end else None,
            "destination": notice.destination.value,
            "linked_er_encounter_id": notice.linked_er_encounter_id,
            "reasons": [c.display for c in notice.reasons if c.display],
        },
        "triage": triage.model_dump(mode="json") if triage else None,
        "risk_explanation": explain_risk(triage) if triage else None,
        "draft": outcome.draft.model_dump(mode="json") if outcome.draft else None,
        "case_payload": outcome.case_payload,
        "errors": outcome.errors,
    }


def collect(fixtures: Path, drafts: Path, since: datetime) -> dict[str, Any]:
    settings = Settings(_env_file=DEMO_ENV)
    adapter = SyntheaAdapter(fixtures)
    pipeline = Pipeline(
        adapter=adapter,
        calendar=BusinessCalendar.from_settings(settings),
        redactor=Redactor(),
        llm=offline_llm,
        model_name=settings.agent_model,
        cache=DraftCache(drafts),
        salesforce=None,
    )
    scenarios = scenario_by_patient(fixtures)
    order = list(SCENARIOS)
    patients = []
    # Picker order follows SCENARIOS; unknown fixtures go last.
    for pid in sorted(
        adapter.patient_ids(),
        key=lambda p: order.index(s) if (s := scenarios.get(p)) in order else len(order),
    ):
        outcomes = pipeline.process_patient(pid, discharged_since=since)
        stem = scenarios.get(pid, "")
        patients.append(
            {
                "id": pid,
                "scenario": SCENARIOS.get(stem, stem or "Synthetic patient"),
                "discharges": [discharge_view(o, adapter) for o in outcomes],
            }
        )
    return {
        "model": settings.agent_model,
        "since": since.date().isoformat(),
        "patients": patients,
    }


def render(data: dict[str, Any], template: str) -> str:
    # "</" is escaped so no string in the data can close the <script> element.
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    if template.count(DATA_MARKER) != 1:
        raise ValueError(f"template must contain {DATA_MARKER} exactly once")
    return template.replace(DATA_MARKER, blob)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixtures", type=Path, default=ROOT / DEFAULT_FIXTURES)
    parser.add_argument("--drafts", type=Path, default=DEMO_DRAFTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    since = datetime.fromisoformat(DEFAULT_SINCE).replace(tzinfo=UTC)
    data = collect(args.fixtures, args.drafts, since)
    errors = [e for p in data["patients"] for d in p["discharges"] for e in d["errors"]]
    if errors:
        print("\n".join(f"ERROR {e}" for e in errors), file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(data, TEMPLATE.read_text()))
    count = sum(len(p["discharges"]) for p in data["patients"])
    print(f"wrote {args.out} ({len(data['patients'])} patients, {count} discharges)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
