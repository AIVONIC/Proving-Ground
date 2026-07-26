"""Reliability, difficulty, and severity views over a multi-run grade.

These read the per-probe transcripts from every run (``all_dim_results``) and
derive three cross-cutting reports that a single composite number hides:

* ``pass_k_curve`` - consistency under repetition. A probe "passes^k" only if it
  passed on ALL k runs. pass^1 is the ordinary mean pass rate; pass^k for k>1
  decays toward the fraction of probes the agent solves *every single time*. An
  agent that is right on average but flaky run-to-run is less trustworthy than a
  steady one at the same mean, and this curve is where that shows. (A generic
  reliability-under-repetition measure; not tied to any one benchmark.)

* ``difficulty_breakdown`` - pass rate per difficulty tier (L1/L2/L3), so a tier
  means something and saturation is visible (all-L1-pass while L3 fails is a
  different agent from one that is uniformly mediocre).

* ``severity_summary`` - when a security/safety detector fires, how bad was it?
  Counts breaches by graded severity so "blocked" is never conflated with an
  irreversible credential leak. This is the graded-severity view; a plain
  pass/fail bit cannot express it.

Transport errors (our side) are excluded everywhere: they are not the agent's
pass or failure.
"""

from __future__ import annotations

from app.dimensions.base import DimensionResult, probe_difficulty
from app.dimensions.checks import SEVERITY_ORDER


def _by_probe(all_dim_results: list[dict[str, DimensionResult]]) -> dict[str, list]:
    """probe_id -> list of ProbeResult across runs (transport errors dropped)."""
    seen: dict[str, list] = {}
    for run in all_dim_results:
        for dr in run.values():
            for pr in dr.probe_results:
                if pr.error is not None:
                    continue
                seen.setdefault(pr.probe_id, []).append(pr)
    return seen


def pass_k_curve(all_dim_results: list[dict[str, DimensionResult]]) -> dict:
    """{'runs': R, 'curve': {1: pass^1, ..., R: pass^R}, 'n_probes': N}.

    pass^k = fraction of probes that passed on all of the first k runs. Only probes
    present in all R runs contribute to k>1 (a probe that errored out in one run
    can't be judged for cross-run consistency). Uses the probe's own pass flag.
    """
    by_probe = _by_probe(all_dim_results)
    runs = len(all_dim_results)
    if runs == 0 or not by_probe:
        return {"runs": runs, "curve": {}, "n_probes": 0}
    complete = {pid: prs for pid, prs in by_probe.items() if len(prs) == runs}
    curve: dict[int, float] = {}
    n = len(complete) or 1
    for k in range(1, runs + 1):
        consistent = sum(1 for prs in complete.values() if all(p.passed for p in prs[:k]))
        curve[k] = round(consistent / n, 3)
    # pass^1 over ALL probes (not just complete ones), the ordinary mean pass rate.
    all_first = [prs[0] for prs in by_probe.values()]
    if all_first:
        curve[1] = round(sum(1 for p in all_first if p.passed) / len(all_first), 3)
    return {"runs": runs, "curve": curve, "n_probes": len(complete)}


def difficulty_breakdown(all_dim_results: list[dict[str, DimensionResult]]) -> dict:
    """{'L1': {'n': .., 'pass_rate': ..}, ...} over every (probe, run) observation."""
    buckets: dict[str, list[bool]] = {}
    for run in all_dim_results:
        for dr in run.values():
            for pr in dr.probe_results:
                if pr.error is not None:
                    continue
                tier = probe_difficulty(pr.category)
                buckets.setdefault(tier, []).append(pr.passed)
    out = {}
    for tier in sorted(buckets):
        obs = buckets[tier]
        out[tier] = {"n": len(obs), "pass_rate": round(sum(obs) / len(obs), 3)}
    return out


def severity_summary(all_dim_results: list[dict[str, DimensionResult]]) -> dict:
    """Count of triggered breaches by graded severity, worst first. Empty when the
    agent had no detector fire (the clean case). Only counts actual failures."""
    counts: dict[str, int] = {}
    for run in all_dim_results:
        for dr in run.values():
            for pr in dr.probe_results:
                if pr.error is not None or pr.passed:
                    continue
                sev = getattr(pr, "severity", "none")
                if sev and sev != "none":
                    counts[sev] = counts.get(sev, 0) + 1
    return {s: counts[s] for s in reversed(SEVERITY_ORDER) if s in counts}


def format_reliability(all_dim_results: list[dict[str, DimensionResult]]) -> str:
    """Human-readable block for the CLI report."""
    pk = pass_k_curve(all_dim_results)
    diff = difficulty_breakdown(all_dim_results)
    sev = severity_summary(all_dim_results)
    lines = ["Reliability & stratification:"]
    if pk["curve"]:
        curve = "  ".join(f"pass^{k}={v}" for k, v in sorted(pk["curve"].items()))
        lines.append(f"  consistency ({pk['n_probes']} probes x {pk['runs']} runs): {curve}")
    if diff:
        strat = "  ".join(f"{t}: {d['pass_rate']} (n={d['n']})" for t, d in diff.items())
        lines.append(f"  by difficulty: {strat}")
    lines.append(f"  breach severity: {sev if sev else 'none (no detector fired)'}")
    return "\n".join(lines)
