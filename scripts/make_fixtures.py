"""Trim real Synthea FHIR bundles into small test fixtures.

Keeps only the resources Phases 1-2 need:
  Patient, Encounter (started on/after --since), and the MedicationRequest,
  Medication, and Condition resources tied to the kept encounters.
Resources are copied verbatim (except Patient.text, the HTML narrative), so the
fixtures look exactly like what the adapter sees from Synthea.

Usage:
  uv run python scripts/make_fixtures.py data/synthea_raw/fhir tests/fixtures/synthea
"""

import argparse
import json
from datetime import date, datetime
from pathlib import Path

# fixture name -> Synthea patient file prefix (from the seeded run in tests/fixtures/README.md)
FIXTURES: dict[str, str] = {
    "inpatient": "Agnes294_Laree109_Davis923_",
    "er_visit": "Dwana281_Zora492_Schmeler639_",
    "med_changes": "Nichelle912_Kelsie51_Kovacek682_",
    "no_hospital": "Wilfredo622_Fritsch593_",
}


def ref_id(reference: str) -> str:
    """'urn:uuid:abc' or 'Encounter/abc' -> 'abc'."""
    return reference.rsplit(":", 1)[-1].rsplit("/", 1)[-1]


def trim_bundle(bundle: dict, since: date) -> dict:
    entries = bundle["entry"]
    by_type: dict[str, list[dict]] = {}
    for entry in entries:
        by_type.setdefault(entry["resource"]["resourceType"], []).append(entry)

    kept_encounters = [
        e
        for e in by_type.get("Encounter", [])
        if datetime.fromisoformat(e["resource"]["period"]["start"]).date() >= since
    ]
    encounter_ids = {e["resource"]["id"] for e in kept_encounters}

    def tied_to_kept_encounter(entry: dict) -> bool:
        enc = entry["resource"].get("encounter")
        return enc is not None and ref_id(enc["reference"]) in encounter_ids

    med_requests = [e for e in by_type.get("MedicationRequest", []) if tied_to_kept_encounter(e)]
    conditions = [e for e in by_type.get("Condition", []) if tied_to_kept_encounter(e)]
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
    for name, prefix in FIXTURES.items():
        matches = sorted(args.raw_dir.glob(f"{prefix}*.json"))
        if len(matches) != 1:
            raise SystemExit(f"{name}: expected 1 file for {prefix!r}, found {len(matches)}")
        trimmed = trim_bundle(json.loads(matches[0].read_text()), args.since)
        out = args.out_dir / f"{name}.json"
        out.write_text(json.dumps(trimmed, indent=1) + "\n")
        counts: dict[str, int] = {}
        for entry in trimmed["entry"]:
            rtype = entry["resource"]["resourceType"]
            counts[rtype] = counts.get(rtype, 0) + 1
        print(f"{out} ({out.stat().st_size // 1024} KB): {counts}")


if __name__ == "__main__":
    main()
