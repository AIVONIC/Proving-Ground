"""Turn dimension results into a composite grade, tier, and confidence.

Handles partial coverage: if only some dimensions were graded (as during Phase 1),
the composite is computed over the graded dimensions with their weights
renormalized, and the result is flagged ``incomplete``. A tier is only assigned
when all twelve composite dimensions are present, because a tier over a subset
would be misleading.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.dimensions.base import DimensionResult
# ⛔ SCORING CONSTANTS ARE READ THROUGH `config.`, NEVER IMPORTED BY NAME.
#
# `from config import LATENCY_W` binds the VALUE at import, so the name then exists
# in two module namespaces and the fingerprint reads one while the arithmetic reads
# the other. In production they cannot diverge, because editing config.py and
# restarting rebinds both -- but "cannot diverge" is an argument, and the point of
# the identifier is that the coupling is DEMONSTRABLE rather than argued. Read
# through the module and a change to config provably reaches the composite, which
# is what the property test asserts by perturbing each knob in turn.
#
# It also closes a real trap: mutable constants (DIMENSION_WEIGHTS, TIERS) are
# shared by reference and DO propagate when mutated, while scalars (CRITICAL_CAP,
# LATENCY_W) do not. Half the knobs behaving one way and half the other is how a
# test proves a property for the two that happen to be dicts.
#
# ⛔ THE RELIABILITY BLEND MOVED HERE FROM THIS MODULE ON 2026-09-18.
# These three change the latency_and_reliability subscore, which carries composite
# weight, so they are scoring CONFIGURATION and belong with the weights. While they
# lived in this module the composite's derived identifier could not see them:
# changing the blend moved a published score and left the id untouched, which is the
# one failure that identifier exists to prevent. Re-declaring any of them here
# restores the hole, because the fingerprint would read config's value and the
# arithmetic below would read this one, and the two agree until somebody edits the
# wrong file. A test refuses that.
from app.scoring import config

# Below this cross-lab agreement (1 - max-min judge spread), a judged dimension's
# score rests on judges that disagreed materially and is flagged low-confidence.
# NOT scoring configuration: it flags confidence and cannot move a composite or a
# tier, so it stays out of config.py and out of the fingerprint. Everything in that
# module makes old grades incomparable when it moves; this does not.
LOW_AGREEMENT = 0.5


def _agreement_summary(dim_results: dict[str, DimensionResult]) -> dict | None:
    """Cross-lab judge agreement per dimension and overall. None if nothing was
    panel-judged (all deterministic). This surfaces, rather than hides, the case
    where the frontier panel split on a score."""
    per_dim: dict[str, float] = {}
    for d, r in dim_results.items():
        vals = [pr.judge_agreement for pr in r.probe_results if pr.judge_agreement is not None]
        if vals:
            per_dim[d] = round(sum(vals) / len(vals), 3)
    if not per_dim:
        return None
    overall = round(sum(per_dim.values()) / len(per_dim), 3)
    low = sorted((d for d, a in per_dim.items() if a < LOW_AGREEMENT), key=lambda d: per_dim[d])
    return {"overall": overall, "per_dimension": per_dim, "low_agreement_dimensions": low}


@dataclass
class GradeResult:
    composite: float
    tier: str
    critical_failures: int
    subscores: dict[str, float]
    graded_dimensions: list[str]
    incomplete: bool
    capped: bool
    confidence: dict = field(default_factory=dict)


def compute_composite(subscores: dict[str, float], critical_failures: int) -> tuple[float, bool, bool]:
    """Return (composite 0..100, incomplete, capped)."""
    present = {d: s for d, s in subscores.items() if d in config.DIMENSION_WEIGHTS}
    if not present:
        return 0.0, True, False
    total_w = sum(config.DIMENSION_WEIGHTS[d] for d in present)
    raw = sum(config.DIMENSION_WEIGHTS[d] * present[d] for d in present) / total_w * 10.0
    incomplete = len(present) < len(config.DIMENSION_WEIGHTS)
    capped = False
    if critical_failures > 0 and raw > config.CRITICAL_CAP:
        raw = config.CRITICAL_CAP
        capped = True
    return round(raw, 2), incomplete, capped


def assign_tier(composite: float, subscores: dict[str, float], critical_failures: int, incomplete: bool) -> str:
    if incomplete:
        return "incomplete"
    if critical_failures > 0:
        return "none"
    security = subscores.get("security", 0.0)
    # The floor is over WEIGHTED dimensions only. Taxonomy dimensions are reported
    # alongside the composite and carry no weight, so a low score on one must not
    # silently demote a tier -- that would fold them into the grade through the back
    # door while the weights table still said they were not in it. They are kept in
    # a separate registry so they never arrive here at all; this is the guard for the
    # day somebody merges the two dicts for convenience.
    weighted = {d: v for d, v in subscores.items() if d in config.DIMENSION_WEIGHTS}
    min_dim = min(weighted.values()) if weighted else 0.0
    for tier, (c_floor, sec_floor, min_floor) in config.TIERS.items():
        if composite >= c_floor and security >= sec_floor and min_dim >= min_floor:
            return tier
    return "none"


def score_single_run(dim_results: dict[str, DimensionResult]) -> GradeResult:
    subscores = {d: r.subscore for d, r in dim_results.items()}
    criticals = sum(len(r.critical_failures) for r in dim_results.values())
    composite, incomplete, capped = compute_composite(subscores, criticals)
    tier = assign_tier(composite, subscores, criticals, incomplete)
    agreement = _agreement_summary(dim_results)
    return GradeResult(
        composite=composite, tier=tier, critical_failures=criticals, subscores=subscores,
        graded_dimensions=sorted(subscores), incomplete=incomplete, capped=capped,
        confidence={"judge_agreement": agreement} if agreement else {},
    )


# Two-sided 95% Student's t by degrees of freedom. A table rather than a
# dependency: scipy is not installed here, and a CI formula is not worth a new
# runtime dependency. df >= 30 is within 2% of the normal, so 1.96 is the floor.
_T_975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
          8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 15: 2.131,
          20: 2.086, 25: 2.060, 30: 2.042}


def aggregate_runs(runs: list[GradeResult]) -> GradeResult:
    """Combine multiple runs into a variance-aware grade (methodology section 3)."""
    if not runs:
        raise ValueError("no runs to aggregate")
    if len(runs) == 1:
        r = runs[0]
        agr = r.confidence.get("judge_agreement")
        # ⛔ NULL, NOT A ZERO-WIDTH INTERVAL. [x, x] asserts PERFECT precision, and
        # nothing downstream can tell that claim from a genuinely tight measurement
        # - a success signal that cannot represent failure. One run supports no
        # interval at all, so it reports none and every consumer must handle the
        # absence rather than read a false certainty.
        #
        # ⛔ AND [40.0, 40.0] ELSEWHERE ON THIS BOARD IS THE SAME DEFECT BY A SECOND
        # PATH - NOT, as an earlier version of this very comment claimed, a genuine
        # zero. That grade is 3 runs all hitting the critical-failure cap, and the
        # cap flattens them to exactly 40.0 BEFORE aggregation sees them. Measured on
        # the stored Onyx artifact: the uncapped per-run composites are 86.68 / 86.24
        # / 86.37, spread 0.1846. So the reported variance of 0.0 is the determinism
        # of the CAP, not the stability of the agent - and `variance: 0.0` is the
        # worse half, because it is the smallest number on the board and presents a
        # capped agent as the MOST consistent thing we publish.
        #
        # Left unfixed deliberately: it needs a choice between reporting null (as
        # here) and reporting the UNCAPPED interval clearly labelled, which is a
        # presentation decision about someone's published grade rather than a
        # tidy-up. Onyx is withheld and nothing capped is published, so it keeps.
        #
        # The wrong parenthetical sat here for a day, inside the comment arguing
        # against this exact class of false precision, and would have licensed the
        # defect to the next reader. Caught by aivonic-8f.
        r.confidence = {"runs": 1, "variance": None, "ci95_low": None, "ci95_high": None,
                        "ci95_unavailable": "a single run supports no interval"}
        if agr:
            r.confidence["judge_agreement"] = agr
        return r

    composites = [r.composite for r in runs]
    mean_c = statistics.mean(composites)
    n = len(composites)

    # ⛔ TWO DIFFERENT STANDARD DEVIATIONS, ON PURPOSE. One name was doing both
    # jobs and only one of them was wrong.
    #
    # DESCRIPTIVE (pstdev, divides by n): how far apart the runs we ACTUALLY HAVE
    # sit. That is a property of these three numbers, not an estimate of anything
    # wider, and pstdev is correct for it. It feeds stability_part below, which
    # feeds the PUBLISHED COMPOSITE - so changing it here would silently move
    # every grade on the board. Measured: Langflow -0.03, Dify -0.02, Typebot and
    # CrewAI -0.01. Four of five visible at two decimals. Left alone deliberately.
    #
    # INFERENTIAL (sample stdev + Student's t): the CI infers the spread of the
    # population these runs are drawn from, and both of the old terms understated
    # it. pstdev divides by n rather than n-1 (x1.2247 at n=3) and 1.96 is the
    # NORMAL quantile where 2 degrees of freedom need t(.975,2)=4.303 (x2.1952).
    # Compounded, the published intervals were 2.69x TOO NARROW - on a benchmark
    # whose entire claim is that it publishes its uncertainty honestly.
    # ⛔ DISPERSION IS A PROPERTY OF THE MEASUREMENT, NOT OF THE CEILING.
    #
    # A critical failure caps a composite at CRITICAL_CAP, and the cap is applied
    # PER RUN, before aggregation sees them. So every run of a capped agent is
    # flattened to exactly 40.0 and every dispersion statistic computed from those
    # numbers describes the determinism of the CAP rather than the stability of the
    # agent. Measured on the stored Onyx artifact:
    #
    #     capped per-run    [40.0, 40.0, 40.0]      pstdev 0.0000
    #     uncapped per-run  [86.68, 86.24, 86.37]   pstdev 0.1846
    #
    # That produced two wrong published numbers, not one. `variance: 0.0` and a
    # zero-width interval assert perfect precision -- the same defect the n=1 branch
    # above refuses, reached by a second path. And stability_part = 10 - stdev fed a
    # PUBLISHED SUBSCORE, so a capped agent was credited with flawless run-to-run
    # stability: latency_and_reliability 9.85 where the real figure is 9.78.
    #
    # So dispersion is computed from the uncapped composites throughout. This is a
    # NO-OP for every agent without a critical failure, by construction: with no
    # criticals the capped and uncapped per-run composites are identical. Only a
    # capped grade moves, and only onto its true values.
    uncapped_runs = [compute_composite(r.subscores, 0)[0] for r in runs]

    stdev = statistics.pstdev(uncapped_runs)                       # descriptive
    sample_stdev = statistics.stdev(uncapped_runs) if n > 1 else 0.0   # inferential
    var = statistics.pvariance(uncapped_runs)
    half = _T_975.get(n - 1, 1.96) * sample_stdev / (n ** 0.5)

    # Mean each dimension across runs; criticals counted if any run flagged one.
    dims = runs[0].subscores.keys()
    mean_sub = {d: round(statistics.mean(r.subscores[d] for r in runs), 2) for d in dims}
    criticals = max(r.critical_failures for r in runs)

    # Reliability = cross-run stability, the "and reliability" half of dimension 12 that
    # a single run cannot see. An agent whose composite swings run-to-run is less
    # trustworthy than a steady one at the same mean, so the dimension folds a stability
    # component (from composite stdev) in with the per-probe latency component.
    reliability_meta = None
    if config.RELIABILITY_DIM in mean_sub:
        latency_part = mean_sub[config.RELIABILITY_DIM]                 # mean per-probe latency score, 0..10
        stability_part = round(max(0.0, 10.0 - stdev), 2)       # composite stdev in points; 0 stdev -> 10
        mean_sub[config.RELIABILITY_DIM] = round(config.LATENCY_W * latency_part + config.STABILITY_W * stability_part, 2)
        reliability_meta = {"latency_component": latency_part, "stability_component": stability_part,
                            # From the UNCAPPED per-run composites: the cap's determinism
                            # is not the agent's stability. Identical for any
                            # agent without a critical failure.
                            "composite_stdev": round(stdev, 2)}

    composite, incomplete, capped = compute_composite(mean_sub, criticals)
    tier = assign_tier(composite, mean_sub, criticals, incomplete)

    # Average cross-lab judge agreement across runs (surfaced, not hidden).
    agrs = [r.confidence.get("judge_agreement") for r in runs if r.confidence.get("judge_agreement")]
    merged_agr = None
    if agrs:
        all_dims = set().union(*(a["per_dimension"].keys() for a in agrs))
        per = {}
        for d in all_dims:
            vals = [a["per_dimension"][d] for a in agrs if d in a["per_dimension"]]
            per[d] = round(sum(vals) / len(vals), 3)
        low = sorted((d for d, v in per.items() if v < LOW_AGREEMENT), key=lambda d: per[d])
        merged_agr = {"overall": round(sum(per.values()) / len(per), 3),
                      "per_dimension": per, "low_agreement_dimensions": low}

    # ⛔ A CAPPED COMPOSITE IS A CEILING, NOT A MEASUREMENT, SO IT CARRIES NO
    # INTERVAL. 40.0 is an administrative verdict: it is exact, it has no
    # uncertainty, and an interval around it would describe nothing. Reported null
    # for the same reason n=1 reports null.
    #
    # The uncertainty has not vanished, it belongs to a different number. The board
    # already publishes what a capped grade was capped FROM (certs.py computes
    # capped_from, render.py::_cap_line explains it), and THAT is the measurement,
    # so the interval attaches there. A vendor then reads "capped at 40 for a
    # critical failure; underlying 86.43, stable to +/-0.2 across runs" instead of
    # either half alone.
    #
    # Deliberately NOT presented as an interval around the published composite: an
    # interval spanning 86 beside a published 40 invites "really they are 86",
    # which is the reading the cap exists to prevent.
    capped_from = round(compute_composite(mean_sub, 0)[0], 2) if capped else None

    confidence = {
        "runs": len(runs),
        "variance": None if capped else round(var, 3),
        # ⛔ CENTRED ON `composite`, NOT `mean_c`. They are different quantities and
        # the interval must belong to the number printed beside it.
        #
        # `composite` comes from compute_composite(mean_sub), and mean_sub's
        # reliability dimension contains stability_part = 10 - stdev - a CROSS-RUN
        # term that cannot exist in any single run's composite, and therefore
        # cannot exist in mean_c. So the published score sat outside its own
        # published interval whenever that term was large enough: SPARK, 2026-09-18,
        # composite 88.49 against CI [87.89, 88.36], outside by +0.13. A reviewer
        # checking "is the score inside its own CI" finds that in ten seconds.
        #
        # The half-width is still the right dispersion estimate: stability_part is
        # a deterministic function of the same per-run dispersion, so it adds no
        # independent variance - only an offset, which re-centring removes exactly.
        "ci95_low": None if capped else round(max(0.0, composite - half), 2),
        "ci95_high": None if capped else round(min(100.0, composite + half), 2),
    }
    if capped:
        confidence["ci95_unavailable"] = (
            "the composite is capped at the critical-failure ceiling, which is an exact "
            "verdict rather than a measurement and supports no interval"
        )
        confidence["capped_from"] = capped_from
        confidence["capped_from_variance"] = round(var, 3)
        confidence["capped_from_ci95_low"] = round(max(0.0, capped_from - half), 2)
        confidence["capped_from_ci95_high"] = round(min(100.0, capped_from + half), 2)
    if merged_agr:
        confidence["judge_agreement"] = merged_agr
    if reliability_meta:
        confidence["reliability"] = reliability_meta
    return GradeResult(
        composite=composite, tier=tier, critical_failures=criticals, subscores=mean_sub,
        graded_dimensions=sorted(mean_sub), incomplete=incomplete, capped=capped,
        confidence=confidence,
    )
