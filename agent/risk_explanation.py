"""Deterministic risk explanation from TriageResult. The LLM never explains risk."""

from models import CaseRoute, LaceScore, TriageResult


def _lace_line(lace: LaceScore) -> str:
    stay = f"{lace.length_of_stay_days}-day stay"
    acuity = "admitted through the ER" if lace.emergent else "no linked ER visit"
    charlson = f"Charlson {lace.charlson.score}"
    if lace.charlson.categories:
        charlson += f": {', '.join(c.replace('_', ' ') for c in lace.charlson.categories)}"
    visits = lace.ed_visits_prior_6_months
    return (
        f"LACE {lace.total} = L{lace.l_points} ({stay}) + A{lace.a_points} ({acuity}) + "
        f"C{lace.c_points} ({charlson}) + E{lace.e_points} "
        f"({visits} ED visit{'s' if visits != 1 else ''} in prior 6 months)"
    )


def explain_risk(result: TriageResult) -> str:
    """One or two sentences an RN can read in the Case: tier, score, components, rules."""
    if result.route is CaseRoute.CLOSE_THE_LOOP:
        return "Not scored: patient expired. Informational close-the-loop case."
    prefix = ""
    if result.route is CaseRoute.DECEASED_STATUS_REVIEW:
        prefix = "Patient contact on hold (deceased status reported, date unknown). "
    tier = result.risk_level.value.capitalize() if result.risk_level else "Unscored"
    if result.lace is not None:
        return f"{prefix}{tier} risk. {_lace_line(result.lace)}."
    er_rules = [r for r in result.fired_rules if r.id.startswith("ER_")]
    reasons = (
        "; ".join(f"{r.id}: {r.detail}" for r in er_rules if r.id != "ER_STANDARD")
        or "no ER risk rule fired"
    )
    return f"{prefix}{tier} risk (ER visit rules, not LACE): {reasons}."
