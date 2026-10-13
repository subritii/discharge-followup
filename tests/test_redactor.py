"""Presidio safety net on free text, measured on seeded fake identifiers.

The seeded cases come from a few templates, so these rates are optimistic compared
with real clinical notes. They catch regressions; they don't prove real-world recall.
"""

from collections import Counter

import pytest

from privacy.redactor import Redactor
from tests.fake_identifiers import fake_cases

CASES = fake_cases(60)
PATTERN_KINDS = {"phone", "email", "mrn", "ssn", "date", "street", "state_zip"}
NER_KINDS = {"name", "doctor"}
NER_MIN_RECALL = 0.95  # measured 1.00 on 200 seeded cases with en_core_web_lg 3.8.0


@pytest.fixture(scope="module")
def redacted(redactor: Redactor) -> list[str]:
    return [redactor.redact(c.text).text for c in CASES]


def test_seeded_cases_cover_every_kind() -> None:
    kinds = Counter(k for c in CASES for k in c.identifiers)
    assert set(kinds) == PATTERN_KINDS | NER_KINDS


def test_pattern_identifiers_always_removed(redacted: list[str]) -> None:
    leaks = [
        (kind, i)
        for i, (case, out) in enumerate(zip(CASES, redacted, strict=True))
        for kind, value in case.identifiers.items()
        if kind in PATTERN_KINDS and value in out
    ]
    assert leaks == []


def test_ner_identifier_recall(redacted: list[str]) -> None:
    total = missed = 0
    for case, out in zip(CASES, redacted, strict=True):
        for kind, value in case.identifiers.items():
            if kind in NER_KINDS:
                total += 1
                missed += any(part in out for part in value.split())
    assert total > 0
    assert (total - missed) / total >= NER_MIN_RECALL


def test_clinical_content_preserved(redacted: list[str]) -> None:
    lost = [
        i
        for i, (case, out) in enumerate(zip(CASES, redacted, strict=True))
        if not all(phrase in out for phrase in case.clinical)
    ]
    assert lost == []


@pytest.mark.parametrize(
    "text",
    [
        "Take furosemide 40 MG daily for 7 days.",
        "Ticagrelor 90 MG twice daily for 12 months.",
        "Weigh yourself every morning; call if you gain 3 lbs in 2 days.",
        "Heparin 10000 units subcutaneous every 8 hours.",
        "Follow up in 1 week. 65 year old with CHF.",
    ],
)
def test_durations_doses_and_ordinary_ages_kept(redactor: Redactor, text: str) -> None:
    assert redactor.redact(text).text == text


@pytest.mark.parametrize(
    ("text", "entity"),
    [
        ("Follow up on 06/03/2025.", "DATE_TIME"),
        ("Seen March 4, 2025 in clinic.", "DATE_TIME"),
        ("92 year old with CHF.", "DATE_TIME"),  # age > 89 is a Safe Harbor identifier
        ("Record 922fb35e-148d-9e82-7e65-bfa05e3b3515 reviewed.", "RECORD_ID"),
        ("MRN: A1234567 on file.", "MRN"),
        ("Call (538) 555-0149.", "PHONE_NUMBER"),  # invalid area code still caught
        ("Lives at 1180 Old Mill Road Apt 4.", "STREET_ADDRESS"),
    ],
)
def test_identifier_redacted(redactor: Redactor, text: str, entity: str) -> None:
    result = redactor.redact(text)
    assert entity in result.entity_types
    assert f"<{entity}>" in result.text


def test_allow_list_prevents_brand_name_false_positive(redactor: Redactor) -> None:
    text = "Continue Entresto and Brilinta."
    assert "Brilinta" not in redactor.redact(text).text  # NER thinks it's a PERSON
    assert redactor.redact(text, allow_list=["Brilinta"]).text == text


def test_result_reports_types_not_values(redactor: Redactor) -> None:
    result = redactor.redact("Call Maria Okafor at 617-555-0142.")
    assert result.entity_types == ("PERSON", "PHONE_NUMBER")
    assert "Okafor" not in repr(result.entity_types)
