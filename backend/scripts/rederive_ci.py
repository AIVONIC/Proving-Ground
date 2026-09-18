#!/usr/bin/env python3
"""Recompute confidence intervals from existing artifacts. No judge calls.

WHY THIS IS NOT A RE-GRADE. The interval is computed at AGGREGATE time from
per-run composites, and those are deterministic from probe scores already stored.
Re-measuring would move the numbers for TWO reasons at once - the corrected
formula and fresh judge non-determinism - making it impossible to attribute the
change to the fix. Recomputation isolates it.

It drives the PRODUCTION aggregate_runs rather than re-implementing the formula,
because a check that re-implements the thing it checks proves the
re-implementation works (signature 10).

    python3 scripts/rederive_ci.py            # report
    python3 scripts/rederive_ci.py --apply    # write the corrected block back
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The reliability blend moved to scoring/config.py on 2026-09-18 so the composite's
# derived identifier can see it; scorer.py reads it through the module now.
from app.scoring.config import LATENCY_W, RELIABILITY_DIM, STABILITY_W
from app.scoring.scorer import GradeResult, aggregate_runs, compute_composite

BACKEND = Path(__file__).resolve().parents[1]


def _per_run(d: dict) -> list[GradeResult]:
    out = []
    for run in d["runs"]:
        subs = {}
        for dim, probes in run.items():
            if not isinstance(probes, list) or not probes:
                continue
            v = [p["score"] for p in probes
                 if isinstance(p, dict) and p.get("score") is not None]
            if v:
                subs[dim] = round(statistics.mean(v) * 10, 2)
        c, inc, cap = compute_composite(subs, 0)
        out.append(GradeResult(composite=c, tier="", critical_failures=0,
                               subscores=subs, incomplete=inc, capped=cap,
                               graded_dimensions=sorted(subs), confidence={}))
    return out


def main() -> int:
    apply = "--apply" in sys.argv
    print(f"{'agent':10} {'composite':>10} {'was':>8} {'old CI':<18} {'new CI':<18} "
          f"{'inside?':<8} offset oracle")
    bad = 0
    for f in sorted((BACKEND / "data" / "runs").glob("*_2026091[78]*.json")):
        if ".INVALID" in f.name or ".pre-" in f.name:
            continue
        d = json.loads(f.read_text())
        g, oldc = d["grade"], d["grade"]["confidence"]
        runs = _per_run(d)
        if len(runs) < 2:
            continue
        new = aggregate_runs(runs)
        nc = new.confidence

        # ORACLE: the pre-fix gap between the published composite and mean_c is
        # predicted by 0.16 * (10 - sigma - L). If the recomputation reproduces
        # that, the mechanism is understood - a stronger check than "nothing moved".
        comps = [r.composite for r in runs]
        sigma = statistics.pstdev(comps)
        L = round(statistics.mean(r.subscores[RELIABILITY_DIM] for r in runs), 2)
        predicted = 0.16 * (10.0 - sigma - L)
        observed = g["composite"] - statistics.mean(comps)

        same = abs(new.composite - g["composite"]) < 1e-9
        inside = nc["ci95_low"] <= new.composite <= nc["ci95_high"]
        if not same or not inside or abs(predicted - observed) > 0.02:
            bad += 1
        print(f"{f.name.split('_')[0][:10]:10} {new.composite:>10.2f} {g['composite']:>8.2f} "
              f"[{oldc['ci95_low']:.2f}, {oldc['ci95_high']:.2f}]".ljust(59)
              + f"[{nc['ci95_low']:.2f}, {nc['ci95_high']:.2f}]".ljust(19)
              + f"{'yes' if inside else 'NO':<8} "
              f"pred {predicted:+.3f} obs {observed:+.3f} "
              f"{'ok' if abs(predicted-observed) <= 0.02 else 'MISMATCH'}"
              + ("" if same else "   ⛔ COMPOSITE MOVED"))
        if apply:
            d["grade"]["confidence"] = {**oldc, **nc}
            f.write_text(json.dumps(d, indent=2) + "\n")
    if apply:
        print("\nwritten back. re-promote and re-render.")
    print(f"\n{'OK' if bad == 0 else f'{bad} PROBLEM(S)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
