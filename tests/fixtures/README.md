# Test fixtures

All data is synthetic. Nothing here is real PHI.

## `synthea/`

Trimmed from real Synthea output, not hand-written, so the adapter is tested against
the exact FHIR shape Synthea produces.

**Source run**

| | |
|---|---|
| Synthea version | **v4.0.0** (GitHub release `v4.0.0`, `synthea-with-dependencies.jar`) |
| Jar SHA-256 | `ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1` |
| Java | 26 |
| Generated | 2026-10-13 |

```bash
java -jar synthea-with-dependencies.jar -s 42 -cs 42 -p 30 -a 55-90 \
  --exporter.baseDirectory=data/synthea_raw \
  --exporter.fhir.export=true \
  --exporter.hospital.fhir.export=false \
  --exporter.practitioner.fhir.export=false \
  --exporter.csv.export=false \
  --exporter.years_of_history=5 \
  Massachusetts

uv run python scripts/make_fixtures.py data/synthea_raw/fhir tests/fixtures/synthea
```

**Trimming** (`scripts/make_fixtures.py`): keeps Patient, Encounters starting on or
after 2024-01-01, and the MedicationRequest / Medication / Condition resources tied to
those encounters. Resources are copied verbatim, except `Patient.text` (the HTML
narrative), which is dropped. The bundle stays a Synthea `transaction` bundle with
`urn:uuid:` references.

**Fixtures**

| File | Patient | What it covers |
|---|---|---|
| `inpatient.json` | Agnes294 Davis923 | 23-day IMP stay, discharged 2026-02-10 to a skilled nursing facility |
| `er_visit.json` | Dwana281 Schmeler639 | one EMER visit (2025-07-05), no inpatient stay |
| `med_changes.json` | Nichelle912 Kovacek682 | EMER visit that becomes an IMP admission (overlapping periods), 2025-05-21/22; aspirin, metoprolol, atorvastatin, sacubitril/valsartan and prasugrel started; inpatient-only meds use `medicationReference` |
| `no_hospital.json` | Wilfredo622 Fritsch593 | ambulatory encounters and med orders only, with no IMP/EMER (negative case) |

**Quirks of Synthea data worth knowing**

- The `MR` identifier value equals `Patient.id`, so MRN matching can't be tested
  meaningfully on Synthea alone.
- Encounters have no `hospitalization` element (no `dischargeDisposition`).
- MedicationRequests have `authoredOn` and `status` (`active` / `completed`) but no
  end date. `status` is the current status, not the status at a given point in time.
- Some MedicationRequests use `medicationReference` -> `Medication` instead of
  `medicationCodeableConcept`.
- An ER visit that leads to admission appears as two overlapping encounters (EMER, then IMP).
