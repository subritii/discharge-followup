# Build Plan

Each phase is testable on its own. Phase numbers are kept from the original proposal.

## Milestone A: offline end-to-end (FHIR -> Salesforce Case via CLI)
| Phase | Scope | Status |
|---|---|---|
| 0 | Scaffold: pyproject, config.py, .env.example, pytest | done |
| 1 | Domain models, adapter protocol, SyntheaAdapter, factory | done |
| 2 | FHIR discharge detection (class IMP/EMER + finished + period.end), ER->IMP merge, destination | done |
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

## Phase notes

### Phase 2: discharge detection (done)
- Detection = class IMP/EMER + status `finished` + `period.end`. dischargeDisposition
  is never required.
- ER->IMP merge: an IMP that starts during an ER visit or within
  `ER_ADMIT_MERGE_WINDOW_HOURS` (default 6) after it ends is one episode, keyed on the
  IMP encounter + its discharge time, with `linked_er_encounter_id` kept. Each ER visit
  links to at most one admission; standalone ER visits stay separate.
- Destination (home / snf / other / expired / unknown) comes from
  `library/disposition_map.yaml` (HL7 discharge-disposition codes today). Missing or
  unrecognized -> unknown. Synthea never emits a disposition, so every living Synthea
  patient's discharge is `unknown`. We do not infer it from reasonCode.
- **Expired:** disposition `exp`, or `Patient.deceased` true (deceasedDateTime /
  deceasedBoolean), gives `expired`. `DischargeNotice.requires_patient_follow_up` is
  False: no patient-call tasks and no AI drafts.
- **Phase 5:** expired -> informational "close the loop" case for the care team lead.
  SNF -> facility coordination instead of the 2-day patient call (Phase 5 deadlines /
  Phase 10 Flow).
- **Phase 11:** NUBC patient discharge status codes (e.g. `01` home, `03` SNF,
  `20` expired) get added to `library/disposition_map.yaml` as a new system. Config
  only, no code change (covered by `test_new_system_is_config_only`).

### Phase 4: patient matching
- Synthea's MR identifier equals `Patient.id`, so matching on it proves nothing.
  Generate synthetic MRNs that are independent of patient UUIDs (e.g. seeded
  `MRN-` + 8 digits, stored as a separate identifier in derived fixtures) and test
  matching against those. Include near-miss cases: transposed digits, same name + DOB
  with a different MRN.

### Phase 6: medication changes
- **Known limitation:** Synthea has no medication stop events. MedicationRequests have
  no end date, and `status` is the status today, not at discharge. From Synthea data we
  can only reliably detect **meds started during the stay** (requests on the IMP
  encounter, or authored inside the admission window, that are still active).
- Stopped / changed meds (dose change, discontinued at discharge) can't be derived from
  Synthea. Document this in `library/guides/med_changes.md`.
- Add **hand-made fixtures with stop events** (e.g. `status: stopped` with a
  discontinue date inside the stay, a dose change as stop + new request), clearly
  labelled as hand-made in `tests/fixtures/README.md`, so the stopped/changed path is
  tested.
- Define "before" vs "after" in `library/guides/med_changes.md`. Draft, to finalize in
  Phase 6: before = requests authored before admit_time and not stopped before it;
  after = before, minus anything stopped during the stay, plus requests authored during
  the stay. Because Synthea's `status` is current rather than as of discharge, "not
  stopped" can only use explicit stop dates (hand-made fixtures). For Synthea it falls
  back to authoredOn alone.
