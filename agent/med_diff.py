"""Deterministic medication changes around a discharge. The LLM never invents these.

Definitions (see library/guides/med_changes.md):
  episode         ER arrival (if admitted via ER) or admission -> discharge
  started         ordered during the episode (on an episode encounter or authored inside
                  the window) and still `active`
  inpatient_only  ordered during the episode but not active now (given in hospital only)
  continued       active order authored before the episode, not re-ordered during it

Known limitation: Synthea has no medication stop events (no end dates; `status` is the
status today, not at discharge), so "stopped at discharge" and dose changes are not
detected. Hand-made stop-event fixtures are in the Version 2 list.
"""

from collections.abc import Iterable
from datetime import datetime

from models import DischargeNotice, Encounter, MedChange, MedChangeKind, MedicationRequest

ACTIVE = frozenset({"active", "on-hold"})
IGNORED = frozenset({"entered-in-error", "draft"})


def episode_start(notice: DischargeNotice, encounters: Iterable[Encounter]) -> datetime:
    linked = next((e for e in encounters if e.id == notice.linked_er_encounter_id), None)
    return (linked.period_start if linked else None) or notice.admit_time


def med_diff(
    notice: DischargeNotice,
    medication_requests: Iterable[MedicationRequest],
    encounters: Iterable[Encounter],
) -> tuple[MedChange, ...]:
    """Started first, then continued, then inpatient-only; one line per (name, dosage)."""
    start, end = episode_start(notice, encounters), notice.discharge_time
    episode_ids = {notice.encounter_id, notice.linked_er_encounter_id} - {None}

    def during(m: MedicationRequest) -> bool:
        if m.encounter_id in episode_ids:
            return True
        return m.authored_on is not None and start <= m.authored_on <= end

    requests = [m for m in medication_requests if m.status not in IGNORED and m.medication.display]
    in_episode = [m for m in requests if during(m)]
    before = [
        m for m in requests if not during(m) and m.authored_on is not None and m.authored_on < start
    ]
    started = [m for m in in_episode if m.status in ACTIVE]
    started_names = {m.medication.display for m in started}

    changes: dict[tuple[str, str | None], MedChange] = {}

    def add(meds: list[MedicationRequest], kind: MedChangeKind) -> None:
        for m in meds:
            key = (m.medication.display or "", m.dosage_text)
            changes.setdefault(key, MedChange(name=key[0], dosage=key[1], change=kind))

    add(started, MedChangeKind.STARTED)
    add(
        [m for m in before if m.status in ACTIVE and m.medication.display not in started_names],
        MedChangeKind.CONTINUED,
    )
    add([m for m in in_episode if m.status not in ACTIVE], MedChangeKind.INPATIENT_ONLY)
    return tuple(changes.values())
