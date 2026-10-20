"""Create follow-up Cases in Salesforce (simple-salesforce, OAuth client credentials).

One Case per discharge, keyed by Idempotency_Key__c (encounter id + discharge time).
An existing Case is never updated, so RN edits are never overwritten; the unique
External ID field also makes Salesforce reject a duplicate if two runs race.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceMalformedRequest

from agent.drafter import DraftPlan
from agent.risk_explanation import explain_risk
from config import Settings
from models import DischargeNotice, MedChangeKind, RiskLevel, TaskKind, TriageResult

AI_DRAFT_MAX = 32768
PRIORITY = {RiskLevel.HIGH: "High", RiskLevel.MODERATE: "Medium", RiskLevel.LOW: "Low"}
_MED_HEADINGS = {
    MedChangeKind.STARTED: "Started",
    MedChangeKind.CONTINUED: "Continued",
    MedChangeKind.INPATIENT_ONLY: "Given in hospital only (not continued)",
}


class SalesforceNotConfiguredError(RuntimeError):
    pass


def connect(settings: Settings) -> Salesforce:
    if not settings.salesforce_configured:
        raise SalesforceNotConfiguredError("set SF_CLIENT_ID, SF_CLIENT_SECRET and SF_DOMAIN")
    assert settings.sf_client_secret is not None
    return Salesforce(
        consumer_key=settings.sf_client_id,
        consumer_secret=settings.sf_client_secret.get_secret_value(),
        domain=settings.sf_domain,
    )


# --- Case content ---------------------------------------------------------------------


def format_ai_draft(plan: DraftPlan) -> str:
    """Plain text for AI_Draft__c; deterministic sections are labelled as such."""
    lines = [plan.label, f"Model: {plan.model} | Prompt: {plan.prompt_version}", ""]
    lines += ["SUMMARY (AI)", plan.summary, ""]
    lines.append("MEDICATION CHANGES (from orders, not AI)")
    for kind, heading in _MED_HEADINGS.items():
        meds = [m for m in plan.med_changes if m.change is kind]
        if meds:
            lines.append(f"{heading}:")
            lines += [f"- {m.name}" + (f" -- {m.dosage}" if m.dosage else "") for m in meds]
    if not plan.med_changes:
        lines.append("- none found")
    if plan.review_flags:
        lines += ["", "REVIEW FLAGS (rules, not AI)"]
        lines += [f"- {f.detail}" for f in plan.review_flags]
    lines += ["", "RN CALL CHECKLIST (AI)"]
    lines += [f"{i}. {item}" for i, item in enumerate(plan.rn_call_checklist, 1)]
    text = "\n".join(lines)
    return text if len(text) <= AI_DRAFT_MAX else text[: AI_DRAFT_MAX - 15] + "\n[truncated]"


def build_case(
    notice: DischargeNotice, triage: TriageResult, draft: DraftPlan | None
) -> dict[str, Any]:
    """Case field values. No patient name or other demographics (synthetic data, but
    the Case only carries FHIR ids for lookup)."""
    first = triage.tasks[0] if triage.tasks else None
    visit = next((t for t in triage.tasks if t.kind is TaskKind.FOLLOW_UP_VISIT), None)
    risk = triage.risk_level
    tier = risk.value if risk else "not scored"
    return {
        "Subject": f"Discharge follow-up: {triage.route.value} ({tier})",
        "Description": (
            "Created by the discharge follow-up pipeline (synthetic data). See Risk "
            "Explanation and AI Draft. Nothing is sent to the patient until an RN approves."
        ),
        "Status": "New",
        "Priority": PRIORITY.get(risk, "Low") if risk else "Low",
        "Idempotency_Key__c": triage.idempotency_key,
        "Patient_FHIR_Id__c": notice.patient_id,
        "Encounter_FHIR_Id__c": notice.encounter_id,
        "Discharge_Date__c": triage.discharge_local_date.isoformat(),
        "Follow_Up_Route__c": triage.route.value,
        "Risk_Level__c": risk.value if risk else None,
        "Risk_Explanation__c": explain_risk(triage),
        "AI_Draft__c": format_ai_draft(draft) if draft else None,
        "Next_Action__c": first.kind.value if first else None,
        "Next_Action_Due__c": first.due_date.isoformat() if first else None,
        "Visit_Due_Date__c": visit.due_date.isoformat() if visit else None,
        "RN_Approved__c": False,
    }


def case_payload(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The exact body sent to Salesforce: empty fields are left out."""
    return {k: v for k, v in fields.items() if v is not None}


# --- Writing --------------------------------------------------------------------------


class _CaseApi(Protocol):
    def create(self, data: Mapping[str, Any]) -> Mapping[str, Any]: ...


class SalesforceApi(Protocol):
    """The slice of simple_salesforce.Salesforce we use (lets tests pass a fake)."""

    Case: _CaseApi

    def query(self, soql: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    created: bool  # False = a Case with this idempotency key already existed


def soql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def find_case_id(sf: SalesforceApi, idempotency_key: str) -> str | None:
    soql = f"SELECT Id FROM Case WHERE Idempotency_Key__c = {soql_string(idempotency_key)} LIMIT 1"
    records = sf.query(soql)["records"]
    return records[0]["Id"] if records else None


def create_case_once(sf: SalesforceApi, fields: Mapping[str, Any]) -> CaseResult:
    """Create the Case unless one with the same Idempotency_Key__c exists."""
    key = fields["Idempotency_Key__c"]
    existing = find_case_id(sf, key)
    if existing:
        return CaseResult(existing, created=False)
    try:
        result = sf.Case.create(case_payload(fields))
    except SalesforceMalformedRequest as exc:
        # Lost a race with another run: the unique field rejected the duplicate.
        if "DUPLICATE_VALUE" in str(exc.content):
            existing = find_case_id(sf, key)
            if existing:
                return CaseResult(existing, created=False)
        raise
    return CaseResult(result["id"], created=True)
