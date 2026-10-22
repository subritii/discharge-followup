# Build Plan

Each phase is testable on its own. Phase numbers are kept from the original proposal.

## Version 1 ("junior version"): fixtures -> AI draft -> Salesforce Case
| Phase | Scope | Status |
|---|---|---|
| 0 | Scaffold: pyproject, config.py, .env.example, pytest | done |
| 1 | Domain models, adapter protocol, SyntheaAdapter, factory | done |
| 2 | FHIR discharge detection (class IMP/EMER + finished + period.end), ER->IMP merge, destination | done |
| 3 | Data minimization (clinical-only LLM payload) + Presidio safety net + guard | done |
| 5 | LACE-based triage rules (illustrative, pending clinical review) + deadlines | done |
| 6 | One Ollama call (llama3.1:8b) -> stay summary + RN checklist; deterministic med diff, risk explanation and review flags | done |
| 8 | Salesforce Case via simple-salesforce (client credentials), AI draft + due dates in custom fields, skip duplicates by idempotency key | done |
| run | `run.py`: process fixtures end to end, cache drafts in data/drafts/, `--model` override, print a summary | done |
| smoke | `scripts/athena_smoke_test.py`: preview token + 3 FHIR encounters for practice 195900 | done: REST fallback works (33 departments); FHIR mode waits on system/*.read scopes |
| docs | README: problem, diagram, how to run, limitations, Version 2 list | done |

## Version 2 (deferred)
| Item | Scope |
|---|---|
| Judge (Phase 7) | Second model (qwen2.5:7b) checks the draft against the payload before the Case is created |
| Drug-interaction checks | e.g. antiplatelet switches (ticagrelor in hospital -> prasugrel at discharge), duplicate therapy, anticoagulant + antiplatelet. Today only the "started as needed" flag exists |
| Med stop events | Hand-made fixtures with `status: stopped` / dose changes so stopped/changed meds can be detected (Synthea has none) |
| PDF intake (Phase 2b) | pdfplumber; OCR later |
| Patient matching (Phase 4) | `matching/`; synthetic MRNs independent of patient UUIDs |
| Athena adapter (Phase 11) | FHIR R4 sandbox adapter + shared adapter contract tests; NUBC codes in disposition_map.yaml |
| FastAPI (Phase 9) | HTTP endpoints for notices |
| Evals (Phase 12) | Labeled synthetic discharges; triage accuracy, med-change precision/recall, redaction leak rate |
| Salesforce Flow (Phase 10) | Community__c -> Queue routing, Tasks, Field History Tracking on RN_Approved__c; export metadata |
| Left-against-advice rule | Separate routing for `aadvice` dispositions |
| Broader ER rule review | Clinical review of the ER-only rules (today: frequent use, comorbidity, overdose); e.g. repeat overdoses, falls in older adults, chest pain |
| Retry folder | Failed notices written to RETRY_DIR for reprocessing |

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
- **Expired:** disposition `exp`, or deceasedDateTime within the stay or up to 3 days
  after discharge, gives `expired` (refined in Phase 5). `DischargeNotice.requires_patient_follow_up` is
  False: no patient-call tasks and no AI drafts.
- **Phase 5:** expired -> informational "close the loop" case for the care team lead.
  SNF -> facility coordination instead of the 2-day patient call (Phase 5 deadlines /
  Phase 10 Flow).
- **Phase 11:** NUBC patient discharge status codes (e.g. `01` home, `03` SNF,
  `20` expired) get added to `library/disposition_map.yaml` as a new system. Config
  only, no code change (covered by `test_new_system_is_config_only`).

### Phase 6: AI draft (done)
- The model writes only the stay summary and the RN checklist. It never sees the risk
  tier, LACE or Charlson (removed from its input, including task wording), and the
  prompt says not to discuss risk.
- Deterministic parts of the draft: med list (`agent/med_diff.py`), risk explanation
  (`agent/risk_explanation.py`), review flags (`agent/review_flags.py`: newly started
  meds with "as needed" instructions).
- llama3.1:8b on an 8 GB M1: ~5-8 min per draft (swapping). `run.py` caches drafts in
  `data/drafts/` keyed by idempotency key + model + prompt version.
- Prompt v3 (after reviewing the v2 demo drafts): every ER draft said "hospitalized",
  a 15-hour ER visit became "6 days" (it read `length_of_stay_days: 0.6`), and ER
  checklists invented "a follow-up visit within 3 business days". Fixes: the payload
  now says `visit_type: "ER visit (not admitted)"` and `length_of_stay: "15 hours"`
  instead of codes and decimals, and the prompt allows a follow-up visit item only when
  TRIAGE has a `follow_up_visit` task.
- Prompt v4: v3 fixed the visit type and length, but ER checklists still turned the
  outreach call into "a follow-up visit" and quoted internal task names. Tasks and
  deadlines are now removed from the model's input entirely, like risk: they live on
  the Case as rule-based fields, and the prompt forbids scheduling items.

### Phase 3: privacy (done)
- See `docs/privacy.md`: allowlisted `LLMPayload`, Presidio on free text only
  (en_core_web_lg 3.8.0), two-layer guard with documented gaps.
- Phase 6 must call `privacy.prepare.prepare_llm_payload` and nothing else to build
  LLM input. Guard failures go to the retry folder (Milestone A CLI).

### Phase 5: triage and routing (done)
- Rules: `agent/triage_rules.py` + `library/triage_rules.yaml`, `charlson_map.yaml`,
  `holidays.yaml`; reviewer summary in `library/guides/triage.md`.
- Choices made where the spec was silent (all configurable in YAML):
  - unknown / other destinations -> patient follow-up + `DESTINATION_UNVERIFIED`
    (other includes left-against-advice, so a call attempt is the safer default)
  - low-risk LACE visit deadline = 14 days (same as moderate)
  - facility coordination due in 2 business days; ER outreach in 3
  - ER visit with destination snf -> facility coordination
  - A (acuity) = 3 only when the admission came through a linked ER visit
- Expired = disposition `exp`, or deceasedDateTime between episode start (ER arrival for
  merged stays) and 3 days after discharge. deceasedBoolean without a date doesn't count.
  On the full 49-patient run this gives 3 close-the-loop discharges (was 190 when any
  deceased flag counted).
- Synthea rarely produces high LACE: 6 of 138 scored inpatient discharges in the
  49-patient run (fixture `high_risk` is one). High-risk paths are mostly covered by
  constructed tests.
- ER_OVERDOSE (added after the first full run): an ER visit with an overdose/poisoning
  diagnosis is high risk and gets an RN review flag. Cases are never updated, so Cases
  created before this rule (e.g. the three overdose ER visits in `high_risk`, created
  as low risk) keep their original values; only new runs use the rule.
- Holidays cover 2023-2027.
- Phase 10: expired -> close-the-loop case for the care team lead; SNF -> facility
  coordination queue.

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
