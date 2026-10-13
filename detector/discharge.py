"""Detect discharges from FHIR encounters.

A discharge is an Encounter with class IMP or EMER, status `finished`, and a
`period.end`. dischargeDisposition is never required: it only sets the destination
(see detector/disposition.py and library/disposition_map.yaml).

An ER visit followed by an inpatient admission is one episode: if the IMP starts
while the ER visit is still open, or within `merge_window` after it ends, the two
are merged into one DischargeNotice keyed on the IMP encounter.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from adapters.base import PatientDataAdapter
from detector.disposition import DispositionMap, resolve_destination
from models import DischargeNotice, Encounter, Patient

DEFAULT_MERGE_WINDOW = timedelta(hours=6)


def is_discharge(encounter: Encounter) -> bool:
    return (
        encounter.is_hospital_class
        and encounter.status == "finished"
        and encounter.period_start is not None
        and encounter.period_end is not None
    )


def er_leads_to_admission(er: Encounter, imp: Encounter, window: timedelta) -> bool:
    """IMP starts during the ER visit, or within `window` after it ends."""
    assert er.period_start and er.period_end and imp.period_start
    return er.period_start <= imp.period_start <= er.period_end + window


def _notice(
    primary: Encounter,
    linked_er: Encounter | None,
    patient: Patient | None,
    mapping: DispositionMap | None,
) -> DischargeNotice:
    assert primary.period_start and primary.discharge_time
    return DischargeNotice(
        patient_id=primary.patient_id,
        encounter_id=primary.id,
        encounter_class=primary.class_code,
        admit_time=primary.period_start,
        discharge_time=primary.discharge_time,
        destination=resolve_destination(
            primary.discharge_disposition,
            mapping,
            patient=patient,
            episode_start=(linked_er.period_start if linked_er else None) or primary.period_start,
            discharge_time=primary.discharge_time,
        ),
        linked_er_encounter_id=linked_er.id if linked_er else None,
        reasons=primary.reasons,
    )


def _ends(encounter: Encounter) -> datetime:
    assert encounter.period_end
    return encounter.period_end


def detect_discharges(
    encounters: Iterable[Encounter],
    merge_window: timedelta = DEFAULT_MERGE_WINDOW,
    *,
    patient: Patient | None = None,
    disposition_map: DispositionMap | None = None,
) -> list[DischargeNotice]:
    """Turn one patient's encounters into discharge notices, oldest discharge first.

    Pass `patient` so a death during the stay (or within 3 days after) marks it EXPIRED.
    """
    qualifying = [e for e in encounters if is_discharge(e)]
    ers = sorted((e for e in qualifying if e.class_code == "EMER"), key=_ends)
    imps = sorted((e for e in qualifying if e.class_code == "IMP"), key=lambda e: e.period_start)

    linked_er_ids: set[str] = set()
    notices: list[DischargeNotice] = []
    for imp in imps:
        # Closest preceding ER visit not already linked to another admission.
        candidates = [
            er
            for er in ers
            if er.id not in linked_er_ids and er_leads_to_admission(er, imp, merge_window)
        ]
        linked = candidates[-1] if candidates else None
        if linked:
            linked_er_ids.add(linked.id)
        notices.append(_notice(imp, linked, patient, disposition_map))

    notices.extend(
        _notice(er, None, patient, disposition_map) for er in ers if er.id not in linked_er_ids
    )
    return sorted(notices, key=lambda n: n.discharge_time)


def detect_for_patient(
    adapter: PatientDataAdapter,
    patient_id: str,
    *,
    merge_window: timedelta = DEFAULT_MERGE_WINDOW,
    discharged_since: datetime | None = None,
) -> list[DischargeNotice]:
    """Detect a patient's discharges, optionally only those at/after `discharged_since`.

    The since-filter is applied after merging, so an ER visit just before the cutoff
    still merges with the admission it led to.
    """
    notices = detect_discharges(
        adapter.get_encounters(patient_id),
        merge_window,
        patient=adapter.get_patient(patient_id),
    )
    if discharged_since is None:
        return notices
    return [n for n in notices if n.discharge_time >= discharged_since]
