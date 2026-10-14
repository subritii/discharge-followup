"""Real Ollama call on the med_changes fixture. Run with: uv run pytest -m integration"""

import json
import urllib.request

import pytest

from adapters.synthea import SyntheaAdapter
from agent.drafter import draft_plan
from agent.llm import make_agent_llm
from config import Settings
from privacy.redactor import Redactor
from tests.conftest import MED_CHANGES_ID
from tests.episode import build_episode

pytestmark = pytest.mark.integration


def _ollama_has(settings: Settings) -> bool:
    try:
        with urllib.request.urlopen(f"{settings.ollama_base_url}/api/tags", timeout=2) as resp:
            names = {m["name"] for m in json.load(resp)["models"]}
    except OSError:
        return False
    return settings.agent_model in names


@pytest.fixture(scope="module")
def settings() -> Settings:
    s = Settings(
        agent_model="llama3.1:8b",
        judge_model="qwen2.5:7b",
        ollama_timeout_seconds=2400,  # slow on 8 GB machines
        _env_file=None,
    )
    if not _ollama_has(s):
        pytest.skip(f"Ollama not running or {s.agent_model} not pulled")
    return s


def test_real_draft_for_med_changes(
    settings: Settings, synthea: SyntheaAdapter, redactor: Redactor
) -> None:
    episode = build_episode(synthea, MED_CHANGES_ID, redactor)
    plan = draft_plan(
        episode.payload,
        episode.triage,
        episode.known,
        make_agent_llm(settings),
        settings.agent_model,
    )
    assert len(plan.summary) > 50
    assert 3 <= len(plan.rn_call_checklist) <= 10
    assert plan.med_changes == episode.payload.medications
    assert episode.known.find(plan.model_dump_json()) == []
    # Destination is unknown, so the prompt asks for confirmation first.
    assert any(
        "destination" in item.lower() or "discharged" in item.lower()
        for item in plan.rn_call_checklist[:2]
    )
