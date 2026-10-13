"""minimize -> redact free text -> guard. The single entry point for LLM-bound data."""

from collections.abc import Iterable

from models import Condition, DischargeNotice, MedicationRequest, Patient
from privacy.guard import KnownIdentifiers, assert_redacted
from privacy.payload import LLMPayload, MedicationLine, build_llm_payload
from privacy.redactor import Redactor


def _redact_free_text(payload: LLMPayload, redactor: Redactor, allow: list[str]) -> LLMPayload:
    def clean(text: str | None) -> str | None:
        return redactor.redact(text, allow).text if text else text

    return payload.model_copy(
        update={
            "instructions": clean(payload.instructions),
            "medications": tuple(
                MedicationLine(name=m.name, dosage=clean(m.dosage)) for m in payload.medications
            ),
        }
    )


def prepare_llm_payload(
    notice: DischargeNotice,
    patient: Patient,
    diagnoses: Iterable[Condition],
    medications: Iterable[MedicationRequest],
    redactor: Redactor,
    instructions: str | None = None,
) -> LLMPayload:
    """Build the LLM payload. `patient` is used ONLY to know what must not appear in it.

    Raises NoAIDraftError for expired patients and UnredactedIdentifierError if the guard
    trips (the pipeline sends those to the retry folder for human review).
    """
    payload = build_llm_payload(notice, diagnoses, medications, instructions)
    # Coded clinical names are never identifiers; keep NER from redacting them.
    allow = [*payload.diagnoses, *(m.name for m in payload.medications)]
    payload = _redact_free_text(payload, redactor, allow)
    assert_redacted(payload, KnownIdentifiers.from_patient(patient, notice), redactor, allow)
    return payload
