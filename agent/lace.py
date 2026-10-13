"""LACE index for inpatient stays (van Walraven et al., CMAJ 2010). ILLUSTRATIVE.

L  length of stay (days)        <1:0  1:1  2:2  3:3  4-6:4  7-13:5  >=14:7
A  acuity: emergent admission    3 (we treat "admitted via a linked ER visit" as emergent)
C  Charlson comorbidity score    0-3: same  >=4: 5
E  ED visits in prior 6 months   0-3: same  >=4: 4
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from models import CharlsonResult, Encounter, LaceScore


def l_points(days: int) -> int:
    if days < 1:
        return 0
    if days <= 3:
        return days
    if days <= 6:
        return 4
    if days <= 13:
        return 5
    return 7


def c_points(charlson_score: int) -> int:
    return 5 if charlson_score >= 4 else charlson_score


def e_points(ed_visits: int) -> int:
    return min(ed_visits, 4)


def prior_ed_visits(
    encounters: Iterable[Encounter],
    before: datetime,
    lookback: timedelta,
    exclude_ids: Iterable[str | None] = (),
) -> list[Encounter]:
    """EMER encounters that started in [before - lookback, before), minus excluded ids."""
    excluded = {i for i in exclude_ids if i}
    window_start = before - lookback
    return [
        e
        for e in encounters
        if e.class_code == "EMER"
        and e.id not in excluded
        and e.status not in ("cancelled", "entered-in-error")
        and e.period_start is not None
        and window_start <= e.period_start < before
    ]


def compute_lace(
    length_of_stay_days: int,
    emergent: bool,
    charlson: CharlsonResult,
    ed_visits_prior_6_months: int,
) -> LaceScore:
    return LaceScore(
        length_of_stay_days=length_of_stay_days,
        l_points=l_points(length_of_stay_days),
        emergent=emergent,
        a_points=3 if emergent else 0,
        charlson=charlson,
        c_points=c_points(charlson.score),
        ed_visits_prior_6_months=ed_visits_prior_6_months,
        e_points=e_points(ed_visits_prior_6_months),
    )
