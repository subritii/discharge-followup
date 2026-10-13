"""Presidio-based redaction for FREE TEXT only (instructions, dosage text).

This is a safety net behind data minimization (privacy/payload.py), not the primary
control. NER misses things, so the pipeline also runs privacy/guard.py.

spaCy model: en_core_web_lg 3.8.0 (pinned in pyproject.toml). It's Presidio's
default, recalls PERSON/LOCATION noticeably better than sm/md, and runs on CPU
without PyTorch. en_core_web_trf is more accurate but much heavier for a backstop layer.
"""

import re
from dataclasses import dataclass
from functools import cached_property

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

SPACY_MODEL = "en_core_web_lg"
# Presidio compiles patterns with IGNORECASE by default. Address patterns depend on
# capitalization ("42 Birch Lane" vs "14 days. Contact Dr"), so they opt out.
_CASE_SENSITIVE = re.DOTALL | re.MULTILINE
SCORE_THRESHOLD = 0.3
# spaCy labels that are never identifiers (doses, counts) -- ignored instead of logged.
_NER_LABELS_TO_IGNORE = ["CARDINAL", "ORDINAL", "QUANTITY", "PERCENT", "MONEY"]

ENTITIES = [
    "PERSON",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "LOCATION",
    "DATE_TIME",
    "US_SSN",
    "US_DRIVER_LICENSE",
    "US_PASSPORT",
    "MEDICAL_LICENSE",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "URL",
    "MRN",
    "RECORD_ID",
    "STREET_ADDRESS",
    "ZIP_CODE",
]

_STREET_SUFFIX = (
    r"Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Court|Ct|Boulevard|Blvd|Way|Place|Pl"
    r"|Terrace|Ter|Circle|Cir|Highway|Hwy|Parkway|Pkwy|Square|Sq"
)

_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
# A calendar date (identifying under HIPAA Safe Harbor) as opposed to a duration or
# frequency ("for 7 days", "twice daily"), which is clinical content we must keep.
_CALENDAR_DATE = re.compile(
    rf"\b\d{{1,4}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?\b|\b(?:19|20)\d{{2}}\b|\b(?:{_MONTHS})\b",
    re.IGNORECASE,
)
_AGE_OVER_89 = re.compile(r"\b(9\d|1\d\d)[- ](?:years?|yrs?|y/?o)\b", re.IGNORECASE)


def _custom_recognizers() -> list[PatternRecognizer]:
    mrn = PatternRecognizer(
        supported_entity="MRN",
        patterns=[
            Pattern(
                "mrn_labelled",
                r"(?i)\b(?:MRN|MR#|medical record(?: number| no\.?)?)"
                r"[\s:#-]*[A-Z0-9][A-Z0-9-]{3,}\b",
                0.85,
            ),
            Pattern("mrn_synthetic", r"\bMRN-\d{8}\b", 0.95),
        ],
    )
    record_id = PatternRecognizer(
        supported_entity="RECORD_ID",
        patterns=[
            Pattern(
                "uuid",
                r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                0.9,
            )
        ],
    )
    street = PatternRecognizer(
        supported_entity="STREET_ADDRESS",
        patterns=[
            Pattern(
                "number_name_suffix",
                rf"\b\d{{1,6}}\s+(?:[A-Z][A-Za-z']*\.?\s+){{1,4}}(?:{_STREET_SUFFIX})\b\.?"
                r"(?:\s+(?:Apt|Unit|Suite|#)\s*\w+)?",
                0.7,
            )
        ],
        global_regex_flags=_CASE_SENSITIVE,
    )
    # State abbreviation + ZIP only: a bare 5-digit number could be a dose (e.g. 10000 units).
    zip_code = PatternRecognizer(
        supported_entity="ZIP_CODE",
        patterns=[Pattern("state_zip", r"\b[A-Z]{2},?\s+\d{5}(?:-\d{4})?\b", 0.7)],
        global_regex_flags=_CASE_SENSITIVE,
    )
    # Presidio's built-in phone recognizer validates numbers (real area codes only).
    # A safety net should catch anything phone-shaped, valid or not.
    phone_shape = PatternRecognizer(
        supported_entity="PHONE_NUMBER",
        patterns=[
            Pattern(
                "us_phone_shape",
                r"(?:\+1[\s.-]?)?(?:\(\d{3}\)\s?|\b\d{3}[\s.-])\d{3}[\s.-]\d{4}\b",
                0.6,
            )
        ],
    )
    return [mrn, record_id, street, zip_code, phone_shape]


def _keep_result(text: str, result: RecognizerResult) -> bool:
    """Drop DATE_TIME hits that are durations/frequencies, not dates or ages over 89."""
    if result.entity_type != "DATE_TIME":
        return True
    span = text[result.start : result.end]
    return bool(_CALENDAR_DATE.search(span) or _AGE_OVER_89.search(span))


@dataclass(frozen=True)
class RedactionResult:
    text: str
    entity_types: tuple[str, ...]  # what was redacted; never the values themselves


class Redactor:
    """Loads spaCy once (a few seconds); reuse one instance."""

    @cached_property
    def _analyzer(self) -> AnalyzerEngine:
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
                "ner_model_configuration": {"labels_to_ignore": _NER_LABELS_TO_IGNORE},
            }
        ).create_engine()
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        for recognizer in _custom_recognizers():
            analyzer.registry.add_recognizer(recognizer)
        return analyzer

    @cached_property
    def _anonymizer(self) -> AnonymizerEngine:
        return AnonymizerEngine()

    def analyze(self, text: str, allow_list: list[str] | None = None) -> list[RecognizerResult]:
        results = self._analyzer.analyze(
            text=text,
            language="en",
            entities=ENTITIES,
            score_threshold=SCORE_THRESHOLD,
            allow_list=allow_list or None,
        )
        return [r for r in results if _keep_result(text, r)]

    def redact(self, text: str, allow_list: list[str] | None = None) -> RedactionResult:
        """Replace detected identifiers with <ENTITY_TYPE> placeholders.

        `allow_list`: exact strings never to redact (e.g. coded medication names that
        NER sometimes mistakes for a PERSON).
        """
        results = self.analyze(text, allow_list)
        if not results:
            return RedactionResult(text=text, entity_types=())
        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=results,  # type: ignore[arg-type]
            operators={
                "DEFAULT": OperatorConfig("custom", {"lambda": lambda _: "<REDACTED>"}),
                **{
                    entity: OperatorConfig("replace", {"new_value": f"<{entity}>"})
                    for entity in ENTITIES
                },
            },
        )
        return RedactionResult(
            text=anonymized.text,
            entity_types=tuple(sorted({r.entity_type for r in results})),
        )
