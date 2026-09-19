#!/usr/bin/env python3
"""Re-judge stored responses under the CORRECTED capability profile.

The profile is judge-side context only - the agents never saw it - so re-judging
stored replies holds agent behaviour fixed by construction and isolates the single
variable that was wrong. A full re-grade would move two things at once and lose the
ability to attribute the delta.

    python3 scripts/rejudge_profile.py --agent crewai-northwind            # dry run
    python3 scripts/rejudge_profile.py --agent crewai-northwind --run --sample 100
    python3 scripts/rejudge_profile.py --agent crewai-northwind --run --apply
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
from app.grade import _load_profile  # noqa: E402
from app.judges.judge import build_ensemble  # noqa: E402
from app.scoring.scorer import GradeResult, aggregate_runs, compute_composite  # noqa: E402

_T = {4: 2.776, 29: 2.045, 49: 2.010, 99: 1.984, 199: 1.972}


def _grade(runs_scores) -> GradeResult:
    out = []
    for subs_probe in runs_scores:
        subs, crit = {}, 0
        for dim, vals in subs_probe.items():
            if vals:
                subs[dim] = round(statistics.mean(v for v, _ in vals) * 10, 2)
                crit += sum(1 for _, c in vals if c)
        c, inc, cap = compute_composite(subs, crit)
        out.append(GradeResult(composite=c, tier="", critical_failures=crit, subscores=subs,
                               incomplete=inc, capped=cap, graded_dimensions=sorted(subs),
                               confidence={}))
    return aggregate_runs(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--sample", type=int, default=0, help="judge only N probes (a test, not a grade)")
    ap.add_argument("--apply", action="store_true", help="write the re-judged artifact")
    ap.add_argument("--seed", type=int, default=20260919)
    # ⛔ THE CONTROL. Re-judging under the NEW profile measures
    # (profile change + whatever the judges are doing today) together. Running the
    # SAME sample and seed under the OLD profile isolates the second, and the
    # difference between the two runs is the profile effect alone.
    ap.add_argument("--profile-override", metavar="FILE",
                    help="judge with this profile text instead of the live one")
    a = ap.parse_args()

    art = BACKEND / "data" / "runs" / "merged" / f"{a.agent}_n5.json"
    d = json.loads(art.read_text())
    cat = {}
    for f in (BACKEND / "data" / "private").glob("*.json"):
        try:
            j = json.loads(f.read_text())
        except Exception:
            continue
        for p in (j if isinstance(j, list) else j.get("probes", [])):
            if isinstance(p, dict) and p.get("id"):
                cat[p["id"]] = p

    items = []
    for ri, run in enumerate(d["runs"]):
        for dim, ps in run.items():
            if not isinstance(ps, list):
                continue
            for pi, p in enumerate(ps):
                if not isinstance(p, dict) or not (p.get("judge_meta") or {}).get("per_judge"):
                    continue
                if p["probe_id"] not in cat:
                    continue
                items.append({"ri": ri, "dim": dim, "pi": pi, "id": p["probe_id"],
                              "prompt": cat[p["probe_id"]].get("prompt", ""),
                              "context": cat[p["probe_id"]].get("context"),
                              "response": p.get("response") or "", "was": p["score"]})
    todo = items
    if a.sample:
        random.Random(a.seed).shuffle(items)
        bydim = defaultdict(list)
        for it in items:
            bydim[it["dim"]].append(it)
        todo, i = [], 0
        while len(todo) < min(a.sample, len(items)) and i < 400:
            for k in sorted(bydim):
                if i < len(bydim[k]) and len(todo) < a.sample:
                    todo.append(bydim[k][i])
            i += 1

    print(f"   {a.agent}: {len(items)} judged probes, judging {len(todo)}"
          f"  (~{len(todo)*4} calls, ~${len(todo)*4*0.00558:.2f})")
    if not a.run:
        print("   DRY RUN - nothing called, nothing spent.")
        return 0

    if a.profile_override:
        profile = Path(a.profile_override).read_text().strip()
        print(f'   CONTROL: judging with an overridden profile ({len(profile)} chars)')
    else:
        profile = _load_profile(a.agent, None)   # provenance-checked on the way in
    judge = build_ensemble(profile)
    dims = {k: (v[0] if isinstance(v, tuple) else v)() for k, v in REGISTRY.items()}

    async def go():
        for it in todo:
            dim = dims.get(it["dim"])
            if dim is None or not getattr(dim, "rubric", ""):
                continue
            j = await judge.score_criteria(it["prompt"], it["response"], dim.rubric,
                                           context=it["context"] or None)
            it["now"] = j.score
            it["meta"] = j.meta
    asyncio.run(go())

    scored = [it for it in todo if "now" in it]

    # ⛔ ALWAYS PERSIST PER-ITEM RESULTS. THIS TOOL PRINTED AGGREGATES AND THREW THE
    # ITEMS AWAY, AND IT COST A CONCLUSION.
    #
    # On 2026-09-19 two arms came back at -0.0208 and -0.0207 and were read as "one
    # common-mode shift, no differential". The statistic the prediction was actually
    # about was crewai MINUS typebot, PAIRED by probe - same items, same seed, so the
    # pairing cancels probe difficulty and the interval on the difference is far
    # narrower than the interval on either level. That statistic could not be computed,
    # because only the means had been kept. Recovering it meant re-judging both arms
    # for another $9, so it was not recovered and the finding had to be stated weaker
    # than the data could have supported.
    #
    # Same defect as a verdict stored without its rubric inputs, third instance in two
    # days: a measurement discarded the moment after it is taken. What you can
    # re-derive you never have to re-buy. Raised by aivonic-52.
    out = BACKEND / "data" / "rejudge"
    out.mkdir(parents=True, exist_ok=True)
    tag = "control" if a.profile_override else "treatment"
    dst = out / f"{a.agent}_{tag}_seed{a.seed}_n{len(scored)}.json"
    dst.write_text(json.dumps({
        "agent": a.agent, "arm": tag, "seed": a.seed,
        "profile_override": str(a.profile_override) if a.profile_override else None,
        # per item, keyed so two arms of the same seed JOIN on (run, probe_id)
        "items": [{"run": it["ri"], "probe_id": it["id"], "dim": it["dim"],
                   "was": it["was"], "now": it["now"]} for it in scored],
    }, indent=2) + "\n")
    print(f"   per-item results: {dst.relative_to(BACKEND)}  ({len(scored)} items)")

    diffs = [it["now"] - it["was"] for it in scored]
    n = len(diffs)
    m = statistics.mean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    half = _T.get(n - 1, 1.96) * sd / n ** 0.5 if n > 1 else 0.0
    print(f"   mean probe-score change {m:+.4f}  95% CI [{m-half:+.4f}, {m+half:+.4f}]  (n={n})")

    bydim = defaultdict(list)
    for it in scored:
        bydim[it["dim"]].append(it["now"] - it["was"])
    print(f"\n   {'dimension':28}{'mean move':>11}{'n':>5}")
    for dim in sorted(bydim, key=lambda k: statistics.mean(bydim[k])):
        v = bydim[dim]
        print(f"   {dim:28}{statistics.mean(v):>+11.4f}{len(v):>5}")

    if a.sample:
        print(f"\n   SAMPLE ONLY - this is a test of the mechanism, not a grade.")
        return 0
    if a.apply:
        for it in scored:
            p = d["runs"][it["ri"]][it["dim"]][it["pi"]]
            p["score"] = it["now"]
            if it.get("meta"):
                p["judge_meta"] = it["meta"]
                p["judge_agreement"] = (it["meta"] or {}).get("agreement")
        runs_scores = []
        for run in d["runs"]:
            acc = defaultdict(list)
            for dim, ps in run.items():
                if not isinstance(ps, list):
                    continue
                for p in ps:
                    if isinstance(p, dict) and p.get("score") is not None:
                        acc[dim].append((p["score"], bool(p.get("critical"))))
            runs_scores.append(acc)
        g = _grade(runs_scores)
        before = d["grade"]["composite"]
        d["grade"].update({"composite": g.composite, "subscores": g.subscores,
                           "confidence": g.confidence, "critical_failures": g.critical_failures,
                           "capped": g.capped, "incomplete": g.incomplete})
        d.setdefault("rejudged", {})["profile_correction"] = {
            "date": "2026-09-19", "composite_before": before, "composite_after": g.composite,
            "why": "profile contradicted the system prompt on scope; responses unchanged"}
        art.write_text(json.dumps(d, indent=2) + "\n")
        c = g.confidence
        print(f"\n   APPLIED: composite {before:.2f} -> {g.composite:.2f} "
              f"[{c['ci95_low']:.2f}, {c['ci95_high']:.2f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
