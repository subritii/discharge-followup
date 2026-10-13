"""Trim real Synthea FHIR bundles into small test fixtures.

Keeps only the resources Phases 1-5 need:
  Patient; hospital Encounters (non-AMB) started on/after --since; MedicationRequests
  (and their Medications) from recent encounters; Conditions from recent encounters
  or still active on/after --since (chronic disorders, for Charlson); and every
  encounter those conditions/meds reference, so all references resolve. Unreferenced
  ambulatory encounters are dropped.
Resources are copied verbatim (except Patient.text, the HTML narrative), so the
fixtures look exactly like what the adapter sees from Synthea.

Usage:
  uv run python scripts/make_fixtures.py data/synthea_raw/fhir tests/fixtures/synthea
"""

import argparse
import json
from datetime import date, datetime
from pathlib import Path

# fixture name -> (Synthea patient file prefix, cutoff override or None for --since)
# Patients come from the seeded run described in tests/fixtures/README.md.
FIXTURES: dict[str, tuple[str, date | None]] = {
    "inpatient": ("Agnes294_Laree109_Davis923_", None),
    "er_visit": ("Dwana281_Zora492_Schmeler639_", None),
    "med_changes": ("Nichelle912_Kelsie51_Kovacek682_", None),
    "no_hospital": ("Wilfredo622_Fritsch593_", None),
    # A LACE 11 stay (Dec 2023) with an ER-linked admission and a prior ED visit (Aug 2023).
    "high_risk": ("Easter642_Emmie273_Cassin499_", date(2023, 6, 1)),
}


def ref_id(reference: str) -> str:
    """'urn:uuid:abc' or 'Encounter/abc' -> 'abc'."""
    return reference.rsplit(":", 1)[-1].rsplit("/", 1)[-1]


def trim_bundle(bundle: dict, since: date) -> dict:
    entries = bundle["entry"]
    by_type: dict[str, list[dict]] = {}
    for entry in entries:
        by_type.setdefault(entry["resource"]["resourceType"], []).append(entry)

    def on_or_after_since(timestamp: str) -> bool:
        return datetime.fromisoformat(timestamp).date() >= since

    def encounter_ref(entry: dict) -> str | None:
        enc = entry["resource"].get("encounter")
        return ref_id(enc["reference"]) if enc else None

    recent_ids = {
        e["resource"]["id"]
        for e in by_type.get("Encounter", [])
        if on_or_after_since(e["resource"]["period"]["start"])
    }

    def still_active_disorder(entry: dict) -> bool:
        resource = entry["resource"]
        display = resource["code"]["coding"][0].get("display", "")
        abated = resource.get("abatementDateTime")
        return display.endswith("(disorder)") and (abated is None or on_or_after_since(abated))

    # Conditions: those from recent encounters, plus older disorders still active on/after
    # `since` (chronic comorbidities that Charlson scoring needs; findings are skipped).
    conditions = [
        e
        for e in by_type.get("Condition", [])
        if encounter_ref(e) in recent_ids or still_active_disorder(e)
    ]
    med_requests = [
        e for e in by_type.get("MedicationRequest", []) if encounter_ref(e) in recent_ids
    ]
    referenced = {encounter_ref(e) for e in [*conditions, *med_requests]}

    def keep_encounter(entry: dict) -> bool:
        resource = entry["resource"]
        if resource["id"] in referenced:
            return True  # includes older encounters behind chronic conditions
        # Recent hospital encounters always; recent ambulatory ones only if referenced.
        return resource["id"] in recent_ids and resource["class"]["code"] != "AMB"

    kept_encounters = [e for e in by_type.get("Encounter", []) if keep_encounter(e)]
    med_ids = {
        ref_id(e["resource"]["medicationReference"]["reference"])
        for e in med_requests
        if "medicationReference" in e["resource"]
    }
    medications = [e for e in by_type.get("Medication", []) if e["resource"]["id"] in med_ids]

    patient = by_type["Patient"][0]
    patient["resource"].pop("text", None)

    kept = [patient, *kept_encounters, *conditions, *medications, *med_requests]
    return {**bundle, "entry": kept}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--since", type=date.fromisoformat, default=date(2024, 1, 1))
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, (prefix, since) in FIXTURES.items():
        matches = sorted(args.raw_dir.glob(f"{prefix}*.json"))
        if len(matches) != 1:
            raise SystemExit(f"{name}: expected 1 file for {prefix!r}, found {len(matches)}")
        trimmed = trim_bundle(json.loads(matches[0].read_text()), since or args.since)
        out = args.out_dir / f"{name}.json"
        out.write_text(json.dumps(trimmed, indent=1) + "\n")
        counts: dict[str, int] = {}
        for entry in trimmed["entry"]:
            rtype = entry["resource"]["resourceType"]
            counts[rtype] = counts.get(rtype, 0) + 1
        print(f"{out} ({out.stat().st_size // 1024} KB): {counts}")


if __name__ == "__main__":
    main()
