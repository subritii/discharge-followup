# Medication changes: definitions

Computed deterministically in `agent/med_diff.py`. The LLM receives this list and may
describe it, but the med list in the draft always comes from this code, never from
the model.

| Change | Rule |
|---|---|
| **started** | Ordered during the episode (on the admission or linked ER encounter, or authored inside the window) and `active` |
| **continued** | `active` order authored before the episode and not re-ordered during it |
| **inpatient_only** | Ordered during the episode but no longer active (given in hospital only) |

**Episode** = ER arrival (when the admission came through the ER) or admission time,
through discharge time.

Orders with status `entered-in-error` or `draft` are ignored. Each (name, dosage) appears
once. When a med is both continued and re-ordered during the stay, it's listed as started.

## Known limitations

- **No stop events in Synthea.** MedicationRequests have no end date, and `status` is
  the status today, not at discharge. So **"stopped at discharge" and dose changes are
  not detected**. A pre-admission med that was actually stopped still shows as
  continued if its order is `active`.
- "continued" relies on the current `status`. An order that was active at admission but
  completed later is not listed.
- Same drug at a different strength (e.g. aspirin 325 MG in the ER, then aspirin 81 MG at
  discharge) shows as two lines, inpatient_only and started. It is not shown as a dose
  change.

Version 2: hand-made fixtures with stop events (`status: stopped` with a stop date inside
the stay, a dose change as stop + new order) so the stopped/changed path can be built
and tested.
