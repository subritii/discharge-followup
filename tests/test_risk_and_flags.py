from datetime import date
from pathlib import Path

import pytest

from adapters.synthea import SyntheaAdapter
from agent.draft_cache import DraftCache
from agent.drafter import DraftPlan
from agent.review_flags import review_flags
from agent.risk_explanation import explain_risk
from models import (
    CaseRoute,
    CharlsonResult,
    FiredRule,
    LaceScore,
    MedChangeKind,
    RiskLevel,
    TriageResult,
)
from privacy.payload import MedicationLine
from tests.conftest import ER_ID, HIGH_RISK_ID, MED_CHANGES_ID
from tests.episode import CALENDAR
from tests.test_triage_fixtures import triage_all


def result(**overrides: object) -> TriageResult:
    fields: dict[str, object] = {
        "patient_id": "p",
        "encounter_id": "e",
        "idempotency_key": "e|2026-01-01T00:00:00Z",
        "pathway": "er",
        "route": CaseRoute.ER_OUTREACH,
        "risk_level": RiskLevel.LOW,
        "discharge_local_date": date(2026, 1, 1),
        "timezone": "America/Chicago",
        "ai_draft_allowed": True,
    }
    fields.update(overrides)
    return TriageResult(**fields)


# --- Risk explanation (deterministic) ----------------------------------------------


def test_lace_explanation_from_fixture(synthea: SyntheaAdapter) -> None:
    [r] = triage_all(synthea, MED_CHANGES_ID)
    assert explain_risk(r) == (
        "Moderate risk. LACE 5 = L1 (1-day stay) + A3 (admitted through the ER) + "
        "C1 (Charlson 1: myocardial infarction) + E0 (0 ED visits in prior 6 months)."
    )


def test_high_risk_explanation(synthea: SyntheaAdapter) -> None:
    stay = next(r for r in triage_all(synthea, HIGH_RISK_ID) if r.lace)
    assert explain_risk(stay).startswith(
        "High risk. LACE 11 = L7 (15-day stay) + A3 (admitted through the ER) + C0 (Charlson 0)"
    )
    assert "E1 (1 ED visit in prior 6 months)" in explain_risk(stay)


def test_er_explanations(synthea: SyntheaAdapter) -> None:
    [standard] = triage_all(synthea, ER_ID)
    assert explain_risk(standard) == "Low risk (ER visit rules, not LACE): no ER risk rule fired."
    high = result(
        risk_level=RiskLevel.HIGH,
        fired_rules=(FiredRule(id="ER_FREQUENT_USE", detail="3 prior ED visits in 182d"),),
    )
    assert explain_risk(high) == (
        "High risk (ER visit rules, not LACE): ER_FREQUENT_USE: 3 prior ED visits in 182d."
    )


def test_expired_and_deceased_review_explanations() -> None:
    expired = result(route=CaseRoute.CLOSE_THE_LOOP, risk_level=None, ai_draft_allowed=False)
    assert explain_risk(expired).startswith("Not scored: patient expired")
    lace = LaceScore(
        length_of_stay_days=2,
        l_points=2,
        emergent=False,
        a_points=0,
        charlson=CharlsonResult(score=0, categories=()),
        c_points=0,
        ed_visits_prior_6_months=0,
        e_points=0,
    )
    review = result(
        route=CaseRoute.DECEASED_STATUS_REVIEW,
        pathway="inpatient",
        lace=lace,
        ai_draft_allowed=False,
    )
    assert explain_risk(review).startswith("Patient contact on hold (deceased status reported")
    assert "LACE 2" in explain_risk(review)


# --- Review flags (deterministic) ----------------------------------------------------


def line(name: str, dosage: str | None, change: MedChangeKind) -> MedicationLine:
    return MedicationLine(name=name, dosage=dosage, change=change)


@pytest.mark.parametrize(
    "dosage", ["Take as needed.", "1 tab PRN pain", "as needed for chest pain"]
)
def test_started_as_needed_flagged(dosage: str) -> None:
    [flag] = review_flags([line("metoprolol 50 MG", dosage, MedChangeKind.STARTED)])
    assert flag.id == "STARTED_MED_AS_NEEDED"
    assert "metoprolol 50 MG" in flag.detail and dosage in flag.detail


def test_not_flagged() -> None:
    meds = [
        line("metoprolol 50 MG", "once daily", MedChangeKind.STARTED),
        line("albuterol", "Take as needed.", MedChangeKind.CONTINUED),  # not newly started
        line("morphine", "as needed", MedChangeKind.INPATIENT_ONLY),
        line("aspirin 81 MG", None, MedChangeKind.STARTED),
        line("prednisone", "unneeded taper?", MedChangeKind.STARTED),
    ]
    assert review_flags(meds) == ()


# --- Draft cache ---------------------------------------------------------------------


def plan(model: str = "llama3.1:8b", version: str = "discharge_plan@v2") -> DraftPlan:
    return DraftPlan(
        summary="The patient stayed one day after an acute MI.",
        risk_explanation="Moderate risk.",
        med_changes=(),
        review_flags=(),
        rn_call_checklist=("Confirm destination",),
        model=model,
        prompt_version=version,
    )


def test_cache_roundtrip_and_keying(tmp_path: Path) -> None:
    cache = DraftCache(tmp_path / "drafts")
    key = "ea33f67a-c1d2|2025-05-22T18:20:59Z"
    assert cache.get(key, "llama3.1:8b", "discharge_plan@v2") is None
    path = cache.put(key, plan())
    assert path.exists() and "|" not in path.name and ":" not in path.name
    assert cache.get(key, "llama3.1:8b", "discharge_plan@v2") == plan()
    # A different model or prompt version is a cache miss.
    assert cache.get(key, "llama3.2:3b", "discharge_plan@v2") is None
    assert cache.get(key, "llama3.1:8b", "discharge_plan@v3") is None
    cache.put(key, plan(model="llama3.2:3b"))
    assert len(list((tmp_path / "drafts").glob("*.json"))) == 2


def test_calendar_import_is_shared() -> None:
    assert CALENDAR.timezone.key == "America/Chicago"
