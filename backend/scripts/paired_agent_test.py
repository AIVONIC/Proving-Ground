#!/usr/bin/env python3
"""Compare two agents with the pairing INTACT, through the real weighted composite.

⛔ WHY INTERVAL OVERLAP UNDERSTATES THIS BOARD. Every agent answers the SAME 157
probes, so an agent-vs-agent comparison is PAIRED and probe difficulty - a large
variance term - cancels. Declaring a tie because two 95% intervals overlap throws
that pairing away and asks for roughly twice the evidence the paired question
needs. Raised by aivonic-52, who found the identical error in their own
generation-to-generation rollback rule.

⛔ AND THE OBVIOUS FIX IS ALSO WRONG. A paired t-test on per-probe score
differences answers "is A better on a TYPICAL PROBE", which is not the composite:
`security` is 36.3% of probes and 16% of weight, `task_success` is 4.5% of probes
and 18% of weight - a 20.3 point divergence. I ran exactly that test first and it
produced a clean, plausible, wrong answer.

So: bootstrap the PROBES (paired - the same resampled probe ids for both agents),
recompute each agent's real weighted composite on each resample, and read the
distribution of the difference. The weighting is applied by production code.
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.scoring.scorer import compute_composite  # noqa: E402


def probe_table(agent: str) -> dict[str, tuple[str, float]]:
    """probe_id -> (dimension, mean score across every run of this agent)"""
    acc: dict[str, list] = defaultdict(list)
    dim_of: dict[str, str] = {}
    files = [f for f in glob.glob(f"{BACKEND}/data/runs/{agent}_2026091[78]*.json")
             if ".INVALID" not in f and ".pre-" not in f]
    for f in files:
        for run in json.loads(Path(f).read_text())["runs"]:
            for dim, ps in run.items():
                if not isinstance(ps, list):
                    continue
                for p in ps:
                    if isinstance(p, dict) and p.get("score") is not None:
                        acc[p["probe_id"]].append(p["score"])
                        dim_of[p["probe_id"]] = dim
    return {k: (dim_of[k], statistics.mean(v)) for k, v in acc.items()}


def composite_from(tbl: dict, ids: list[str]) -> float:
    by_dim: dict[str, list] = defaultdict(list)
    for i in ids:
        d, s = tbl[i]
        by_dim[d].append(s)
    subs = {d: round(statistics.mean(v) * 10, 2) for d, v in by_dim.items() if v}
    return compute_composite(subs, 0)[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20260918)
    a = ap.parse_args()

    agents = ["crewai-northwind", "dify-northwind", "flowise-northwind",
              "typebot-northwind", "langflow-northwind", "spark"]
    tbl = {g: probe_table(g) for g in agents}
    tbl = {g: t for g, t in tbl.items() if t}
    rnd = random.Random(a.seed)

    names = sorted(tbl, key=lambda g: -composite_from(tbl[g], list(tbl[g])))
    print(f"   {'pair':44}{'diff':>9}{'95% CI (paired bootstrap)':>28}{'verdict':>10}")
    dist = tie = 0
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            shared = sorted(set(tbl[x]) & set(tbl[y]))
            if len(shared) < 20:
                continue
            point = composite_from(tbl[x], shared) - composite_from(tbl[y], shared)
            diffs = []
            for _ in range(a.iters):
                # SAME resampled ids for both agents - that is the pairing
                s = [shared[rnd.randrange(len(shared))] for _ in shared]
                diffs.append(composite_from(tbl[x], s) - composite_from(tbl[y], s))
            diffs.sort()
            lo = diffs[int(0.025 * len(diffs))]
            hi = diffs[int(0.975 * len(diffs))]
            d = lo > 0 or hi < 0
            dist += d
            tie += not d
            print(f"   {x[:20]:21}vs {y[:20]:21}{point:>+9.2f}   [{lo:>+7.2f},{hi:>+7.2f}]"
                  f"{'DISTINCT' if d else 'tie':>10}")
    print(f"\n   paired bootstrap: {dist} distinct, {tie} ties  "
          f"({len(shared)} shared probes, {a.iters} resamples)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
