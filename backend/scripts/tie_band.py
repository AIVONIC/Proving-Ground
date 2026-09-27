#!/usr/bin/env python3
"""Decide which board orderings are publishable, on the right statistic.

⛔ NON-OVERLAPPING INTERVALS IS THE WRONG TEST, AND IT IS WRONG IN BOTH DIRECTIONS.
Two estimates can overlap and still differ significantly; they can fail to overlap
and not. Requiring two 95% intervals to be disjoint demands roughly 2.8 standard
errors of separation where the two-sample question needs about 2.0, so it declares
ties that are not ties - and it is not conservative in the reassuring sense, because
the error runs the other way too. Raised by aivonic-52.

This runs Welch's t on the per-run composites, which is the test the board's claim
is actually about ("would these agents' composites differ on a re-run"), and then
ALSO requires the gap to clear the measured judge-drift bound, because a difference
smaller than the drift we cannot exclude is not publishable however significant it is.

Both conditions, deliberately: statistics answer "is it real", the drift bound
answers "is it bigger than what we cannot rule out".
"""
from __future__ import annotations

import glob
import json
import math
import statistics
import sys
from itertools import combinations
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.scoring.scorer import compute_composite  # noqa: E402

DRIFT_NOT_EXCLUDED = 0.47   # composite points, measured 2026-09-18 at n=100
_T = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306}


def run_composites(path: str) -> list[float]:
    out = []
    for run in json.loads(Path(path).read_text())["runs"]:
        subs, crit = {}, 0
        for dim, ps in run.items():
            if not isinstance(ps, list) or not ps:
                continue
            v = [p["score"] for p in ps if isinstance(p, dict) and p.get("score") is not None]
            crit += sum(1 for p in ps if isinstance(p, dict) and p.get("critical"))
            if v:
                subs[dim] = round(statistics.mean(v) * 10, 2)
        out.append(compute_composite(subs, crit)[0])
    return out


def welch(a: list[float], b: list[float]):
    ma, mb = statistics.mean(a), statistics.mean(b)
    va, vb = statistics.variance(a), statistics.variance(b)
    na, nb = len(a), len(b)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return ma - mb, 0.0, float("inf")
    df = (va / na + vb / nb) ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    t = _T.get(int(round(df)), 1.96)
    return ma - mb, t * se, df


def main() -> int:
    agents = {}
    for f in sorted(glob.glob(str(BACKEND / "data/runs/merged/*_n5.json"))):
        name = json.loads(Path(f).read_text())["agent"]
        if name == "spark":       # published but RECUSED - not a board ordering
            continue
        agents[name] = run_composites(f)
    order = sorted(agents, key=lambda k: -statistics.mean(agents[k]))
    print(f"   {'pair':30}{'gap':>7}{'95% CI of gap':>20}{'sig?':>6}{'>drift?':>9}{'verdict':>10}")
    distinct = []
    for a, b in combinations(order, 2):
        d, half, df = welch(agents[a], agents[b])
        sig = abs(d) > half
        big = abs(d) > DRIFT_NOT_EXCLUDED
        ok = sig and big
        if ok:
            distinct.append((a, b))
        print(f"   {a.split('-')[0][:13]:14}vs {b.split('-')[0][:13]:14}{d:>7.2f}"
              f"   [{d-half:+.2f},{d+half:+.2f}]".rjust(20)
              + f"{'yes' if sig else 'no':>6}{'yes' if big else 'no':>9}"
              + f"{'DISTINCT' if ok else 'tie':>10}")
    n = len(order)
    print(f"\n   publishable orderings: {len(distinct)} of {n*(n-1)//2}")
    tied = [a for a in order if not any(a in p for p in distinct)]
    print(f"   agents in no distinct pair (fully tied): {tied or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
