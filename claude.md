# Discharge Follow-Up Automation

## Goal
When a hospital/ER discharge notice arrives, create a Salesforce case for the
right RN care manager with an AI-drafted follow-up plan and 2-day / 7-day deadlines.
Portfolio project for an AI Automation Workflow Specialist role (athenahealth + Salesforce shop).

## Flow
notice (FHIR Encounter or PDF) -> extract -> minimize + redact (Presidio) -> match patient
-> AI agent (summarize, med changes, triage, draft call plan) -> LLM judge
-> idempotency check -> Salesforce Case (simple-salesforce)
-> Salesforce Flow routes to community Queue + creates tasks -> RN approves

Build order and phase details: docs/plan.md.

## Rules
- Synthetic data ONLY (Synthea / athenahealth sandbox). Never real PHI.
- All secrets in .env, never committed. Provide .env.example.
- Data minimization: only clinical fields go to the LLM. No names, DOB, MRN, or
  contact details in the LLM path. Presidio redaction of free text is a safety net,
  and assert_redacted is a guard, not a guarantee.
- Discharge detection: Encounter class IMP or EMER + status finished + period.end set.
  Do not rely on dischargeDisposition.
- Triage = explicit rules first (LACE index, labeled illustrative / pending clinical
  review), LLM explanation second.
- Idempotency: key = encounter id + discharge time. Check Salesforce for an existing
  Case with that key before creating one.
- Failures are written to the retry folder (RETRY_DIR) for reprocessing.
- AI never contacts patients; RN approval required (RN_Approved__c, Field History Tracking on).
- Python 3.11, FastAPI, pytest. Small functions, type hints, tests for each module.
- One data adapter interface: SyntheaAdapter and AthenaAdapter (FHIR R4), switched by config.

## Salesforce
- Case.Community__c + one Queue per community; the Flow routes on Community__c.
- The Flow is built by hand in Flow Builder, then exported into salesforce/metadata/.

## Model
- Local only via Ollama: llama3.1:8b (agent), qwen2.5:7b (judge). No paid APIs.
- Use ChatOllama from langchain-ollama. Model names come from .env.

## Folders
adapters/ detector/ privacy/ matching/ agent/ salesforce/ evals/ docs/ tests/
library/ = prompts, rules (e.g. LACE), and guides, stored as data rather than code.
