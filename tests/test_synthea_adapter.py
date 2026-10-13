from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from adapters.base import NotFoundError, PatientDataAdapter
from adapters.synthea import SyntheaAdapter
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


def hospital_encounters(adapter: SyntheaAdapter, patient_id: str) -> dict[str, str]:
    return {e.id: e.class_code for e in adapter.get_encounters(patient_id) if e.is_hospital_class}


def test_implements_protocol(synthea: SyntheaAdapter) -> None:
    assert isinstance(synthea, PatientDataAdapter)


def test_patient_fields(synthea: SyntheaAdapter) -> None:
    p = synthea.get_patient(INPATIENT_ID)
    assert p.family_name == "Davis923"
    assert p.given_names == ("Agnes294", "Laree109")
    assert p.birth_date == date(1961, 1, 15)
    assert p.mrn == INPATIENT_ID  # Synthea uses the patient UUID as the MR identifier
    assert p.address is not None and p.address.city == "Grafton"


def test_inpatient_stay(synthea: SyntheaAdapter) -> None:
    assert hospital_encounters(synthea, INPATIENT_ID) == {INPATIENT_IMP_ENCOUNTER: "IMP"}
    enc = synthea.get_encounter(INPATIENT_IMP_ENCOUNTER)
    assert enc.patient_id == INPATIENT_ID
    assert enc.status == "finished"
    assert enc.discharge_time == datetime(2026, 2, 10, 15, 4, 51, tzinfo=UTC)
    assert enc.idempotency_key == f"{INPATIENT_IMP_ENCOUNTER}|2026-02-10T15:04:51Z"


def test_er_visit_only(synthea: SyntheaAdapter) -> None:
    assert hospital_encounters(synthea, ER_ID) == {ER_EMER_ENCOUNTER: "EMER"}
    enc = synthea.get_encounter(ER_EMER_ENCOUNTER)
    assert enc.status == "finished"
    assert enc.discharge_time is not None


def test_er_then_admitted_has_both_encounters(synthea: SyntheaAdapter) -> None:
    # Real Synthea pattern: ER visit overlaps the inpatient admission it led to.
    assert hospital_encounters(synthea, MED_CHANGES_ID) == {
        MED_CHANGES_EMER_ENCOUNTER: "EMER",
        MED_CHANGES_IMP_ENCOUNTER: "IMP",
    }
    er = synthea.get_encounter(MED_CHANGES_EMER_ENCOUNTER)
    imp = synthea.get_encounter(MED_CHANGES_IMP_ENCOUNTER)
    assert er.period_start < imp.period_start < er.period_end  # type: ignore[operator]


def test_medication_changes_around_stay(synthea: SyntheaAdapter) -> None:
    meds = synthea.get_medication_requests(MED_CHANGES_ID)
    on_stay = [m for m in meds if m.encounter_id == MED_CHANGES_IMP_ENCOUNTER]
    started = {m.medication.display for m in on_stay if m.status == "active"}
    inpatient_only = {m.medication.display for m in on_stay if m.status == "completed"}
    assert "aspirin 81 MG Oral Tablet" in started
    assert any("metoprolol" in name for name in started)  # type: ignore[operator]
    # These come via medicationReference -> Medication, so resolution must work.
    assert any("bivalirudin" in name for name in inpatient_only)  # type: ignore[operator]
    assert all(m.medication.code for m in meds)


def test_no_hospital_encounters(synthea: SyntheaAdapter) -> None:
    encounters = synthea.get_encounters(NO_HOSPITAL_ID)
    assert encounters, "negative case should still have ambulatory encounters"
    assert hospital_encounters(synthea, NO_HOSPITAL_ID) == {}
    assert synthea.get_medication_requests(NO_HOSPITAL_ID)


def test_encounter_ids_unique_and_stable() -> None:
    first = SyntheaAdapter(SYNTHEA_FIXTURES)
    second = SyntheaAdapter(SYNTHEA_FIXTURES)
    for pid in (INPATIENT_ID, ER_ID, MED_CHANGES_ID, NO_HOSPITAL_ID):
        ids_a = [e.id for e in first.get_encounters(pid)]
        ids_b = [e.id for e in second.get_encounters(pid)]
        assert ids_a == ids_b
        assert len(ids_a) == len(set(ids_a))
        assert [e.idempotency_key for e in first.get_encounters(pid)] == [
            e.idempotency_key for e in second.get_encounters(pid)
        ]


def test_all_finished_encounters_have_tz_aware_discharge(synthea: SyntheaAdapter) -> None:
    for pid in (INPATIENT_ID, ER_ID, MED_CHANGES_ID, NO_HOSPITAL_ID):
        for enc in synthea.get_encounters(pid):
            if enc.status == "finished":
                assert enc.discharge_time is not None
                assert enc.discharge_time.tzinfo is UTC


def test_search_by_mrn(synthea: SyntheaAdapter) -> None:
    assert [p.id for p in synthea.search_patients(mrn=ER_ID)] == [ER_ID]


def test_search_by_name_and_dob(synthea: SyntheaAdapter) -> None:
    found = synthea.search_patients(family_name="kovacek682", birth_date=date(1969, 4, 15))
    assert [p.id for p in found] == [MED_CHANGES_ID]
    assert synthea.search_patients(family_name="Kovacek682", birth_date=date(1970, 1, 1)) == []


def test_search_requires_criteria(synthea: SyntheaAdapter) -> None:
    with pytest.raises(ValueError):
        synthea.search_patients()


def test_not_found(synthea: SyntheaAdapter) -> None:
    with pytest.raises(NotFoundError):
        synthea.get_patient("nope")
    with pytest.raises(NotFoundError):
        synthea.get_encounter("nope")
    with pytest.raises(NotFoundError):
        synthea.get_medication_requests("nope")


def test_missing_data_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SyntheaAdapter(tmp_path / "missing")
