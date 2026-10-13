import pytest

from adapters.fhir_parse import (
    first_coding,
    parse_encounter,
    parse_medication_request,
    parse_patient,
    ref_id,
)


@pytest.mark.parametrize(
    "reference",
    ["urn:uuid:abc-123", "Encounter/abc-123", "abc-123", "https://fhir.example/Encounter/abc-123"],
)
def test_ref_id(reference: str) -> None:
    assert ref_id(reference) == "abc-123"


def test_first_coding_falls_back_to_text() -> None:
    coding = first_coding({"coding": [{"system": "s", "code": "c"}], "text": "Label"})
    assert (coding.system, coding.code, coding.display) == ("s", "c", "Label")
    assert first_coding({"text": "only text"}).display == "only text"
    assert first_coding(None).code is None


def test_parse_patient_picks_mrn_and_official_name() -> None:
    patient = parse_patient(
        {
            "resourceType": "Patient",
            "id": "p1",
            "identifier": [
                {"system": "ssn", "type": {"coding": [{"code": "SS"}]}, "value": "999"},
                {"system": "hosp", "type": {"coding": [{"code": "MR"}]}, "value": "MRN-1"},
            ],
            "name": [
                {"use": "maiden", "family": "Old", "given": ["A"]},
                {"use": "official", "family": "New", "given": ["A", "B"]},
            ],
            "birthDate": "1960-01-02",
            "address": [{"city": "Lowell", "state": "MA", "postalCode": "01850"}],
        }
    )
    assert patient.mrn == "MRN-1"
    assert patient.family_name == "New"
    assert patient.given_names == ("A", "B")
    assert patient.address is not None and patient.address.postal_code == "01850"


def test_parse_encounter_without_end() -> None:
    enc = parse_encounter(
        {
            "resourceType": "Encounter",
            "id": "e1",
            "status": "in-progress",
            "class": {"code": "IMP"},
            "subject": {"reference": "urn:uuid:p1"},
            "period": {"start": "2026-01-01T00:00:00Z"},
        }
    )
    assert enc.patient_id == "p1"
    assert enc.period_end is None


MED_REQ_BY_REF = {
    "resourceType": "MedicationRequest",
    "id": "mr1",
    "status": "completed",
    "intent": "order",
    "medicationReference": {"reference": "urn:uuid:med1"},
    "subject": {"reference": "urn:uuid:p1"},
    "encounter": {"reference": "urn:uuid:e1"},
}


def test_medication_reference_resolved() -> None:
    meds = {"med1": {"id": "med1", "code": {"coding": [{"code": "1191", "display": "aspirin"}]}}}
    req = parse_medication_request(MED_REQ_BY_REF, meds)
    assert req.medication.display == "aspirin"
    assert req.encounter_id == "e1"


def test_unresolved_medication_reference_raises() -> None:
    with pytest.raises(ValueError, match="unresolved Medication med1"):
        parse_medication_request(MED_REQ_BY_REF, {})
