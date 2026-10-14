# Triage and routing rules (for clinical review)

**Status: ILLUSTRATIVE, pending clinical review.** Explicit rules decide risk, route and
deadlines. The LLM only explains the result. Configuration lives in
`library/triage_rules.yaml`, `library/charlson_map.yaml` and `library/holidays.yaml`.

## Which pathway

| Discharge | Pathway | Risk method |
|---|---|---|
| Inpatient stay (incl. ER visit that led to admission) | inpatient | LACE index |
| Standalone ER visit | er | Simple rules (not LACE) |

## LACE (inpatient only)

| Component | Points | How we compute it |
|---|---|---|
| **L** length of stay | <1d 0, 1-3d = days, 4-6d 4, 7-13d 5, ≥14d 7 | Admission to discharge, calendar days in the practice time zone |
| **A** acuity | emergent 3 | Emergent = admitted through a linked ER visit (no other acuity data in FHIR) |
| **C** comorbidity | Charlson 0-3 = score, ≥4 → 5 | SNOMED conditions active during the stay, via `charlson_map.yaml` |
| **E** ED visits | prior 6 months, capped at 4 | EMER encounters in the 182 days before the episode; the index ER visit is excluded |

LACE ≥ 10 → high, 5-9 → moderate, 0-4 → low.

## ER-only rules

Any rule firing → high, otherwise low:
- `ER_FREQUENT_USE`: ≥ 2 prior ED visits in 182 days
- `ER_COMORBIDITY`: Charlson ≥ 2
- `ER_OVERDOSE`: the ER visit's reason or diagnoses include overdose / poisoning
  (SNOMED list in `triage_rules.yaml`, or "overdose"/"poisoning" in the text). Also adds
  the review flag "ER visit for overdose — RN review".

## Routing and deadlines

| Destination | Route | Tasks |
|---|---|---|
| home / unknown / other (inpatient) | patient follow-up | contact within 2 business days; visit within 7 days (high) or 14 (moderate, low) |
| snf | facility coordination | facility coordination within 2 business days; **no patient call** |
| expired (disposition `exp`, or death during the stay / within 3 days after) | close the loop | **no tasks, no AI draft**; informational case for the care team lead (not configurable) |
| deceased flag with no date (`deceasedBoolean` only) | deceased status review | one RN review task (1 business day), **no patient contact, no AI draft**; flagged "Deceased status reported, date unknown — confirm before contacting" (not configurable) |
| ER-only (home / unknown / other) | ER outreach | outreach within 3 business days (configurable) |
| ER-only, snf | facility coordination | as snf |

- Business days are counted in the practice time zone (`PRACTICE_TIMEZONE`, default
  America/Chicago), skipping weekends and listed holidays. Day 1 is the first business
  day after the discharge date.
- Visit deadlines are calendar days. If the last day is a weekend or holiday, the due
  date moves **earlier** so the window is never exceeded.
- `unknown` and `other` destinations fire `DESTINATION_UNVERIFIED`: the RN confirms
  before calling.
- Due dates outside the holiday list's years (currently 2023-2027) fire
  `HOLIDAY_CALENDAR_MISSING`.

Every result lists its fired rules and the LACE components, so the RN can see why.
