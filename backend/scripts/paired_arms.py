#!/usr/bin/env python3
"""Compare two re-judge arms PAIRED by probe, which is the statistic that matters.

⛔ TWO LEVELS ARE NOT A DIFFERENTIAL. On 2026-09-19 a treatment arm and a control
arm came back at -0.0208 and -0.0207 and were read as "one common-mode shift, no
differential". But the hypothesis was about crewai MINUS typebot, and with the same
items and seed on both arms that difference is PAIRED: probe difficulty cancels, and
its interval is far narrower than the interval on either level.

Reported as two overlapping levels, "no differential detected" is indistinguishable
from "underpowered to detect one". Those are different conclusions and only the
paired form separates them. Raised by aivonic-52.

    python3 scripts/paired_arms.py --a <treatment.json> --b <control.json>
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

_T = {29: 2.045, 49: 2.010, 99: 1.984, 199: 1.972, 399: 1.966}


def load(p: str) -> dict:
    d = json.loads(Path(p).read_text())
    # key on (run, probe_id) so the two arms join item-for-item
    return {(i["run"], i["probe_id"]): i for i in d["items"]}, d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="treatment arm json")
    ap.add_argument("--b", required=True, help="control arm json")
    ap.add_argument("--label", default="treatment - control")
    a = ap.parse_args()

    A, da = load(a.a)
    B, db = load(a.b)
    shared = sorted(set(A) & set(B))
    if not shared:
        print("UNRELIABLE: the two arms share no items. Not a result.")
        return 2
    # ⛔ REFUSE a comparison across different seeds: different samples are not paired,
    # and pairing them by probe_id would silently compare unlike items.
    if da.get("seed") != db.get("seed"):
        print(f"REFUSING: seeds differ ({da.get('seed')} vs {db.get('seed')}). "
              "Different samples are not paired.")
        return 2

    d = [A[k]["now"] - B[k]["now"] for k in shared]
    n = len(d)
    m = statistics.mean(d)
    sd = statistics.stdev(d) if n > 1 else 0.0
    half = _T.get(n - 1, 1.96) * sd / n ** 0.5 if n > 1 else 0.0
    print(f"   arms      : {da['agent']} [{da['arm']}] vs {db['agent']} [{db['arm']}]")
    print(f"   paired on : {n} items (run, probe_id), seed {da.get('seed')}")
    print(f"\n   PAIRED {a.label}: {m:+.4f}   95% CI [{m-half:+.4f}, {m+half:+.4f}]")
    print(f"   in composite points     : {10*m:+.3f}   [{10*(m-half):+.3f}, {10*(m+half):+.3f}]")
    excl = m - half > 0 or m + half < 0
    print(f"\n   interval {'EXCLUDES' if excl else 'SPANS'} zero -> "
          f"{'a real effect at this power' if excl else 'no effect detected AT THIS POWER (not: no effect)'}")
    # unpaired, for contrast - this is what the weaker framing would have reported
    ua = statistics.mean(A[k]["now"] - A[k]["was"] for k in shared)
    ub = statistics.mean(B[k]["now"] - B[k]["was"] for k in shared)
    sda = statistics.stdev([A[k]["now"] - A[k]["was"] for k in shared])
    sdb = statistics.stdev([B[k]["now"] - B[k]["was"] for k in shared])
    unp = ((sda ** 2 + sdb ** 2) / n) ** 0.5 * 1.96
    print(f"\n   for contrast, UNPAIRED: {ua-ub:+.4f} +/- {unp:.4f}  "
          f"({unp/half if half else float('inf'):.1f}x wider)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
