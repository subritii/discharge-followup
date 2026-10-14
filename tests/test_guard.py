"""Guard tests with seeded fake identifiers. The guard reduces risk; it can't guarantee."""

import random
from datetime import date

import pytest

from models import Address, DischargeDestination, MedChangeKind, Patient
from privacy.guard import KnownIdentifiers, UnredactedIdentifierError, assert_redacted
from privacy.payload import LLMPayload, MedicationLine
from privacy.redactor import Redactor
from tests.fake_identifiers import FIRST, LAST, SEED


def seeded_patient(seed: int = SEED) -> Patient:
    rng = random.Random(seed)
    return Patient(
        id=f"{rng.getrandbits(32):08x}-0000-4000-8000-{rng.getrandbits(48):012x}",
        mrn=f"MRN-{rng.randint(10_000_000, 99_999_999)}",
        given_names=(f"{rng.choice(FIRST)}{rng.randint(10, 999)}",),  # Synthea-style digits
        family_name=f"{rng.choice(LAST)}{rng.randint(10, 999)}",
        birth_date=date(rng.randint(1930, 1970), rng.randint(1, 12), rng.randint(1, 28)),
        address=Address(lines=("77 Quarry Hill Road",), city="Haverhill", postal_code="01830"),
        phones=("617-555-0123",),
    )


PATIENT = seeded_patient()
KNOWN = KnownIdentifiers.from_patient(PATIENT)
GIVEN = PATIENT.given_names[0].rstrip("0123456789")
FAMILY = (PATIENT.family_name or "").rstrip("0123456789")
DOB = PATIENT.birth_date
assert DOB is not None


def payload(instructions: str | None = None, dosage: str | None = None) -> LLMPayload:
    return LLMPayload(
        encounter_class="IMP",
        destination=DischargeDestination.HOME,
        length_of_stay_days=2.0,
        diagnoses=("Heart failure (disorder)",),
        medications=(
            MedicationLine(
                name="furosemide 40 MG Oral Tablet", dosage=dosage, change=MedChangeKind.STARTED
            ),
        ),
        instructions=instructions,
    )


# --- Layer 1: known identifiers ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"Spoke with {GIVEN} today.", "name"),
        (f"spoke with {GIVEN.lower()} today.", "name"),  # lowercase
        (f"{FAMILY.upper()} FAMILY AT BEDSIDE", "name"),
        (f"Patient {PATIENT.given_names[0]} seen.", "name"),  # with Synthea digits
        (f"DOB {DOB.isoformat()}", "birth_date"),
        (f"born {DOB.month}/{DOB.day}/{DOB.year}", "birth_date"),
        (f"born {DOB.strftime('%B')} {DOB.day}, {DOB.year}", "birth_date"),
        (f"ref {PATIENT.mrn}", "mrn"),
        (f"id={PATIENT.id}", "patient_id"),
        ("Call (617) 555-0123", "phone"),
        ("Call 617.555.0123", "phone"),
        ("Call +1 617 555 0123", "phone"),
        ("Lives on 77 quarry hill road", "address"),
        ("Moved to Haverhill", "address"),
        ("zip 01830", "postal_code"),
    ],
)
def test_known_identifier_found(text: str, kind: str) -> None:
    assert KNOWN.find(text) == [kind]


def test_names_match_whole_words_only() -> None:
    known = KnownIdentifiers.from_patient(Patient(id="x", given_names=("Ann",)))
    assert known.find("Annual follow-up; plan to scan") == []
    assert known.find("Ann will call") == ["name"]


def test_short_names_ignored() -> None:
    # Two-letter names would match ordinary words; documented trade-off.
    known = KnownIdentifiers.from_patient(Patient(id="x", given_names=("Al",)))
    assert "name" not in known.find("Al called")


def test_clean_payload_passes() -> None:
    assert_redacted(payload("Take furosemide daily for 7 days. Weigh every morning."), KNOWN)


def test_known_identifier_in_instructions_fails() -> None:
    with pytest.raises(UnredactedIdentifierError, match="known:name"):
        assert_redacted(payload(f"Remind {GIVEN} to weigh daily."), KNOWN)


def test_known_identifier_in_dosage_fails() -> None:
    with pytest.raises(UnredactedIdentifierError, match="known:phone"):
        assert_redacted(payload(dosage="call 617-555-0123 before dose"), KNOWN)


def test_error_never_contains_identifier_values() -> None:
    text = f"{GIVEN} {FAMILY}, DOB {DOB.isoformat()}, {PATIENT.mrn}, 617-555-0123"
    with pytest.raises(UnredactedIdentifierError) as exc:
        assert_redacted(payload(text), KNOWN)
    message = str(exc.value) + repr(exc.value.findings)
    for value in (GIVEN, FAMILY, DOB.isoformat(), PATIENT.mrn, "555-0123"):
        assert value not in message  # type: ignore[operator]
    assert set(exc.value.findings) == {
        "known:name",
        "known:birth_date",
        "known:mrn",
        "known:phone",
    }


# --- Layer 2: Presidio re-scan of free text ---------------------------------------


def test_unredacted_third_party_caught_by_presidio(redactor: Redactor) -> None:
    # Not this patient, so layer 1 can't know the name; NER catches it.
    with pytest.raises(UnredactedIdentifierError, match="presidio:instructions:PERSON"):
        assert_redacted(payload("Daughter Priya Raman will drive."), KNOWN, redactor)


def test_placeholders_are_not_flagged(redactor: Redactor) -> None:
    redacted = "Follow up with Dr. <PERSON> at <PHONE_NUMBER> on <DATE_TIME>."
    assert_redacted(payload(redacted), KNOWN, redactor)


def test_layers_complement_each_other(redactor: Redactor) -> None:
    # A bare lowercase surname with no context is easy for NER to miss, but layer 1
    # knows this patient's surname. Neither layer alone covers everything.
    text = f"spoke with {FAMILY.lower()} re: scale at home"
    with pytest.raises(UnredactedIdentifierError, match="known:name"):
        assert_redacted(payload(text), KNOWN, redactor)
