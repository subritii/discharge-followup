"""Create the follow-up Case(s) for one fixture patient in the Salesforce org.

Usage: uv run python scripts/create_case.py <patient_id>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.synthea import SyntheaAdapter  # noqa: E402
from agent.deadlines import BusinessCalendar  # noqa: E402
from agent.draft_cache import DraftCache  # noqa: E402
from agent.llm import make_agent_llm  # noqa: E402
from config import Settings  # noqa: E402
from pipeline import Pipeline  # noqa: E402
from privacy.redactor import Redactor  # noqa: E402
from salesforce.client import connect  # noqa: E402
from salesforce.schema import check_case_fields  # noqa: E402

FIXTURES = Path("tests/fixtures/synthea")


def main(patient_id: str) -> int:
    settings = Settings()
    sf = connect(settings)
    problems = check_case_fields(sf.Case.describe())
    for problem in problems:
        print(f"schema warning: {problem}")

    pipeline = Pipeline(
        adapter=SyntheaAdapter(FIXTURES),
        calendar=BusinessCalendar.from_settings(settings),
        redactor=Redactor(),
        llm=lambda: make_agent_llm(settings),
        model_name=settings.agent_model,
        cache=DraftCache(Path("data/drafts")),
        salesforce=sf,
    )
    failed = False
    for outcome in pipeline.process_patient(patient_id):
        print(f"discharge {outcome.notice.idempotency_key}")
        if outcome.draft:
            print(f"  draft: {'cache' if outcome.draft_from_cache else 'generated'}")
        if outcome.case:
            action = "created" if outcome.case.created else "already exists, skipped"
            url = f"https://{settings.sf_domain}.salesforce.com/{outcome.case.case_id}"
            print(f"  case {outcome.case.case_id}: {action}  {url}")
        for error in outcome.errors:
            failed = True
            print(f"  ERROR {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
