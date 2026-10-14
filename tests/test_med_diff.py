from datetime import UTC, datetime, timedelta

from adapters.synthea import SyntheaAdapter
from agent.med_diff import med_diff
from detector.discharge import detect_for_patient
from models import (
    Coding,
    DischargeNotice,
    Encounter,
    MedChangeKind,
    MedicationRequest,
)
from tests.conftest import INPATIENT_ID, MED_CHANGES_ID

ADMIT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
DISCHARGE = ADMIT + timedelta(days=3)
NOTICE = DischargeNotice(
    patient_id="p",
    encounter_id="imp",
    encounter_class="IMP",
    admit_time=ADMIT,
    discharge_time=DISCHARGE,
)


def order(
    name: str,
    authored: datetime,
    status: str = "active",
    encounter: str | None = None,
    dosage: str | None = None,
) -> MedicationRequest:
    return MedicationRequest(
        id=f"{name}-{authored.isoformat()}",
        patient_id="p",
        encounter_id=encounter,
        status=status,
        intent="order",
        medication=Coding(display=name),
        authored_on=authored,
        dosage_text=dosage,
    )


def kinds(changes) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {c.name: c.change.value for c in changes}


def test_started_continued_inpatient_only() -> None:
    meds = [
        order("lisinopril", ADMIT - timedelta(days=90)),  # home med, still active
        order("furosemide", ADMIT + timedelta(days=1)),  # new, active
        order("heparin", ADMIT, status="completed", encounter="imp"),  # hospital only
        order("old antibiotic", ADMIT - timedelta(days=200), status="completed"),  # past
    ]
    assert kinds(med_diff(NOTICE, meds, [])) == {
        "furosemide": "started",
        "lisinopril": "continued",
        "heparin": "inpatient_only",
    }


def test_reordered_home_med_listed_as_started_once() -> None:
    meds = [
        order("metoprolol", ADMIT - timedelta(days=30)),
        order("metoprolol", ADMIT + timedelta(hours=5)),
    ]
    assert kinds(med_diff(NOTICE, meds, [])) == {"metoprolol": "started"}


def test_entered_in_error_ignored_and_duplicates_collapsed() -> None:
    meds = [
        order("aspirin", ADMIT + timedelta(hours=1), dosage="daily"),
        order("aspirin", ADMIT + timedelta(hours=2), dosage="daily"),
        order("warfarin", ADMIT + timedelta(hours=3), status="entered-in-error"),
    ]
    changes = med_diff(NOTICE, meds, [])
    assert [(c.name, c.change) for c in changes] == [("aspirin", MedChangeKind.STARTED)]


def test_episode_starts_at_linked_er_arrival() -> None:
    er = Encounter(
        id="er",
        patient_id="p",
        status="finished",
        class_code="EMER",
        period_start=ADMIT - timedelta(hours=4),
        period_end=ADMIT,
    )
    notice = NOTICE.model_copy(update={"linked_er_encounter_id": "er"})
    nitro = order("nitroglycerin", ADMIT - timedelta(hours=2), status="completed")
    assert kinds(med_diff(notice, [nitro], [er])) == {"nitroglycerin": "inpatient_only"}
    # Without the ER link, the same order predates the episode and isn't active -> omitted.
    assert med_diff(NOTICE, [nitro], []) == ()


def test_med_changes_fixture(synthea: SyntheaAdapter) -> None:
    [n] = detect_for_patient(synthea, MED_CHANGES_ID)
    changes = med_diff(
        n, synthea.get_medication_requests(MED_CHANGES_ID), synthea.get_encounters(MED_CHANGES_ID)
    )
    by_kind: dict[str, set[str]] = {}
    for c in changes:
        by_kind.setdefault(c.change.value, set()).add(c.name)
    assert by_kind["started"] == {
        "aspirin 81 MG Oral Tablet",
        "24 HR metoprolol succinate 50 MG Extended Release Oral Tablet",
        "atorvastatin 10 MG Oral Tablet",
        "sacubitril 24 MG / valsartan 26 MG Oral Tablet [Entresto]",
        "prasugrel 10 MG Oral Tablet",
    }
    assert "50 ML bivalirudin 5 MG/ML Injection" in by_kind["inpatient_only"]
    assert "aspirin 325 MG Oral Tablet" in by_kind["inpatient_only"]  # ER dose, not a "change"
    assert "continued" not in by_kind
    assert [c.change for c in changes][:5] == [MedChangeKind.STARTED] * 5  # started first


def test_inpatient_fixture_home_meds_continued(synthea: SyntheaAdapter) -> None:
    [n] = detect_for_patient(synthea, INPATIENT_ID)
    changes = med_diff(
        n, synthea.get_medication_requests(INPATIENT_ID), synthea.get_encounters(INPATIENT_ID)
    )
    assert {c.change for c in changes} == {MedChangeKind.CONTINUED}
    assert any("budesonide" in c.name for c in changes)
