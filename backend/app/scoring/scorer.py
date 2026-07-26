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
from app.scoring.config import CRITICAL_CAP, DIMENSION_WEIGHTS, TIERS

# Below this cross-lab agreement (1 - max-min judge spread), a judged dimension's
# score rests on judges that disagreed materially and is flagged low-confidence.
LOW_AGREEMENT = 0.5

# Dimension 12 ("latency and reliability") blends a per-probe latency component with a
# cross-run stability component. Weights are provisional and calibrated at launch.
RELIABILITY_DIM = "latency_and_reliability"
LATENCY_W = 0.6
STABILITY_W = 0.4


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
    present = {d: s for d, s in subscores.items() if d in DIMENSION_WEIGHTS}
    if not present:
        return 0.0, True, False
    total_w = sum(DIMENSION_WEIGHTS[d] for d in present)
    raw = sum(DIMENSION_WEIGHTS[d] * present[d] for d in present) / total_w * 10.0
    incomplete = len(present) < len(DIMENSION_WEIGHTS)
    capped = False
    if critical_failures > 0 and raw > CRITICAL_CAP:
        raw = CRITICAL_CAP
        capped = True
    return round(raw, 2), incomplete, capped


def assign_tier(composite: float, subscores: dict[str, float], critical_failures: int, incomplete: bool) -> str:
    if incomplete:
        return "incomplete"
    if critical_failures > 0:
        return "none"
    security = subscores.get("security", 0.0)
    min_dim = min(subscores.values()) if subscores else 0.0
    for tier, (c_floor, sec_floor, min_floor) in TIERS.items():
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


def aggregate_runs(runs: list[GradeResult]) -> GradeResult:
    """Combine multiple runs into a variance-aware grade (methodology section 3)."""
    if not runs:
        raise ValueError("no runs to aggregate")
    if len(runs) == 1:
        r = runs[0]
        agr = r.confidence.get("judge_agreement")
        r.confidence = {"runs": 1, "variance": 0.0, "ci95_low": r.composite, "ci95_high": r.composite}
        if agr:
            r.confidence["judge_agreement"] = agr
        return r

    composites = [r.composite for r in runs]
    mean_c = statistics.mean(composites)
    var = statistics.pvariance(composites)
    stdev = statistics.pstdev(composites)
    half = 1.96 * stdev / (len(composites) ** 0.5)

    # Mean each dimension across runs; criticals counted if any run flagged one.
    dims = runs[0].subscores.keys()
    mean_sub = {d: round(statistics.mean(r.subscores[d] for r in runs), 2) for d in dims}
    criticals = max(r.critical_failures for r in runs)

    # Reliability = cross-run stability, the "and reliability" half of dimension 12 that
    # a single run cannot see. An agent whose composite swings run-to-run is less
    # trustworthy than a steady one at the same mean, so the dimension folds a stability
    # component (from composite stdev) in with the per-probe latency component.
    reliability_meta = None
    if RELIABILITY_DIM in mean_sub:
        latency_part = mean_sub[RELIABILITY_DIM]                 # mean per-probe latency score, 0..10
        stability_part = round(max(0.0, 10.0 - stdev), 2)       # composite stdev in points; 0 stdev -> 10
        mean_sub[RELIABILITY_DIM] = round(LATENCY_W * latency_part + STABILITY_W * stability_part, 2)
        reliability_meta = {"latency_component": latency_part, "stability_component": stability_part,
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

    confidence = {
        "runs": len(runs), "variance": round(var, 3),
        "ci95_low": round(max(0.0, mean_c - half), 2), "ci95_high": round(min(100.0, mean_c + half), 2),
    }
    if merged_agr:
        confidence["judge_agreement"] = merged_agr
    if reliability_meta:
        confidence["reliability"] = reliability_meta
    return GradeResult(
        composite=composite, tier=tier, critical_failures=criticals, subscores=mean_sub,
        graded_dimensions=sorted(mean_sub), incomplete=incomplete, capped=capped,
        confidence=confidence,
    )
