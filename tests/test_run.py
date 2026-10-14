from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapters.synthea import SyntheaAdapter
from privacy.redactor import Redactor
from run import DEFAULT_SINCE, render_summary, to_row
from tests.test_drafter import GOOD, StubLLM
from tests.test_salesforce import FakeSalesforce, make_pipeline


def test_summary_over_all_fixtures(
    synthea: SyntheaAdapter, redactor: Redactor, tmp_path: Path
) -> None:
    parts: dict[str, Any] = {"adapter": synthea, "redactor": redactor}
    pipe = make_pipeline(parts, tmp_path, FakeSalesforce(), StubLLM(GOOD))
    since = datetime.fromisoformat(DEFAULT_SINCE).replace(tzinfo=UTC)
    rows = [
        to_row(o, dry_run=False)
        for pid in synthea.patient_ids()
        for o in pipe.process_patient(pid, discharged_since=since)
    ]
    assert len(synthea.patient_ids()) == 5
    assert len(rows) == 7  # no_hospital none; high_risk 4 (ER 2023, IMP 2023, ER x2 2026)
    assert all(r.case.startswith("created") for r in rows)
    text = render_summary(rows, 5, "stub-model", 90)
    assert "Next action (due)" in text
    assert "moderate (LACE 5)" in text and "high (LACE 11)" in text
    assert text.splitlines()[-1].startswith(
        "5 patients, 7 discharges | drafts: 7 generated, 0 from cache | cases: 7 created"
    )


def test_dry_run_rows(synthea: SyntheaAdapter, redactor: Redactor, tmp_path: Path) -> None:
    parts: dict[str, Any] = {"adapter": synthea, "redactor": redactor}
    pipe = make_pipeline(parts, tmp_path, None, StubLLM(GOOD))
    [outcome] = pipe.process_patient(synthea.patient_ids()[0])
    assert to_row(outcome, dry_run=True).case == "dry run"
