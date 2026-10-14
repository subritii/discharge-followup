"""Salesforce schema check, Case mapping, and idempotent creation (no network)."""

from pathlib import Path
from typing import Any

import pytest
from simple_salesforce.exceptions import SalesforceMalformedRequest

from adapters.synthea import SyntheaAdapter
from agent.draft_cache import DraftCache
from agent.drafter import LLMDraft
from models import CaseRoute, DischargeDestination
from pipeline import Pipeline
from privacy.redactor import Redactor
from salesforce.client import (
    build_case,
    create_case_once,
    find_case_id,
    format_ai_draft,
    soql_string,
)
from salesforce.schema import CASE_FIELDS, check_case_fields
from tests.conftest import ER_ID, MED_CHANGES_ID, NO_HOSPITAL_ID
from tests.episode import CALENDAR
from tests.test_drafter import GOOD, StubLLM

# --- Schema check -------------------------------------------------------------------


def describe_field(name: str, **overrides: Any) -> dict[str, Any]:
    spec = CASE_FIELDS[name]
    field: dict[str, Any] = {
        "name": name,
        "type": spec.type,
        "length": spec.length or 0,
        "unique": spec.unique,
        "caseSensitive": spec.unique,
        "externalId": spec.external_id,
        "restrictedPicklist": bool(spec.picklist),
        "picklistValues": [{"value": v, "active": True} for v in spec.picklist],
        "createable": True,
    }
    field.update(overrides)
    return field


def test_schema_ok() -> None:
    describe = {"fields": [describe_field(n) for n in CASE_FIELDS]}
    assert check_case_fields(describe) == []


def test_schema_problems_reported() -> None:
    fields = [describe_field(n) for n in CASE_FIELDS if n != "Visit_Due_Date__c"]
    fields = [
        describe_field(
            "Risk_Level__c",
            picklistValues=[{"value": v, "active": True} for v in ("high", "moderate", "ow")],
        )
        if f["name"] == "Risk_Level__c"
        else describe_field("Next_Action__c", length=20)
        if f["name"] == "Next_Action__c"
        else describe_field("AI_Draft__c", createable=False)
        if f["name"] == "AI_Draft__c"
        else f
        for f in fields
    ]
    problems = check_case_fields({"fields": fields})
    assert any("Visit_Due_Date__c: missing" in p for p in problems)
    assert any("Risk_Level__c: picklist values" in p and "'ow'" in p for p in problems)
    assert any("Next_Action__c: length 20" in p for p in problems)
    assert any("AI_Draft__c: not createable" in p for p in problems)


def test_longer_field_is_fine() -> None:
    fields = [
        describe_field(n, length=32768) if n == "Risk_Explanation__c" else describe_field(n)
        for n in CASE_FIELDS
    ]
    assert check_case_fields({"fields": fields}) == []


# --- Fake Salesforce ----------------------------------------------------------------


class FakeCaseApi:
    def __init__(self, store: dict[str, dict[str, Any]], race: bool = False) -> None:
        self.store, self.race, self.created = store, race, 0

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        key = data["Idempotency_Key__c"]
        if self.race or key in {c["Idempotency_Key__c"] for c in self.store.values()}:
            if self.race:  # another run created it between our query and our create
                self.store["500RACE"] = dict(data)
                self.race = False
            raise SalesforceMalformedRequest(
                "url", 400, "Case", [{"errorCode": "DUPLICATE_VALUE", "message": "dup"}]
            )
        case_id = f"500{len(self.store):012d}"
        self.store[case_id] = dict(data)
        self.created += 1
        return {"id": case_id, "success": True}


class FakeSalesforce:
    def __init__(self, race: bool = False) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        self.Case = FakeCaseApi(self.store, race)
        self.queries: list[str] = []

    def query(self, soql: str) -> dict[str, Any]:
        self.queries.append(soql)
        key = soql.split("= '", 1)[1].rsplit("' LIMIT", 1)[0].replace("\\'", "'")
        ids = [i for i, c in self.store.items() if c["Idempotency_Key__c"] == key]
        return {"records": [{"Id": i} for i in ids]}


def test_soql_string_escapes() -> None:
    assert soql_string("a'b\\c") == "'a\\'b\\\\c'"


def test_create_then_skip_duplicate() -> None:
    sf = FakeSalesforce()
    fields = {"Idempotency_Key__c": "enc|2025-05-22T18:20:59Z", "Subject": "x", "AI_Draft__c": None}
    first = create_case_once(sf, fields)
    second = create_case_once(sf, fields)
    assert first.created and not second.created
    assert first.case_id == second.case_id
    assert sf.Case.created == 1
    assert "AI_Draft__c" not in sf.store[first.case_id]  # None values not sent


def test_race_resolved_to_existing_case() -> None:
    sf = FakeSalesforce(race=True)
    result = create_case_once(sf, {"Idempotency_Key__c": "k", "Subject": "x"})
    assert result == type(result)("500RACE", created=False)
    assert find_case_id(sf, "k") == "500RACE"


# --- Case mapping -------------------------------------------------------------------


@pytest.fixture(scope="module")
def pipeline_parts(synthea: SyntheaAdapter, redactor: Redactor) -> dict[str, Any]:
    return {"adapter": synthea, "redactor": redactor}


def make_pipeline(parts: dict[str, Any], tmp_path: Path, sf: Any, llm: StubLLM) -> Pipeline:
    return Pipeline(
        adapter=parts["adapter"],
        calendar=CALENDAR,
        redactor=parts["redactor"],
        llm=lambda: llm,
        model_name="stub-model",
        cache=DraftCache(tmp_path / "drafts"),
        salesforce=sf,
    )


def test_med_changes_case_fields(pipeline_parts: dict[str, Any], tmp_path: Path) -> None:
    sf = FakeSalesforce()
    [outcome] = make_pipeline(pipeline_parts, tmp_path, sf, StubLLM(GOOD)).process_patient(
        MED_CHANGES_ID
    )
    assert outcome.errors == []
    assert outcome.case is not None and outcome.case.created
    case = sf.store[outcome.case.case_id]
    assert case["Subject"] == "Discharge follow-up: patient_follow_up (moderate)"
    assert case["Priority"] == "Medium"
    assert case["Status"] == "New"
    assert case["Follow_Up_Route__c"] == "patient_follow_up"
    assert case["Risk_Level__c"] == "moderate"
    assert case["Discharge_Date__c"] == "2025-05-22"
    assert case["Next_Action__c"] == "patient_contact"
    assert case["Next_Action_Due__c"] == "2025-05-27"  # Memorial Day
    assert case["Visit_Due_Date__c"] == "2025-06-05"
    assert case["RN_Approved__c"] is False
    assert case["Risk_Explanation__c"].startswith("Moderate risk. LACE 5")
    draft = case["AI_Draft__c"]
    assert draft.startswith("AI draft")
    assert "Started:\n- aspirin 81 MG Oral Tablet" in draft
    assert "REVIEW FLAGS (rules, not AI)" in draft
    assert "1. Confirm discharge destination" in draft
    # No demographics on the Case.
    patient = pipeline_parts["adapter"].get_patient(MED_CHANGES_ID)
    for value in (*patient.given_names, patient.family_name, str(patient.birth_date)):
        assert value not in str(case)


def test_rerun_uses_cache_and_skips_case(pipeline_parts: dict[str, Any], tmp_path: Path) -> None:
    sf, llm = FakeSalesforce(), StubLLM(GOOD)
    pipe = make_pipeline(pipeline_parts, tmp_path, sf, llm)
    [first] = pipe.process_patient(MED_CHANGES_ID)
    llm.messages = []
    [second] = make_pipeline(pipeline_parts, tmp_path, sf, llm).process_patient(MED_CHANGES_ID)
    assert not first.draft_from_cache and second.draft_from_cache
    assert llm.messages == []  # no LLM call on the re-run
    assert second.case is not None and not second.case.created
    assert sf.Case.created == 1


def test_expired_case_has_no_draft_or_tasks(pipeline_parts: dict[str, Any], tmp_path: Path) -> None:
    [outcome] = make_pipeline(pipeline_parts, tmp_path, None, StubLLM(GOOD)).process_patient(ER_ID)
    expired_notice = outcome.notice.model_copy(update={"destination": DischargeDestination.EXPIRED})
    from agent.triage_rules import triage

    adapter = pipeline_parts["adapter"]
    result = triage(
        expired_notice, adapter.get_encounters(ER_ID), adapter.get_conditions(ER_ID), CALENDAR
    )
    case = build_case(expired_notice, result, None)
    assert case["Follow_Up_Route__c"] == CaseRoute.CLOSE_THE_LOOP
    assert case["Risk_Level__c"] is None and case["AI_Draft__c"] is None
    assert case["Next_Action__c"] is None and case["Visit_Due_Date__c"] is None
    assert case["Subject"].endswith("(not scored)")
    assert case["Risk_Explanation__c"].startswith("Not scored: patient expired")


def test_errors_reported_per_discharge(pipeline_parts: dict[str, Any], tmp_path: Path) -> None:
    bad = StubLLM({"summary": "short", "rn_call_checklist": []})
    sf = FakeSalesforce()
    [outcome] = make_pipeline(pipeline_parts, tmp_path, sf, bad).process_patient(MED_CHANGES_ID)
    assert outcome.errors and outcome.errors[0].startswith("draft: ValidationError")
    assert outcome.case is None and sf.Case.created == 0  # no Case without its draft


def test_no_discharges_no_outcomes(pipeline_parts: dict[str, Any], tmp_path: Path) -> None:
    pipe = make_pipeline(pipeline_parts, tmp_path, FakeSalesforce(), StubLLM(GOOD))
    assert pipe.process_patient(NO_HOSPITAL_ID) == []


def test_format_ai_draft_truncates() -> None:
    from agent.drafter import DraftPlan

    long = DraftPlan(
        summary="x" * 40000,
        risk_explanation="r",
        med_changes=(),
        review_flags=(),
        rn_call_checklist=("a",),
        model="m",
        prompt_version="p@v2",
    )
    text = format_ai_draft(long)
    assert len(text) <= 32768 and text.endswith("[truncated]")
    short = long.model_copy(update={"summary": "The patient stayed one day."})
    assert "- none found" in format_ai_draft(short)


def test_llm_draft_schema_reexported() -> None:
    assert LLMDraft.model_fields.keys() == {"summary", "rn_call_checklist"}


def test_cached_draft_gets_current_deterministic_parts(
    pipeline_parts: dict[str, Any], tmp_path: Path
) -> None:
    from tests.conftest import HIGH_RISK_ER_2026_MAY, HIGH_RISK_ID

    llm = StubLLM(GOOD)
    pipe = make_pipeline(pipeline_parts, tmp_path, None, llm)
    first = next(
        o
        for o in pipe.process_patient(HIGH_RISK_ID)
        if o.notice.encounter_id == HIGH_RISK_ER_2026_MAY
    )
    assert first.draft is not None
    assert [f.id for f in first.draft.review_flags] == ["ER_OVERDOSE"]
    # Simulate a draft cached before the overdose rule existed.
    stale = first.draft.model_copy(update={"review_flags": (), "risk_explanation": "old"})
    pipe.cache.put(first.triage.idempotency_key, stale)  # type: ignore[union-attr]
    llm.messages = []
    again = next(
        o
        for o in pipe.process_patient(HIGH_RISK_ID)
        if o.notice.encounter_id == HIGH_RISK_ER_2026_MAY
    )
    assert again.draft_from_cache and llm.messages == []
    assert again.draft is not None
    assert [f.id for f in again.draft.review_flags] == ["ER_OVERDOSE"]
    assert again.draft.risk_explanation.startswith(
        "High risk (ER visit rules, not LACE): ER_OVERDOSE"
    )
    assert "ER visit for overdose" in format_ai_draft(again.draft)
