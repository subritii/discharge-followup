"""Drafter with a stub LLM: prompt contents, output handling, safety checks."""

import re
from typing import Any

import pytest

from adapters.synthea import SyntheaAdapter
from agent.drafter import AI_DRAFT_LABEL, LLMDraft, Prompt, draft_plan
from models import CaseRoute
from privacy.guard import UnredactedIdentifierError
from privacy.payload import NoAIDraftError
from privacy.redactor import Redactor
from tests.conftest import MED_CHANGES_ID
from tests.episode import Episode, build_episode

GOOD = LLMDraft(
    summary="The patient was admitted for an acute STEMI and stayed one day. Five "
    "medications were started. Risk is moderate (LACE 5).",
    rn_call_checklist=[
        "Confirm discharge destination",
        "Review each started medication",
        "Review chest pain red flags",
        "Schedule follow-up visit within 14 days",
    ],
)


class StubLLM:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.messages: list[tuple[str, str]] = []
        self.schema: Any = None
        self.kwargs: dict[str, Any] = {}

    def with_structured_output(self, schema: Any, **kwargs: Any) -> "StubLLM":
        self.schema, self.kwargs = schema, kwargs
        return self

    def invoke(self, messages: list[tuple[str, str]]) -> Any:
        self.messages = messages
        return self.response

    @property
    def prompt_text(self) -> str:
        return "\n".join(text for _, text in self.messages)


@pytest.fixture(scope="module")
def episode(synthea: SyntheaAdapter, redactor: Redactor) -> Episode:
    return build_episode(synthea, MED_CHANGES_ID, redactor)


def test_draft_combines_llm_text_with_deterministic_meds(episode: Episode) -> None:
    llm = StubLLM(GOOD)
    plan = draft_plan(episode.payload, episode.triage, episode.known, llm, "stub-model")
    assert plan.summary == GOOD.summary
    assert plan.rn_call_checklist == tuple(GOOD.rn_call_checklist)
    assert plan.med_changes == episode.payload.medications  # never from the model
    assert plan.model == "stub-model"
    assert plan.prompt_version == "discharge_plan@v1"
    assert plan.label == AI_DRAFT_LABEL
    assert llm.schema is LLMDraft
    assert llm.kwargs == {"method": "json_schema"}


def test_prompt_contains_clinical_facts_and_triage(episode: Episode) -> None:
    llm = StubLLM(GOOD)
    draft_plan(episode.payload, episode.triage, episode.known, llm, "stub")
    text = llm.prompt_text
    assert "Acute ST segment elevation myocardial infarction" in text
    assert '"change": "started"' in text
    assert '"risk_level": "moderate"' in text
    assert '"lace_total": 5' in text
    assert "DESTINATION_UNVERIFIED" in text
    assert "never contact the patient" in text


def test_prompt_has_no_identifiers_ids_or_dates(episode: Episode) -> None:
    llm = StubLLM(GOOD)
    draft_plan(episode.payload, episode.triage, episode.known, llm, "stub")
    text = llm.prompt_text
    assert episode.known.find(text) == []
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text)  # record ids
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)  # calendar dates
    assert episode.triage.idempotency_key not in text


def test_dict_output_is_validated(episode: Episode) -> None:
    plan = draft_plan(
        episode.payload, episode.triage, episode.known, StubLLM(GOOD.model_dump()), "stub"
    )
    assert plan.summary == GOOD.summary


def test_invalid_output_rejected(episode: Episode) -> None:
    with pytest.raises(ValueError):
        draft_plan(
            episode.payload,
            episode.triage,
            episode.known,
            StubLLM({"summary": "too short", "rn_call_checklist": []}),
            "stub",
        )


def test_invented_identifier_in_output_rejected(episode: Episode) -> None:
    given = episode.patient.given_names[0].rstrip("0123456789")
    bad = GOOD.model_copy(update={"summary": f"{given} was admitted for an acute STEMI."})
    with pytest.raises(UnredactedIdentifierError, match="output:name"):
        draft_plan(episode.payload, episode.triage, episode.known, StubLLM(bad), "stub")


def test_identifier_in_prompt_blocked_before_call(episode: Episode) -> None:
    given = episode.patient.given_names[0].rstrip("0123456789")
    leaky = Prompt(version="x@v0", system="Patient " + given, user="{{payload}}{{triage}}")
    llm = StubLLM(GOOD)
    with pytest.raises(UnredactedIdentifierError, match="prompt:name"):
        draft_plan(episode.payload, episode.triage, episode.known, llm, "stub", prompt=leaky)
    assert llm.messages == []  # the model was never called


@pytest.mark.parametrize("route", [CaseRoute.CLOSE_THE_LOOP, CaseRoute.DECEASED_STATUS_REVIEW])
def test_no_draft_when_not_allowed(episode: Episode, route: CaseRoute) -> None:
    blocked = episode.triage.model_copy(update={"route": route, "ai_draft_allowed": False})
    llm = StubLLM(GOOD)
    with pytest.raises(NoAIDraftError):
        draft_plan(episode.payload, blocked, episode.known, llm, "stub")
    assert llm.messages == []


def test_prompt_file_parses() -> None:
    prompt = Prompt.load()
    assert prompt.version == "discharge_plan@v1"
    assert "{{payload}}" in prompt.user and "{{triage}}" in prompt.user
    assert "{{" not in prompt.system
