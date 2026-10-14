"""Cache AI drafts on disk so re-runs don't repeat a slow local LLM call.

Keyed by idempotency key + model + prompt version: a different model (--model) or an
edited prompt produces a new draft instead of reusing a stale one.
"""

import re
from pathlib import Path

from agent.drafter import DraftPlan


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", part)


class DraftCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, idempotency_key: str, model: str, prompt_version: str) -> Path:
        name = "__".join(_safe(p) for p in (idempotency_key, model, prompt_version))
        return self.directory / f"{name}.json"

    def get(self, idempotency_key: str, model: str, prompt_version: str) -> DraftPlan | None:
        path = self.path(idempotency_key, model, prompt_version)
        if not path.exists():
            return None
        return DraftPlan.model_validate_json(path.read_text())

    def put(self, idempotency_key: str, plan: DraftPlan) -> Path:
        path = self.path(idempotency_key, plan.model, plan.prompt_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plan.model_dump_json(indent=2))
        return path
