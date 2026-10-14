"""The Case custom fields this project writes, and a check against the org's describe()."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from models import CaseRoute, RiskLevel


@dataclass(frozen=True)
class FieldSpec:
    type: str  # Salesforce describe() type: string, date, textarea, boolean, picklist
    length: int | None = None  # minimum; a longer field is fine
    unique: bool = False
    external_id: bool = False
    picklist: tuple[str, ...] = ()


CASE_FIELDS: dict[str, FieldSpec] = {
    "Idempotency_Key__c": FieldSpec("string", 100, unique=True, external_id=True),
    "Patient_FHIR_Id__c": FieldSpec("string", 64),
    "Encounter_FHIR_Id__c": FieldSpec("string", 64),
    "Discharge_Date__c": FieldSpec("date"),
    "Follow_Up_Route__c": FieldSpec("picklist", picklist=tuple(r.value for r in CaseRoute)),
    "Risk_Level__c": FieldSpec("picklist", picklist=tuple(r.value for r in RiskLevel)),
    "Risk_Explanation__c": FieldSpec("textarea", 2000),
    "AI_Draft__c": FieldSpec("textarea", 32768),
    "Next_Action__c": FieldSpec("string", 40),
    "Next_Action_Due__c": FieldSpec("date"),
    "Visit_Due_Date__c": FieldSpec("date"),
    "RN_Approved__c": FieldSpec("boolean"),
}


def check_case_fields(describe: Mapping[str, Any]) -> list[str]:
    """Compare Case describe() output with CASE_FIELDS. Empty list = all good."""
    actual = {f["name"]: f for f in describe["fields"]}
    problems: list[str] = []
    for name, spec in CASE_FIELDS.items():
        field = actual.get(name)
        if field is None:
            problems.append(f"{name}: missing (or not visible to the API user)")
            continue
        if field["type"] != spec.type:
            problems.append(f"{name}: type {field['type']}, expected {spec.type}")
        if spec.length is not None and (field.get("length") or 0) < spec.length:
            problems.append(f"{name}: length {field.get('length')}, need at least {spec.length}")
        if spec.unique and not field.get("unique"):
            problems.append(f"{name}: not Unique")
        if spec.unique and not field.get("caseSensitive"):
            problems.append(f"{name}: Unique should be case sensitive")
        if spec.external_id and not field.get("externalId"):
            problems.append(f"{name}: not an External ID")
        if spec.picklist:
            values = {v["value"] for v in field.get("picklistValues", []) if v.get("active")}
            if values != set(spec.picklist):
                problems.append(
                    f"{name}: picklist values {sorted(values)}, expected {sorted(spec.picklist)}"
                )
            if not field.get("restrictedPicklist"):
                problems.append(f"{name}: picklist is not restricted")
        if not field.get("createable"):
            problems.append(f"{name}: not createable by the API user (field-level security)")
    return problems
