# Privacy controls for the LLM path

All data in this project is synthetic. These controls are built as if it weren't.

```
DischargeNotice + Conditions + MedicationRequests (+ free-text instructions)
        │
        ▼  1. Data minimization        privacy/payload.py   (primary control)
   LLMPayload (allowlist)
        │
        ▼  2. Presidio redaction       privacy/redactor.py  (safety net, free text only)
        │
        ▼  3. Guard                    privacy/guard.py     (last check; can't guarantee)
        │
        ▼  LLM (Phase 6)
```

Single entry point: `privacy.prepare.prepare_llm_payload(...)`.

## 1. Data minimization (primary control)

`LLMPayload` is the allowlist. It has `extra="forbid"`, and `test_payload.py` pins its
field set so any new field needs a reviewed test change.

| Field | Source | Notes |
|---|---|---|
| `encounter_class` | DischargeNotice | `IMP` or `EMER` |
| `destination` | DischargeNotice | home / snf / other / unknown |
| `length_of_stay_days` | discharge − admit | Replaces dates, which are Safe Harbor identifiers |
| `diagnoses` | Condition displays | Coded terms |
| `medications` | name + dosage text | Dosage is **free text** |
| `instructions` | discharge instructions | **Free text** |

What never enters the LLM path: names, DOB, address, phone, email, MRN, patient,
encounter and record ids, and calendar dates. `build_llm_payload` doesn't take a
`Patient` at all (a test checks its signature). Expired patients raise `NoAIDraftError`,
so no AI draft is ever produced for them.

## 2. Presidio redaction (safety net, free text only)

Only `instructions` and `medications[].dosage` are redacted. Coded fields (diagnosis and
medication names) are not, because they come from terminologies, not people.

**spaCy model: `en_core_web_lg` 3.8.0**, pinned as a wheel URL in `pyproject.toml`.
- It's Presidio's default model, so the built-in recognizer settings are tuned for it.
- It recalls PERSON and LOCATION noticeably better than `sm`/`md`, which matters most
  for a safety net.
- It runs on CPU without PyTorch. `en_core_web_trf` is more accurate but much heavier
  (torch, slower), which is out of proportion for a backstop layer behind minimization.

**Custom recognizers** added to Presidio's defaults:

| Entity | Why |
|---|---|
| `MRN` | Labelled MRNs (`MRN: A123…`) and our synthetic `MRN-########` format |
| `RECORD_ID` | UUIDs (Synthea / FHIR resource ids) |
| `STREET_ADDRESS` | Number + capitalized words + suffix. Presidio has no street recognizer. |
| `ZIP_CODE` | State + ZIP only. A bare 5-digit number may be a dose (heparin 10000 units). |
| `PHONE_NUMBER` (shape) | Presidio validates area codes. A safety net should catch any phone-shaped string. |

Address patterns are case-sensitive. Presidio defaults to case-insensitive patterns,
which matched "14 days. Contact Dr" as a street and destroyed clinical text in 65 of
200 test cases until fixed.

**DATE_TIME filtering:** only calendar dates and ages over 89 are redacted. Durations
and frequencies ("for 7 days", "twice daily", "every 8 hours") are clinical content and
are kept.

**Allow-list:** the payload's own coded diagnosis and medication names are passed to
Presidio as an allow-list. Without it, NER tags brand names such as "Brilinta" as
PERSON and removes them.

Placeholders are non-reversible (`<PERSON>`, `<PHONE_NUMBER>`, …). Nothing maps them
back, so no PHI lookup table exists to protect.

## 3. Guard (reduces risk; cannot guarantee detection)

`assert_redacted` runs two layers and raises `UnredactedIdentifierError` if either finds
anything. The pipeline sends those to the retry folder for human review.

1. **Known identifiers (deterministic).** We know this patient's identifiers, so we
   search the whole serialized payload for them:
   - name tokens, case-insensitive and whole-word, with and without Synthea's digit
     suffixes
   - DOB in 8 common formats
   - MRN, patient id, encounter ids
   - address lines, city, postal code
   - phone, compared on digits so any formatting matches
2. **Presidio re-scan** of the redacted free-text fields. Anything still flagged
   (ignoring our own `<PLACEHOLDER>` tokens) fails.

Error messages and `findings` contain only the kind and field (e.g. `known:name`,
`presidio:instructions:PERSON`), never the value. Logs and the retry folder don't
receive PHI from the guard.

### What it can miss

- **Other people's identifiers that NER doesn't recognize.** Layer 1 only knows this
  patient. A family member or clinician written in lowercase, misspelled, or with no
  context may pass both layers.
- **Formats we don't normalize:** DOB as words ("the fifteenth of January"), partial
  dates, nicknames ("Bill" for William), initials.
- **Bare city names without context.** NER missed "Lowell" mid-sentence in testing.
  Layer 1 catches the patient's own city only.
  Under HIPAA Safe Harbor, geographic units smaller than a state (e.g. city) are
  identifiers, so bare city names in free text are a known gap.
- **Names under 3 characters** are skipped by layer 1, because they'd match ordinary
  words.
- **Names that are common words** ("May", "Hope") make layer 1 fail closed on ordinary
  text. That's safe but noisy: expect occasional false trips sent for human review.
- **Synthea's digit-suffixed names** ("Nichelle912") aren't recognized by NER at all.
  Layer 1 catches them (see `test_guard_blocks_synthea_style_name_that_ner_misses`).
  Real names don't have this problem, but it shows why both layers exist.

## Measured on seeded fake identifiers

`tests/fake_identifiers.py`, seed `20261013`, 200 cases, en_core_web_lg 3.8.0:

| Kind | Removed |
|---|---|
| Patient name | 79 / 79 |
| Clinician name | 143 / 143 |
| MRN | 78 / 78 |
| Calendar date | 78 / 78 |
| Email | 65 / 65 |
| SSN | 65 / 65 |
| Phone | 57 / 57 (55 / 57 before the shape recognizer) |
| Street address | 57 / 57 |
| State + ZIP | 57 / 57 |
| Clinical phrases lost | 0 / 200 (65 / 200 before the case-sensitivity fix) |

These cases come from three templates, so **the rates are optimistic** compared with
real clinical notes. The tests use them to catch regressions (pattern kinds must be
100%, NER kinds ≥ 95%), not as evidence of real-world recall.
