from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import run
from adapters.synthea import SyntheaAdapter
from privacy.redactor import Redactor
from run import DEFAULT_SINCE, render_summary, to_row
from tests.test_drafter import GOOD, StubLLM
from tests.test_salesforce import FakeSalesforce, make_pipeline

DEMO_DRAFTS = Path(__file__).resolve().parent.parent / "demo" / "drafts"


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


def test_offline_dry_run_prints_payload_without_ollama_or_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_DOMAIN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_MODEL", "llama3.1:8b")
    monkeypatch.setenv("JUDGE_MODEL", "qwen2.5:7b")
    settings_cls = run.Settings
    monkeypatch.setattr(run, "Settings", lambda: settings_cls(_env_file=None))
    monkeypatch.setattr(run, "make_agent_llm", lambda s: pytest.fail("Ollama was called"))
    assert run.main(["--dry-run", "--offline", "--drafts", str(DEMO_DRAFTS)]) == 0
    out = capsys.readouterr().out
    assert out.count("Salesforce Case payload:") == 7
    assert '"Idempotency_Key__c":' in out and '"RN_Approved__c": false' in out
    assert "drafts: 0 generated, 7 from cache" in out


def test_offline_without_saved_draft_is_an_error(
    synthea: SyntheaAdapter, redactor: Redactor, tmp_path: Path
) -> None:
    parts: dict[str, Any] = {"adapter": synthea, "redactor": redactor}
    pipe = make_pipeline(parts, tmp_path, None, StubLLM(GOOD))
    pipe.llm = run.offline_llm
    [outcome] = pipe.process_patient(synthea.patient_ids()[0])
    assert outcome.errors and "OfflineError" in outcome.errors[0]
    assert outcome.case_payload is None
