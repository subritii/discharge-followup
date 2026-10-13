"""Discharge detection against the real (trimmed) Synthea fixtures."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from adapters.synthea import SyntheaAdapter
from detector.discharge import DISPOSITION_SYSTEM, detect_for_patient
from models import DischargeDestination
from tests.conftest import (
    ER_EMER_ENCOUNTER,
    ER_ID,
    INPATIENT_ID,
    INPATIENT_IMP_ENCOUNTER,
    MED_CHANGES_EMER_ENCOUNTER,
    MED_CHANGES_ID,
    MED_CHANGES_IMP_ENCOUNTER,
    NO_HOSPITAL_ID,
    SYNTHEA_FIXTURES,
)


def test_inpatient_detected_with_unknown_destination(synthea: SyntheaAdapter) -> None:
    [notice] = detect_for_patient(synthea, INPATIENT_ID)
    assert notice.encounter_id == INPATIENT_IMP_ENCOUNTER
    assert notice.encounter_class == "IMP"
    assert notice.discharge_time == datetime(2026, 2, 10, 15, 4, 51, tzinfo=UTC)
    # Synthea emits no dischargeDisposition, even though this stay's reasonCode says
    # "transfer to skilled nursing facility". We don't infer from reasonCode.
    assert notice.destination == DischargeDestination.UNKNOWN


def test_er_only_detected(synthea: SyntheaAdapter) -> None:
    [notice] = detect_for_patient(synthea, ER_ID)
    assert notice.encounter_id == ER_EMER_ENCOUNTER
    assert notice.encounter_class == "EMER"
    assert notice.linked_er_encounter_id is None


def test_er_then_admit_merged(synthea: SyntheaAdapter) -> None:
    [notice] = detect_for_patient(synthea, MED_CHANGES_ID)
    imp = synthea.get_encounter(MED_CHANGES_IMP_ENCOUNTER)
    assert notice.encounter_id == MED_CHANGES_IMP_ENCOUNTER
    assert notice.linked_er_encounter_id == MED_CHANGES_EMER_ENCOUNTER
    assert notice.idempotency_key == imp.idempotency_key


def test_no_hospital_has_no_discharges(synthea: SyntheaAdapter) -> None:
    assert detect_for_patient(synthea, NO_HOSPITAL_ID) == []


def test_discharged_since_filter(synthea: SyntheaAdapter) -> None:
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    assert detect_for_patient(synthea, INPATIENT_ID, discharged_since=cutoff)
    assert detect_for_patient(synthea, ER_ID, discharged_since=cutoff) == []


def test_since_filter_applies_after_merge(synthea: SyntheaAdapter) -> None:
    # Cutoff between the ER discharge and the IMP discharge: the merged notice survives
    # and still carries the ER link.
    er = synthea.get_encounter(MED_CHANGES_EMER_ENCOUNTER)
    assert er.discharge_time is not None
    [notice] = detect_for_patient(
        synthea, MED_CHANGES_ID, discharged_since=er.discharge_time + timedelta(minutes=1)
    )
    assert notice.linked_er_encounter_id == MED_CHANGES_EMER_ENCOUNTER


def test_disposition_read_from_real_bundle_shape(tmp_path: Path) -> None:
    # Take the real inpatient bundle and add the hospitalization element athena-style
    # sources include, to prove the parse path works on Synthea's resource shape.
    bundle = json.loads((SYNTHEA_FIXTURES / "inpatient.json").read_text())
    patched = copy.deepcopy(bundle)
    for entry in patched["entry"]:
        if entry["resource"]["id"] == INPATIENT_IMP_ENCOUNTER:
            entry["resource"]["hospitalization"] = {
                "dischargeDisposition": {
                    "coding": [{"system": DISPOSITION_SYSTEM, "code": "snf"}],
                    "text": "Skilled nursing facility",
                }
            }
    (tmp_path / "inpatient.json").write_text(json.dumps(patched))

    [notice] = detect_for_patient(SyntheaAdapter(tmp_path), INPATIENT_ID)
    assert notice.destination == DischargeDestination.SNF
