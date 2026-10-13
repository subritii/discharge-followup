"""FHIR R4 JSON -> domain models. Shared by every FHIR-based adapter."""

from collections.abc import Mapping
from typing import Any

from models import Address, Coding, Condition, Encounter, MedicationRequest, Patient

Resource = Mapping[str, Any]

MRN_TYPE_CODE = "MR"


def ref_id(reference: str) -> str:
    """'urn:uuid:abc', 'Encounter/abc', or 'abc' -> 'abc'."""
    return reference.rsplit(":", 1)[-1].rsplit("/", 1)[-1]


def _ref(resource: Resource, field: str) -> str | None:
    ref = resource.get(field, {}).get("reference")
    return ref_id(ref) if ref else None


def first_coding(concept: Mapping[str, Any] | None) -> Coding:
    """First coding of a CodeableConcept; falls back to its text as the display."""
    if not concept:
        return Coding()
    codings = concept.get("coding") or [{}]
    c = codings[0]
    return Coding(
        system=c.get("system"),
        code=c.get("code"),
        display=c.get("display") or concept.get("text"),
    )


def _status_code(concept: Mapping[str, Any] | None) -> str | None:
    return first_coding(concept).code if concept else None


def parse_patient(resource: Resource) -> Patient:
    mrn = next(
        (
            ident.get("value")
            for ident in resource.get("identifier", [])
            for coding in ident.get("type", {}).get("coding", [])
            if coding.get("code") == MRN_TYPE_CODE
        ),
        None,
    )
    names = resource.get("name", [])
    name = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    addresses = resource.get("address", [])
    address = (
        Address(
            city=addresses[0].get("city"),
            state=addresses[0].get("state"),
            postal_code=addresses[0].get("postalCode"),
        )
        if addresses
        else None
    )
    return Patient(
        id=resource["id"],
        mrn=mrn,
        given_names=tuple(name.get("given", [])),
        family_name=name.get("family"),
        birth_date=resource.get("birthDate"),
        gender=resource.get("gender"),
        address=address,
    )


def parse_encounter(resource: Resource) -> Encounter:
    period = resource.get("period", {})
    return Encounter(
        id=resource["id"],
        patient_id=_ref(resource, "subject") or "",
        status=resource["status"],
        class_code=resource.get("class", {}).get("code", ""),
        types=tuple(first_coding(t) for t in resource.get("type", [])),
        reasons=tuple(first_coding(r) for r in resource.get("reasonCode", [])),
        period_start=period.get("start"),
        period_end=period.get("end"),
        service_provider=resource.get("serviceProvider", {}).get("display"),
    )


def parse_condition(resource: Resource) -> Condition:
    return Condition(
        id=resource["id"],
        patient_id=_ref(resource, "subject") or "",
        encounter_id=_ref(resource, "encounter"),
        code=first_coding(resource.get("code")),
        clinical_status=_status_code(resource.get("clinicalStatus")),
        onset=resource.get("onsetDateTime"),
        abatement=resource.get("abatementDateTime"),
    )


def parse_medication_request(
    resource: Resource, medications: Mapping[str, Resource]
) -> MedicationRequest:
    """`medications` maps Medication.id -> Medication resource, for medicationReference."""
    if "medicationCodeableConcept" in resource:
        medication = first_coding(resource["medicationCodeableConcept"])
    else:
        med_id = _ref(resource, "medicationReference")
        if med_id not in medications:
            raise ValueError(f"MedicationRequest {resource['id']}: unresolved Medication {med_id}")
        medication = first_coding(medications[med_id].get("code"))
    dosage = resource.get("dosageInstruction") or [{}]
    return MedicationRequest(
        id=resource["id"],
        patient_id=_ref(resource, "subject") or "",
        encounter_id=_ref(resource, "encounter"),
        status=resource["status"],
        intent=resource["intent"],
        medication=medication,
        authored_on=resource.get("authoredOn"),
        dosage_text=dosage[0].get("text"),
    )
