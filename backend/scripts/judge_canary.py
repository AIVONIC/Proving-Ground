#!/usr/bin/env python3
"""Detect whether a JUDGE changed underneath us, as distinct from an agent changing.

⛔ THE PROBLEM THIS EXISTS FOR. A grade moving tells you a number changed. It
cannot tell you WHICH SIDE moved. A vendor can ship a silent point release, a
routing change or a requantisation, and `claude-opus-5` is then a different judge
with the same name, announced by nothing. After the fact that is unfalsifiable:
a changed agent and a changed judge both present as a moved score.

⛔ WHY IT IS URGENT RIGHT NOW. The n=5 board MERGES three runs already taken with
two taken days later, and the entire merge argument is recompute-don't-re-measure
- which assumes the old and new per-run data are commensurable. If a panel model
shifted in between they are not, and the merged interval comes out NARROWER AND
WRONG. That is strictly worse than the two interval defects already fixed this
week, because a tighter interval reads as a better measurement.

Raised by aivonic-52 out of EVO's judge bake-off, where they measured a zero
within-session flip rate at temperature=0 and correctly refused to generalise it
across days: two calls seconds apart measure within-session reproducibility and
say nothing about next week.

HOW IT WORKS. Frozen (prompt, response) pairs lifted from stored artifacts are
re-scored by the CURRENT panel and compared against the verdicts stored with
them. The responses never change, so any movement is judge-side by construction.

    python3 scripts/judge_canary.py                    # dry run, spends nothing
    python3 scripts/judge_canary.py --run --n 30       # re-score 30 probes
    python3 scripts/judge_canary.py --run --baseline   # write a fresh baseline
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import hashlib
import statistics
import sys

_T_975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 9: 2.262,
          19: 2.093, 29: 2.045, 49: 2.010, 99: 1.984}
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_ENV = Path(os.environ.get("PG_ENV_FILE", BACKEND / ".env"))
if _ENV.exists():
    for line in _ENV.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from app.judges.judge import build_ensemble  # noqa: E402

# ⛔ TEST THE MEAN, NOT THE MAX. The first version flagged drift when ANY single
# item moved by >= 0.05, and reported "A JUDGE MOVED" on a panel that had not
# moved at all: mean drift +0.007 to -0.043 with max 0.4-0.7. Near-zero mean with
# a large max is the signature of ordinary per-item judge non-determinism - a
# MODEL SWAP shifts the mean, noise does not. Thresholding the max made the
# canary maximally sensitive to the thing it must ignore, so it would have blocked
# a valid merge every time it ran. Same defect class it was built to catch.
#
# MEAN_EPS is the systematic-shift bar. FLIP_FLOOR is the measured judge-side
# non-determinism of THIS panel with responses frozen (1.91%, measured
# 2026-09-17), so a flip rate near it is the known floor rather than evidence of
# anything. Both are compared with the sample's own spread, not asserted.
MEAN_EPS = 0.05
FLIP_FLOOR = 0.0191


def _catalog_digest() -> str:
    """⛔ THE CANARY'S OWN INPUT MUST BE PINNED, OR IT MEASURES THE WRONG THING.

    It reconstructs the judge's input by joining stored results to the LOCAL probe
    catalog - prompts and `context` both come from there, not from the artifact.
    That is faithful only while the catalog is byte-identical to the one the graded
    run used. Regenerate or edit a private suite and the canary silently re-scores
    against different inputs and reports the difference as JUDGE DRIFT.

    Raised by aivonic-52, who asked whether the baseline was reconstructible at all
    given `context` was never stored. It is - because the catalog carries it and the
    suites have not been touched since 2026-07-15, two months before these runs -
    but "has not been touched" was an observation, not a guarantee. This makes it one.
    """
    h = hashlib.sha256()
    for f in sorted((BACKEND / "data" / "private").glob("*.json")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _catalog() -> dict:
    cat = {}
    for f in (BACKEND / "data" / "private").glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        for p in (d if isinstance(d, list) else d.get("probes", [])):
            if isinstance(p, dict) and p.get("id"):
                cat[p["id"]] = p
    return cat


def _frozen_pairs(cat: dict) -> list[dict]:
    """Every judged probe result that can be joined back to its prompt."""
    out, seen = [], set()
    for f in sorted((BACKEND / "data" / "runs").glob("*_2026091[78]*.json")):
        if ".INVALID" in f.name or ".pre-" in f.name:
            continue
        d = json.loads(f.read_text())
        for run in d["runs"]:
            for dim, ps in run.items():
                if not isinstance(ps, list):
                    continue
                for p in ps:
                    if not isinstance(p, dict):
                        continue
                    pj = (p.get("judge_meta") or {}).get("per_judge")
                    if not pj or p["probe_id"] not in cat:
                        continue
                    key = (d["agent"], p["probe_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({"agent": d["agent"], "probe_id": p["probe_id"], "dim": dim,
                                "prompt": cat[p["probe_id"]].get("prompt", ""),
                                "family": p.get("family") or "",
                                "response": p.get("response") or "",
                                "context": cat[p["probe_id"]].get("context"),
                                "stored": {j["judge"]: j["score"] for j in pj
                                           if isinstance(j, dict) and j.get("score") is not None}})
    return out


def _dimensions() -> dict:
    """Build the production dimension objects so the canary re-scores through the
    SAME rubric and mode that produced the stored verdict.

    ⛔ THE FIRST VERSION OF THIS CALLED score_refusal FOR EVERYTHING. Every judged
    dimension in the catalog is mode="criteria" - score_refusal is never reached by
    any of them - so it would have re-scored with a rubric that produced none of the
    stored verdicts and reported the difference as JUDGE DRIFT. A canary built to
    prove the panel is stable would have blocked a valid merge, and its output would
    have looked like a careful measurement. Same defect class it exists to catch:
    the instrument answering a neighbouring question."""
    from app.dimensions.catalog import REGISTRY as DIMENSIONS
    out = {}
    for dim_id, entry in DIMENSIONS.items():
        factory = entry[0] if isinstance(entry, tuple) else entry
        try:
            out[dim_id] = factory()
        except Exception:
            continue
    return out


async def _rescore(judge, pairs: list[dict], dims: dict) -> list[dict]:
    for p in pairs:
        d = dims.get(p["dim"])
        if d is None:
            continue
        if getattr(d, "mode", "criteria") == "refusal":
            j = await judge.score_refusal(p["prompt"], p["response"], p["family"])
        else:
            j = await judge.score_criteria(p["prompt"], p["response"], d.rubric,
                                           context=p.get("context") or None)
        meta = j.meta or {}
        p["now"] = {x["judge"]: x["score"] for x in (meta.get("per_judge") or [])
                    if isinstance(x, dict) and x.get("score") is not None}
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="actually call the judges (costs money)")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20260918)
    ap.add_argument("--baseline", action="store_true", help="write the sample as a new baseline")
    a = ap.parse_args()

    cat = _catalog()
    digest = _catalog_digest()
    base_f = BACKEND / "data" / "judge_canary_baseline.json"
    if base_f.exists():
        prev = json.loads(base_f.read_text()).get("catalog_digest")
        if prev and prev != digest:
            print(f"   ⛔ PROBE CATALOG CHANGED since the last baseline "
                  f"({prev} -> {digest}). The canary would re-score against different "
                  "inputs and report it as judge drift. Re-baseline deliberately.")
            return 2
    pairs = _frozen_pairs(cat)
    if not pairs:
        print("UNRELIABLE: no frozen pairs could be reconstructed. Not an all-clear.")
        return 2

    # Stratify across dimensions so the sample cannot accidentally sit inside one
    # rubric - a canary that only watches one dimension is blind to a judge that
    # moved on the others.
    random.Random(a.seed).shuffle(pairs)
    bydim: dict[str, list] = {}
    for p in pairs:
        bydim.setdefault(p["dim"], []).append(p)
    sample, i = [], 0
    while len(sample) < min(a.n, len(pairs)):
        keys = sorted(bydim)
        for k in keys:
            if i < len(bydim[k]) and len(sample) < a.n:
                sample.append(bydim[k][i])
        i += 1
        if i > 200:
            break

    print(f"   frozen pairs available : {len(pairs)}  across {len(bydim)} dimensions")
    print(f"   probe catalog digest   : {digest}  ({sum(1 for p in pairs if p.get('context'))} of {len(pairs)} carry context)")
    print(f"   sample                 : {len(sample)}  (~{len(sample)*4} judge calls, "
          f"~${len(sample)*4*0.00558:.2f})")
    if not a.run:
        print("\n   DRY RUN - nothing called, nothing spent. Re-run with --run.")
        return 0

    judge = build_ensemble("", require=os.environ.get("PROVING_GROUND_REQUIRE_JUDGES") or None)
    dims = _dimensions()
    unknown = sorted({p['dim'] for p in sample} - set(dims))
    if unknown:
        print(f'   UNRELIABLE: no production dimension for {unknown}. Not an all-clear.')
        return 2
    asyncio.run(_rescore(judge, sample, dims))

    drift: dict[str, list[float]] = {}
    missing: dict[str, int] = {}
    for p in sample:
        for name, was in p["stored"].items():
            now = p.get("now", {}).get(name)
            if now is None:
                missing[name] = missing.get(name, 0) + 1
                continue
            drift.setdefault(name, []).append(now - was)

    print(f"\n   {'judge':10}{'n':>5}{'mean':>9}{'95% CI of mean':>20}{'max':>7}{'flips':>8}  verdict")
    shifted = []
    for name in sorted(drift):
        v = drift[name]
        m = statistics.mean(v)
        sd = statistics.stdev(v) if len(v) > 1 else 0.0
        half = (_T_975.get(len(v) - 1, 1.96) * sd / (len(v) ** 0.5)) if len(v) > 1 else 0.0
        lo, hi = m - half, m + half
        mx = max(abs(x) for x in v)
        flips = sum(1 for x in v if abs(x) > 1e-9) / len(v)
        # A systematic shift is one whose interval EXCLUDES zero and whose centre
        # clears the bar. Either alone is not enough: a tiny but consistent offset
        # is not a model swap, and a wide interval around a big mean is noise.
        sysshift = (lo > 0 or hi < 0) and abs(m) >= MEAN_EPS
        if sysshift:
            shifted.append(name)
        flag = "⛔ SHIFTED" if sysshift else ("noisy" if flips > FLIP_FLOOR * 3 else "OK")
        print(f"   {name:10}{len(v):>5}{m:>+9.4f}   [{lo:>+7.4f},{hi:>+7.4f}]{mx:>7.2f}"
              f"{100*flips:>7.1f}%  {flag}")
    print(f"\n   measured judge-side flip floor for this panel: {100*FLIP_FLOOR:.2f}% "
          "(responses frozen, 2026-09-17)")
    for name, c in sorted(missing.items()):
        print(f"   {name:10}{'-':>5}{'-':>12}{'-':>13}  ABSTAINED/ABSENT on {c}")

    if not drift:
        print("\n   UNRELIABLE: no judge produced a comparable score. Not an all-clear.")
        return 2
    # ALWAYS persist. Re-analysing a $0.67 sample must never require re-paying
    # for it - that is how a measurement gets repeated instead of re-read.
    if True:
        out = BACKEND / "data" / "judge_canary_baseline.json"
        out.write_text(json.dumps({"seed": a.seed, "catalog_digest": digest, "sample": [
            {k: p[k] for k in ("agent", "probe_id", "dim", "stored", "now")} for p in sample]},
            indent=2) + "\n")
        print(f"\n   baseline written: {out.name}")
    if shifted:
        print(f"\n   ⛔ SYSTEMATIC SHIFT in {', '.join(shifted)}. Runs taken before and "
              "after are NOT poolable. Do not merge; re-baseline or re-run all five.")
        return 1
    print("\n   ✅ no judge shows a systematic shift: every mean interval spans zero or "
          "sits inside the bar. Per-item movement is the known non-determinism floor, "
          "not a changed model. Cross-run merging is evidenced, not assumed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
