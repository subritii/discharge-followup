"""One patient, end to end: detect -> triage -> (minimize, redact, guard) -> draft -> Case.

Shared by run.py and the scripts. Each discharge's outcome is returned, including
errors, so one bad record doesn't stop the batch.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from adapters.base import PatientDataAdapter
from agent.deadlines import BusinessCalendar
from agent.draft_cache import DraftCache
from agent.drafter import DraftPlan, Prompt, StructuredChatModel, draft_plan
from agent.med_diff import med_diff
from agent.review_flags import review_flags
from agent.risk_explanation import explain_risk
from agent.triage_rules import triage
from detector.discharge import detect_for_patient
from models import DischargeNotice, TriageResult
from privacy.guard import KnownIdentifiers
from privacy.prepare import prepare_llm_payload
from privacy.redactor import Redactor
from salesforce.client import CaseResult, SalesforceApi, build_case, create_case_once


@dataclass
class Outcome:
    notice: DischargeNotice
    triage: TriageResult | None = None
    draft: DraftPlan | None = None
    draft_from_cache: bool = False
    case: CaseResult | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class Pipeline:
    adapter: PatientDataAdapter
    calendar: BusinessCalendar
    redactor: Redactor
    llm: Callable[[], StructuredChatModel]  # created lazily: cache hits never load the model
    model_name: str
    cache: DraftCache
    salesforce: SalesforceApi | None = None  # None = dry run, no Case created
    prompt: Prompt = field(default_factory=Prompt.load)
    _llm: StructuredChatModel | None = None

    def process_patient(
        self, patient_id: str, discharged_since: datetime | None = None
    ) -> list[Outcome]:
        notices = detect_for_patient(self.adapter, patient_id, discharged_since=discharged_since)
        return [self._process(n) for n in notices]

    def _draft(self, notice: DischargeNotice, result: TriageResult) -> tuple[DraftPlan, bool]:
        cached = self.cache.get(result.idempotency_key, self.model_name, self.prompt.version)
        if cached is not None:
            # Only the LLM text is reused; deterministic parts follow the current rules.
            refreshed = cached.model_copy(
                update={
                    "risk_explanation": explain_risk(result),
                    "review_flags": (*result.review_flags, *review_flags(cached.med_changes)),
                }
            )
            return refreshed, True
        pid = notice.patient_id
        patient = self.adapter.get_patient(pid)
        encounters = self.adapter.get_encounters(pid)
        episode_ids = {notice.encounter_id, notice.linked_er_encounter_id}
        payload = prepare_llm_payload(
            notice,
            patient,
            [c for c in self.adapter.get_conditions(pid) if c.encounter_id in episode_ids],
            med_diff(notice, self.adapter.get_medication_requests(pid), encounters),
            self.redactor,
        )
        if self._llm is None:
            self._llm = self.llm()
        plan = draft_plan(
            payload,
            result,
            KnownIdentifiers.from_patient(patient, notice),
            self._llm,
            self.model_name,
            self.prompt,
        )
        self.cache.put(result.idempotency_key, plan)
        return plan, False

    def _process(self, notice: DischargeNotice) -> Outcome:
        outcome = Outcome(notice=notice)
        pid = notice.patient_id
        try:
            outcome.triage = triage(
                notice,
                self.adapter.get_encounters(pid),
                self.adapter.get_conditions(pid),
                self.calendar,
            )
        except Exception as exc:  # noqa: BLE001 - reported per discharge, batch continues
            outcome.errors.append(f"triage: {type(exc).__name__}: {exc}")
            return outcome
        if outcome.triage.ai_draft_allowed:
            try:
                outcome.draft, outcome.draft_from_cache = self._draft(notice, outcome.triage)
            except Exception as exc:  # noqa: BLE001
                # Messages from the privacy guard carry kinds, never identifier values.
                outcome.errors.append(f"draft: {type(exc).__name__}: {exc}")
        if self.salesforce is not None and not outcome.errors:
            try:
                fields = build_case(notice, outcome.triage, outcome.draft)
                outcome.case = create_case_once(self.salesforce, fields)
            except Exception as exc:  # noqa: BLE001
                outcome.errors.append(f"salesforce: {type(exc).__name__}: {exc}")
        return outcome
