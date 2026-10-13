# Build Plan

Each phase is testable on its own. Phase numbers are kept from the original proposal.

## Milestone A: offline end-to-end (FHIR -> Salesforce Case via CLI)
| Phase | Scope | Status |
|---|---|---|
| 0 | Scaffold: pyproject, config.py, .env.example, pytest | done |
| 1 | Domain models, adapter protocol, SyntheaAdapter, factory | |
| 2 | FHIR discharge detection (class IMP/EMER + finished + period.end) | |
| 3 | Data minimization (clinical-only LLM payload) + Presidio safety net + guard | |
| 5 | LACE-based triage rules (illustrative, pending clinical review) + deadlines | |
| 6 | Agent on Ollama llama3.1:8b; deterministic med diff | |
| 8 | Salesforce Case client + idempotency (encounter id + discharge time) | |
| A | CLI pipeline (`python -m cli run <bundle>`), retry folder for failures | |

## Milestone B: routing, real data source, quality gate
| Phase | Scope |
|---|---|
| 10 | Salesforce Flow (built in Flow Builder): Community__c -> Queue, 2d/7d Tasks; export metadata; Field History Tracking on RN_Approved__c |
| 11 | AthenaAdapter (FHIR R4 sandbox) + shared adapter contract tests |
| 7 | LLM judge on qwen2.5:7b |
| — | Dedup hardening (repeated / updated notices for the same encounter) |

## Milestone C: breadth and polish
| Phase | Scope |
|---|---|
| 2b | PDF intake (pdfplumber; OCR later) |
| 4 | Patient matching (`matching/`) |
| 9 | FastAPI endpoints |
| 12 | Evals + docs (architecture, safety, README) |

## Definitions to document when built
- **Meds before vs after** (Phase 6): to be defined and documented in
  `library/guides/med_changes.md` (which MedicationRequests count as pre-admission vs
  discharge, and how status/authoredOn are used).
