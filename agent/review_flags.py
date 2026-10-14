"""Deterministic review flags on the medication list, for RN verification."""

import re
from collections.abc import Iterable

from models import MedChangeKind, ReviewFlag
from privacy.payload import MedicationLine

_AS_NEEDED = re.compile(r"\b(as needed|prn)\b", re.IGNORECASE)


def review_flags(medications: Iterable[MedicationLine]) -> tuple[ReviewFlag, ...]:
    """Newly started meds with "as needed" instructions: often a data-entry issue for
    scheduled drugs (e.g. a beta-blocker), so the RN verifies the instructions."""
    return tuple(
        ReviewFlag(
            id="STARTED_MED_AS_NEEDED",
            detail=f'Verify instructions: {m.name} was started with "{m.dosage}" dosing.',
        )
        for m in medications
        if m.change is MedChangeKind.STARTED and m.dosage and _AS_NEEDED.search(m.dosage)
    )
