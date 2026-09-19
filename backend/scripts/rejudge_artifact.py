#!/usr/bin/env python3
"""Re-judge an artifact's STORED responses with today's judges.

⛔ THE QUESTION THIS ANSWERS, WHICH RE-RUNNING CANNOT. When a grade moves between
two dates, three things could have changed: the agent's replies, the judges, or
the scoring. Re-running the agent samples a distribution you have already sampled
and cannot speak to the past run at all - the variable you would need to hold is
time. Re-judging the STORED replies holds the agent fixed by construction, so any
difference is judge-side.

Raised by aivonic-52 against my own proposal to re-run the agent a sixth time.

Aggregation is checked separately and for free (recompute the stored per-probe
scores under today's code); if that reproduces, only the judges are left.

⛔ TRUNCATION CONFOUND. Artifacts written before 2026-09-18 store response[:500];
newer ones store 2000. Comparing across that boundary measures the storage change
unless the newer text is truncated to match. This script reports the truncated
fraction and refuses above a threshold rather than quietly averaging it in.

    python3 scripts/rejudge_artifact.py --artifact <path>            # dry run
    python3 scripts/rejudge_artifact.py --artifact <path> --run --n 50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
_ENV = Path(os.environ.get("PG_ENV_FILE", BACKEND / ".env"))
if _ENV.exists():
    for _l in _ENV.read_text().splitlines():
        if "=" in _l and not _l.strip().startswith("#"):
            _k, _, _v = _l.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.dimensions.catalog import REGISTRY  # noqa: E402
from app.judges.judge import build_ensemble  # noqa: E402

_T = {29: 2.045, 49: 2.010, 99: 1.984, 199: 1.972}
MAX_TRUNCATED = 0.15


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20260918)
    ap.add_argument("--observed", type=float, metavar="POINTS",
                    help="the composite-point change being explained. Without it "
                         "the script reports judge movement and draws no "
                         "conclusion, because movement alone is not a cause.")
    a = ap.parse_args()

    d = json.loads(Path(a.artifact).read_text())
    cat = {}
    for f in (BACKEND / "data" / "private").glob("*.json"):
        try:
            j = json.loads(f.read_text())
        except Exception:
            continue
        for p in (j if isinstance(j, list) else j.get("probes", [])):
            if isinstance(p, dict) and p.get("id"):
                cat[p["id"]] = p

    items, trunc = [], 0
    for run in d["runs"]:
        for dim, ps in run.items():
            if not isinstance(ps, list):
                continue
            for p in ps:
                if not isinstance(p, dict) or not (p.get("judge_meta") or {}).get("per_judge"):
                    continue
                if p["probe_id"] not in cat:
                    continue
                r = p.get("response") or ""
                if len(r) >= 500:
                    trunc += 1
                items.append({"dim": dim, "id": p["probe_id"], "response": r,
                              "prompt": cat[p["probe_id"]].get("prompt", ""),
                              "context": cat[p["probe_id"]].get("context"),
                              "family": p.get("family") or "", "stored": p["score"]})
    if not items:
        print("UNRELIABLE: no judged probes could be joined to prompts. Not an answer.")
        return 2
    frac = trunc / len(items)
    print(f"   artifact         : {Path(a.artifact).name}")
    print(f"   judged probes    : {len(items)}   truncated at 500: {trunc} ({100*frac:.1f}%)")
    if frac > MAX_TRUNCATED:
        print(f"   ⛔ REFUSING: {100*frac:.0f}% of stored replies are truncated. A re-judge "
              "would measure the storage cap, not the judges.")
        return 2

    random.Random(a.seed).shuffle(items)
    bydim = defaultdict(list)
    for it in items:
        bydim[it["dim"]].append(it)
    sample, i = [], 0
    while len(sample) < min(a.n, len(items)) and i < 200:
        for k in sorted(bydim):
            if i < len(bydim[k]) and len(sample) < a.n:
                sample.append(bydim[k][i])
        i += 1
    print(f"   sample           : {len(sample)} probes, ~{len(sample)*4} calls, "
          f"~${len(sample)*4*0.00558:.2f}")
    if not a.run:
        print("\n   DRY RUN - nothing called, nothing spent.")
        return 0

    judge = build_ensemble("")
    dims = {k: (v[0] if isinstance(v, tuple) else v)() for k, v in REGISTRY.items()}

    async def go():
        for it in sample:
            dim = dims.get(it["dim"])
            if dim is None or not getattr(dim, "rubric", ""):
                continue
            j = await judge.score_criteria(it["prompt"], it["response"], dim.rubric,
                                           context=it["context"] or None)
            it["now"] = j.score
    asyncio.run(go())

    diffs = [it["now"] - it["stored"] for it in sample if "now" in it]
    n = len(diffs)
    m = statistics.mean(diffs)
    sd = statistics.stdev(diffs)
    half = _T.get(n - 1, 1.96) * sd / n ** 0.5
    print(f"\n   re-judged {n} of the SAME stored replies with today's judges")
    print(f"   mean score change : {m:+.4f}   95% CI [{m-half:+.4f}, {m+half:+.4f}]")
    print(f"   = composite points: {10*m:+.2f}   [{10*(m-half):+.2f}, {10*(m+half):+.2f}]")
    moved = m - half > 0 or m + half < 0
    print(f"\n   judges {'MOVED' if moved else 'did NOT move'} on these replies "
          f"({10*m:+.2f} points).")

    # ⛔ "DID THE JUDGES MOVE" AND "DO THE JUDGES EXPLAIN THE GRADE CHANGE" ARE TWO
    # QUESTIONS, AND THE FIRST VERSION OF THIS SCRIPT ANSWERED THE FIRST WHILE
    # REPORTING THE SECOND. On CrewAI it found the judges moved -0.49 and concluded
    # "the grade's change is judge-side" - but the grade had moved UP by 1.90, so the
    # judge movement ran OPPOSITE to it and made the agent's share LARGER, not
    # smaller. Direction and magnitude both matter; a non-zero result is not a cause.
    if a.observed is not None:
        agent_share = a.observed - 10 * m
        print(f"   observed grade change      {a.observed:+.2f} points")
        print(f"   judge-side movement        {10*m:+.2f} points")
        print(f"   => attributable to AGENT   {agent_share:+.2f} points")
        if moved and (10 * m) * a.observed > 0 and abs(10 * m) >= abs(a.observed) * 0.5:
            print("\n   The judges moved in the SAME direction and account for a substantial "
                  "part of the change. Treat the grade move as largely ours.")
        elif moved:
            print("\n   ⛔ The judges moved, but NOT in a way that explains the grade change - "
                  "it is the wrong direction or too small. The agent's share is LARGER "
                  "than the headline, and nothing in git explains it. Publish that as a "
                  "limitation rather than asserting a cause.")
        else:
            print("\n   The judges are stable on these replies, so the change is the AGENT's.")
    else:
        print("   (pass --observed <points> to attribute the change; without it this "
              "reports judge movement only, which is not a cause.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
