from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.charlson import active_during, charlson_for_stay, load_charlson_map
from agent.lace import c_points, compute_lace, e_points, l_points, prior_ed_visits
from models import CharlsonResult, Coding, Condition, Encounter

SNOMED = "http://snomed.info/sct"
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def cond(code: str, onset: datetime | None = None, abatement: datetime | None = None) -> Condition:
    return Condition(
        id=code,
        patient_id="p",
        code=Coding(system=SNOMED, code=code),
        onset=onset,
        abatement=abatement,
    )


def ed(id: str, start: datetime, status: str = "finished") -> Encounter:
    return Encounter(
        id=id,
        patient_id="p",
        status=status,
        class_code="EMER",
        period_start=start,
        period_end=start + timedelta(hours=2),
    )


# --- LACE points ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "points"),
    [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (6, 4), (7, 5), (13, 5), (14, 7), (40, 7)],
)
def test_l_points(days: int, points: int) -> None:
    assert l_points(days) == points


@pytest.mark.parametrize(("score", "points"), [(0, 0), (1, 1), (3, 3), (4, 5), (9, 5)])
def test_c_points(score: int, points: int) -> None:
    assert c_points(score) == points


@pytest.mark.parametrize(("visits", "points"), [(0, 0), (3, 3), (4, 4), (11, 4)])
def test_e_points(visits: int, points: int) -> None:
    assert e_points(visits) == points


def test_compute_lace_components_and_total() -> None:
    lace = compute_lace(14, True, CharlsonResult(score=2, categories=("a", "b")), 1)
    assert (lace.l_points, lace.a_points, lace.c_points, lace.e_points) == (7, 3, 2, 1)
    assert lace.total == 13
    assert compute_lace(0, False, CharlsonResult(score=0, categories=()), 0).total == 0


def test_lace_maximum_is_19() -> None:
    assert compute_lace(30, True, CharlsonResult(score=10, categories=()), 9).total == 19


# --- ED visits in the prior 6 months --------------------------------------------------


def test_prior_ed_window_and_exclusions() -> None:
    lookback = timedelta(days=182)
    encounters = [
        ed("in_window", T0 - timedelta(days=30)),
        ed("boundary", T0 - lookback),  # exactly 182 days: included
        ed("too_old", T0 - lookback - timedelta(days=1)),
        ed("index_er", T0 - timedelta(hours=3)),
        ed("after", T0 + timedelta(days=1)),
        ed("cancelled", T0 - timedelta(days=10), status="cancelled"),
    ]
    found = prior_ed_visits(encounters, before=T0, lookback=lookback, exclude_ids=["index_er"])
    assert {e.id for e in found} == {"in_window", "boundary"}


def test_prior_ed_ignores_other_classes() -> None:
    amb = Encounter(
        id="amb",
        patient_id="p",
        status="finished",
        class_code="AMB",
        period_start=T0 - timedelta(days=5),
        period_end=T0 - timedelta(days=5),
    )
    assert prior_ed_visits([amb], before=T0, lookback=timedelta(days=182)) == []


# --- Charlson -----------------------------------------------------------------------


def test_map_is_labelled_illustrative() -> None:
    m = load_charlson_map()
    assert m.illustrative is True
    assert "pending clinical review" in m.review_status


def test_charlson_scoring_and_hierarchy() -> None:
    m = load_charlson_map()
    # MI (1) + CHF (1) + CKD stage 4 (2)
    assert m.score(["401303003", "88805009", "431857002"]).score == 4
    # complicated diabetes (2) supersedes uncomplicated (1)
    diabetes = m.score(["44054006", "127013003"])
    assert diabetes == CharlsonResult(score=2, categories=("diabetes_complicated",))
    # metastatic (6) supersedes any malignancy (2)
    assert m.score(["254837009", "94225005"]).score == 6
    # unmapped codes ignored; duplicates count once
    assert m.score(["66383009", "401303003", "22298006"]).score == 1


def test_active_during_stay() -> None:
    start, end = T0, T0 + timedelta(days=3)
    assert active_during(cond("x"), start, end)  # no dates: assume active
    assert active_during(cond("x", onset=start + timedelta(days=1)), start, end)  # new dx
    assert not active_during(cond("x", onset=end + timedelta(days=1)), start, end)
    assert not active_during(cond("x", abatement=start - timedelta(days=1)), start, end)
    assert active_during(cond("x", abatement=start + timedelta(hours=1)), start, end)


def test_charlson_for_stay_filters_time_and_system() -> None:
    start, end = T0, T0 + timedelta(days=3)
    conditions = [
        cond("88805009"),  # CHF, chronic -> counts
        cond("44054006", abatement=start - timedelta(days=400)),  # resolved -> no
        Condition(
            id="icd",
            patient_id="p",
            code=Coding(system="http://hl7.org/fhir/sid/icd-10-cm", code="401303003"),
        ),
    ]
    assert charlson_for_stay(conditions, start, end).categories == ("congestive_heart_failure",)


def test_invalid_supersedes_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "c.yaml"
    bad.write_text(
        "illustrative: true\nreview_status: x\ncategories:\n"
        "  a: {weight: 1, snomed: ['1'], supersedes: [nope]}\n"
    )
    with pytest.raises(ValidationError, match="unknown categories"):
        load_charlson_map(bad)
