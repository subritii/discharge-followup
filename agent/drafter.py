"""One LLM call: allowlisted payload + triage context -> stay summary and RN checklist.

Everything else in the draft is deterministic: the medication list (agent/med_diff.py),
the risk explanation (agent/risk_explanation.py) and the review flags
(agent/review_flags.py). The prompt and the model output are both checked against the
patient's known identifiers.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agent.review_flags import ReviewFlag, review_flags
from agent.risk_explanation import explain_risk
from models import TriageResult
from privacy.guard import KnownIdentifiers, UnredactedIdentifierError
from privacy.payload import LLMPayload, MedicationLine, NoAIDraftError, triage_context

PROMPT_PATH = Path(__file__).resolve().parent.parent / "library" / "prompts" / "discharge_plan.md"
AI_DRAFT_LABEL = "AI draft — requires RN review and approval. Not sent to the patient."


class LLMDraft(BaseModel):
    """What the model must return (enforced as a JSON schema by Ollama)."""

    summary: str = Field(min_length=20, max_length=1500)
    rn_call_checklist: list[str] = Field(min_length=3, max_length=10)


class DraftPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str  # LLM: the stay and medication changes
    risk_explanation: str  # deterministic, from TriageResult
    med_changes: tuple[MedicationLine, ...]  # deterministic, from the payload
    review_flags: tuple[ReviewFlag, ...]  # deterministic
    rn_call_checklist: tuple[str, ...]  # LLM
    model: str
    prompt_version: str
    label: str = AI_DRAFT_LABEL


class StructuredChatModel(Protocol):
    """The slice of a LangChain chat model we use (lets tests pass a stub)."""

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Prompt:
    version: str
    system: str
    user: str

    @classmethod
    def load(cls, path: Path = PROMPT_PATH) -> "Prompt":
        _, front, body = path.read_text().split("---", 2)
        meta = yaml.safe_load(front)
        system, user = body.split("## user", 1)
        return cls(
            version=f"{meta['name']}@v{meta['version']}",
            system=system.replace("## system", "", 1).strip(),
            user=user.strip(),
        )

    def render(self, payload: LLMPayload, triage_json: str) -> list[tuple[str, str]]:
        user = self.user.replace("{{payload}}", payload.model_dump_json(indent=1)).replace(
            "{{triage}}", triage_json
        )
        return [("system", self.system), ("human", user)]


def draft_plan(
    payload: LLMPayload,
    triage: TriageResult,
    known: KnownIdentifiers,
    llm: StructuredChatModel,
    model_name: str,
    prompt: Prompt | None = None,
) -> DraftPlan:
    if not triage.ai_draft_allowed:
        raise NoAIDraftError(f"no AI draft for route={triage.route.value}")
    prompt = prompt or Prompt.load()
    messages = prompt.render(payload, triage_context(triage).model_dump_json(indent=1))

    # Last check on the exact text leaving the process.
    leaked = known.find("\n".join(text for _, text in messages))
    if leaked:
        raise UnredactedIdentifierError([f"prompt:{kind}" for kind in leaked])

    raw = llm.with_structured_output(LLMDraft, method="json_schema").invoke(messages)
    draft = raw if isinstance(raw, LLMDraft) else LLMDraft.model_validate(raw)

    # The model can't know identifiers, but it can make some up; never pass those on.
    leaked = known.find(json.dumps(draft.model_dump()))
    if leaked:
        raise UnredactedIdentifierError([f"output:{kind}" for kind in leaked])

    return DraftPlan(
        summary=draft.summary.strip(),
        risk_explanation=explain_risk(triage),
        med_changes=payload.medications,
        review_flags=review_flags(payload.medications),
        rn_call_checklist=tuple(item.strip() for item in draft.rn_call_checklist if item.strip()),
        model=model_name,
        prompt_version=prompt.version,
    )
