"""Process every fixture patient end to end and print a summary.

  uv run python run.py                     # all fixtures -> drafts -> Salesforce Cases
  uv run python run.py --dry-run           # no Salesforce; print each Case payload instead
  uv run python run.py --dry-run --offline --drafts demo/drafts   # demo: no Ollama, no secrets
  uv run python run.py --model llama3.2:3b --dry-run   # compare another local model

Drafts are cached in data/drafts/ by idempotency key + model + prompt version, so a
re-run reuses them instead of repeating the slow local LLM call. Cases are created
once per idempotency key; existing Cases are skipped, never updated.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from adapters.synthea import SyntheaAdapter
from agent.deadlines import BusinessCalendar
from agent.draft_cache import DraftCache
from agent.drafter import StructuredChatModel
from agent.llm import make_agent_llm
from config import Settings
from pipeline import Outcome, Pipeline
from privacy.redactor import Redactor

DEFAULT_FIXTURES = Path("tests/fixtures/synthea")
DEFAULT_DRAFTS = Path("data/drafts")
# Fixture cutoff: older hospital encounters exist only as context for chronic conditions
# (e.g. a 1964 ER visit where a diagnosis was first recorded), not as discharges to act on.
DEFAULT_SINCE = "2023-06-01"


class OfflineError(RuntimeError):
    pass


def offline_llm() -> StructuredChatModel:
    raise OfflineError("no saved draft for this discharge, and --offline never calls Ollama")


def format_payload(outcome: Outcome) -> str:
    """The Case body a live run would send, as indented JSON (dry run only)."""
    body = json.dumps(outcome.case_payload, indent=2, ensure_ascii=False)
    return "\n".join("      " + line for line in ["Salesforce Case payload:", *body.splitlines()])


@dataclass(frozen=True)
class Row:
    patient: str
    discharged: str
    pathway: str
    route: str
    risk: str
    next_action: str
    draft: str
    case: str
    error: str


def to_row(outcome: Outcome, dry_run: bool) -> Row:
    triage = outcome.triage
    first = triage.tasks[0] if triage and triage.tasks else None
    risk = "-"
    if triage and triage.risk_level:
        risk = triage.risk_level.value + (f" (LACE {triage.lace.total})" if triage.lace else "")
    if outcome.draft:
        draft = "cache" if outcome.draft_from_cache else "generated"
    else:
        draft = "not allowed" if triage and not triage.ai_draft_allowed else "-"
    if outcome.case:
        case = f"{'created' if outcome.case.created else 'exists'} {outcome.case.case_id}"
    else:
        case = "dry run" if dry_run and not outcome.errors else "-"
    return Row(
        patient=outcome.notice.patient_id[:8],
        discharged=triage.discharge_local_date.isoformat() if triage else "?",
        pathway=triage.pathway if triage else outcome.notice.encounter_class,
        route=triage.route.value if triage else "-",
        risk=risk,
        next_action=f"{first.kind.value} {first.due_date.isoformat()}" if first else "-",
        draft=draft,
        case=case,
        error=outcome.errors[0] if outcome.errors else "",
    )


COLUMNS = [
    ("patient", "Patient"),
    ("discharged", "Discharged"),
    ("pathway", "Pathway"),
    ("route", "Route"),
    ("risk", "Risk"),
    ("next_action", "Next action (due)"),
    ("draft", "Draft"),
    ("case", "Case"),
]


def render_summary(rows: list[Row], patients: int, model: str, seconds: float) -> str:
    widths = {
        attr: max(len(title), *(len(getattr(r, attr)) for r in rows)) if rows else len(title)
        for attr, title in COLUMNS
    }
    line = "  ".join(title.ljust(widths[attr]) for attr, title in COLUMNS)
    out = [line, "  ".join("-" * widths[attr] for attr, _ in COLUMNS)]
    out += ["  ".join(getattr(r, a).ljust(widths[a]) for a, _ in COLUMNS) for r in rows]
    errors = [r for r in rows if r.error]
    out.append("")
    out += [f"ERROR {r.patient}: {r.error}" for r in errors]
    created = sum(r.case.startswith("created") for r in rows)
    existing = sum(r.case.startswith("exists") for r in rows)
    generated = sum(r.draft == "generated" for r in rows)
    cached = sum(r.draft == "cache" for r in rows)
    out.append(
        f"{patients} patients, {len(rows)} discharges | drafts: {generated} generated, "
        f"{cached} from cache | cases: {created} created, {existing} already existed | "
        f"errors: {len(errors)} | model {model} | {seconds / 60:.1f} min"
    )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", help="Ollama model (default: AGENT_MODEL from .env)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="don't call Salesforce; print the exact Case payload instead",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="never call Ollama; use saved drafts only (a missing draft is an error)",
    )
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--drafts", type=Path, default=DEFAULT_DRAFTS)
    parser.add_argument(
        "--since",
        type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC),
        default=DEFAULT_SINCE,
        help=f"only discharges on/after this date, YYYY-MM-DD (default {DEFAULT_SINCE})",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    if args.model:
        settings = settings.model_copy(update={"agent_model": args.model})
    salesforce = None
    if not args.dry_run:
        from salesforce.client import connect

        salesforce = connect(settings)

    adapter = SyntheaAdapter(args.fixtures)
    pipeline = Pipeline(
        adapter=adapter,
        calendar=BusinessCalendar.from_settings(settings),
        redactor=Redactor(),
        llm=offline_llm if args.offline else lambda: make_agent_llm(settings),
        model_name=settings.agent_model,
        cache=DraftCache(args.drafts),
        salesforce=salesforce,
    )

    start = time.monotonic()
    rows: list[Row] = []
    patient_ids = adapter.patient_ids()
    for i, pid in enumerate(patient_ids, 1):
        print(f"[{i}/{len(patient_ids)}] patient {pid[:8]}", flush=True)
        for outcome in pipeline.process_patient(pid, discharged_since=args.since):
            row = to_row(outcome, args.dry_run)
            rows.append(row)
            print(
                f"    {row.discharged} {row.route}: draft {row.draft}, case {row.case}"
                + (f", ERROR {row.error}" if row.error else ""),
                flush=True,
            )
            if args.dry_run and outcome.case_payload is not None:
                print(format_payload(outcome), flush=True)

    print()
    print(render_summary(rows, len(patient_ids), settings.agent_model, time.monotonic() - start))
    return 1 if any(r.error for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
