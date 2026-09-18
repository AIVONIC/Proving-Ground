#!/usr/bin/env python3
"""Merge additional runs into an existing grade and re-aggregate in ONE step.

n=3 cannot discriminate this board: nine of fifteen agent pairs overlap. n=5
leaves three overlapping, and those three are a genuine tie. n=10 was measured
to buy ZERO further discrimination over n=5 - the same three pairs - so five is
where it stops.

⛔ WHY MERGE RATHER THAN RE-RUN ALL FIVE. Per-probe scores for the existing three
runs are already stored and a composite is deterministic from them. Re-running
five costs $53 instead of $21 and, worse, moves every number for TWO reasons at
once - more runs AND fresh judge non-determinism - so no change could be
attributed to the sample size. Merging isolates the variable.

⛔ WHY THIS RE-AGGREGATES RATHER THAN LEAVING IT TO A SECOND COMMAND. The first
version of this script copied the base artifact's `grade` block onto the merged
file and told the operator to re-aggregate afterwards. That leaves a file on disk
holding FIVE runs beside a grade block computed from THREE, with nothing marking
it stale - a wrong answer that reads as a valid artifact. Anything that read it
in that window (the promoter, the renderer, a human) would get a 3-run grade
labelled n=5. The window is now closed by construction.

⛔ CRITICALS ARE RECONSTRUCTED PER RUN, NOT ASSUMED ZERO. `rederive_ci.py`'s
`_per_run` hardcodes `critical_failures=0`, which is correct there because that
script only re-derives intervals and criticals are carried separately. It would
be WRONG here: `aggregate_runs` takes `max(critical_failures)` across runs, and a
critical caps the composite at CRITICAL_CAP. Every agent in the current board has
zero criticals, so assuming zero would produce identical output today and silently
wrong output the first time a graded agent fails a safety probe. That is the exact
shape of the Onyx threshold edge - immaterial until it decides an outcome.

    python3 scripts/extend_runs.py --base <3-run>.json --extra <2-run>.json --out <5-run>.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.scoring.scorer import (GradeResult, aggregate_runs,  # noqa: E402
                                assign_tier, compute_composite)


def _run_composite(run: dict) -> float:
    """One run's composite, from its stored per-probe scores."""
    subs, crit = {}, 0
    for dim, probes in run.items():
        if not isinstance(probes, list) or not probes:
            continue
        vals = [p["score"] for p in probes
                if isinstance(p, dict) and p.get("score") is not None]
        crit += sum(1 for p in probes if isinstance(p, dict) and p.get("critical"))
        if vals:
            subs[dim] = round(statistics.mean(vals) * 10, 2)
    return compute_composite(subs, crit)[0]


def _per_run(d: dict) -> list[GradeResult]:
    """Rebuild each run's GradeResult from stored per-probe scores AND criticals."""
    out = []
    for run in d["runs"]:
        subs, crit = {}, 0
        for dim, probes in run.items():
            if not isinstance(probes, list) or not probes:
                continue
            vals = [p["score"] for p in probes
                    if isinstance(p, dict) and p.get("score") is not None]
            crit += sum(1 for p in probes if isinstance(p, dict) and p.get("critical"))
            if vals:
                subs[dim] = round(statistics.mean(vals) * 10, 2)
        c, inc, cap = compute_composite(subs, crit)
        out.append(GradeResult(composite=c, tier=assign_tier(c, subs, crit, inc),
                               critical_failures=crit, subscores=subs, incomplete=inc,
                               capped=cap, graded_dimensions=sorted(subs), confidence={}))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="existing artifact to extend")
    ap.add_argument("--extra", required=True, help="artifact holding the additional runs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-scoring-config-change", metavar="REASON",
                    help="pool across differing composite_ids, recording REASON "
                         "in the merged artifact. For a coverage-only widening "
                         "where no configured value moved.")
    a = ap.parse_args()

    base = json.loads(Path(a.base).read_text())
    extra = json.loads(Path(a.extra).read_text())

    # Refuse rather than merge unlike things. A silent cross-agent merge would
    # produce a plausible composite for an agent that was never graded that way.
    if base["agent"] != extra["agent"]:
        sys.exit(f"REFUSING: different agents ({base['agent']} vs {extra['agent']})")
    # ⛔ THE FINGERPRINT GUARD, AND WHY IT HAS AN OVERRIDE RATHER THAN AN EXCEPTION.
    #
    # Two runs scored under different rules are not poolable, so a differing
    # composite_id refuses. That is correct and it fired for real: the n=5 board
    # straddles the 2026-09-18 fingerprint change, every agent carrying b4e796bd on
    # its 3-run file and 9c569e75 on its 2-run file, and this guard would have
    # refused all five AFTER the grading money was spent.
    #
    # It was a false positive IN SUBSTANCE. Both configs were reconstructed from git
    # and diffed by VALUE rather than by hash: CRITICAL_CAP, LATENCY_W, STABILITY_W,
    # RELIABILITY_DIM, DIMENSION_WEIGHTS and TIERS all identical. The fingerprint's
    # COVERAGE widened; no configured value moved. The ids differ because the input
    # SET grew, not because the scoring did.
    #
    # Two repairs were rejected, and the second is the tempting one:
    #   - weakening the gate. A mechanism that says "not comparable" beside a tool
    #     that ignores it is worse than having neither.
    #   - re-stamping the older artifacts with the new id. That asserts they were
    #     computed under a configuration that did not exist when they ran - falsifying
    #     provenance to pass a check.
    #
    # So: an EXPLICIT, RECORDED override. The merged artifact carries BOTH ids and the
    # reason, so the straddle is published with the number instead of being invisible
    # in it. A genuine value change still refuses, and no reason string makes it pass
    # quietly, because the reason travels with the grade.
    #
    # This exists because the artifacts store the fingerprint HASH and not the VALUES
    # it was computed from, so poolability is undecidable from the artifacts alone -
    # the same defect as a verdict stored without its rubric inputs. grade.py now
    # records the values (aivonic-8f), which makes the NEXT straddle decidable
    # mechanically. It cannot help here: these artifacts predate it.
    bid = base["grade"].get("composite_id")
    eid = extra["grade"].get("composite_id")
    if bid != eid and not a.allow_scoring_config_change:
        sys.exit(f"REFUSING: different scoring config ({bid} vs {eid}). Runs scored "
                 "under different rules are not poolable.\n\n"
                 "If the ids differ only because the fingerprint's COVERAGE widened - "
                 "verify by diffing the configs' VALUES, not their hashes - re-run with\n"
                 '  --allow-scoring-config-change "<why you established they are poolable>"\n'
                 "The reason is written into the merged artifact and published with it.")

    # ⛔ POOLABILITY GATE. Merging assumes the later runs were drawn under the same
    # conditions as the earlier ones. A judge that moved in between makes them
    # incommensurable, and the merged interval then comes out NARROWER AND WRONG -
    # worse than the too-wide intervals already fixed, because a tighter interval
    # reads as a better measurement.
    #
    # The judge canary bounds gross judge replacement but cannot resolve drift finer
    # than the board's own margins. This test is sharper AND free: it asks whether the
    # NEW runs sit inside the spread of the OLD ones. Same agent, same probes, so a
    # new run landing outside the old runs' range is evidence that something other
    # than the agent moved. Raised by aivonic-52.
    import statistics as _st
    b_c = [_run_composite(r) for r in base["runs"]]
    e_c = [_run_composite(r) for r in extra["runs"]]
    lo, hi = min(b_c), max(b_c)
    spread = hi - lo
    # Allow the new runs to sit within the old range widened by its own spread; with
    # n=3 the observed range understates the true one, so a bare min/max test would
    # reject valid runs. Widening by 1x the range is deliberately permissive - this
    # gate exists to catch a REGIME change, not ordinary variation.
    out_of_band = [c for c in e_c if not (lo - spread <= c <= hi + spread)]
    print(f"   poolability: old runs {[round(x,2) for x in b_c]} "
          f"(range {lo:.2f}-{hi:.2f}), new {[round(x,2) for x in e_c]}")
    if out_of_band:
        sys.exit(f"REFUSING: new run composite(s) {[round(x,2) for x in out_of_band]} fall "
                 f"outside the old runs' range widened by its own spread "
                 f"[{lo-spread:.2f}, {hi+spread:.2f}]. The later runs are not evidently "
                 "drawn from the same conditions - do not pool. Re-run all five instead.")

    merged = dict(base)
    merged["runs"] = base["runs"] + extra["runs"]

    runs = _per_run(merged)
    new = aggregate_runs(runs)
    old_c = base["grade"]["composite"]

    merged["grade"] = {**base["grade"], "composite": new.composite, "tier": new.tier,
                       "critical_failures": new.critical_failures,
                       "subscores": new.subscores, "incomplete": new.incomplete,
                       "capped": new.capped, "graded_dimensions": new.graded_dimensions,
                       "confidence": new.confidence}
    merged["spend"] = {
        "usd_total": round(base["spend"]["usd_total"] + extra["spend"]["usd_total"], 4),
        "by_model": base["spend"].get("by_model"),
        "merged_from": [Path(a.base).name, Path(a.extra).name],
    }
    # Provenance: a merged grade is not a single sitting, and silence here would
    # make it indistinguishable from one taken all at once.
    merged["merged"] = {"base_runs": len(base["runs"]), "added_runs": len(extra["runs"]),
                        "base_artifact": Path(a.base).name,
                        "extra_artifact": Path(a.extra).name}
    if bid != eid:
        # Published WITH the grade: a reader auditing poolability gets the
        # real answer rather than silence.
        merged["merged"]["scoring_config_straddle"] = {
            "base_composite_id": bid, "extra_composite_id": eid,
            "reason": a.allow_scoring_config_change}

    Path(a.out).write_text(json.dumps(merged, indent=2) + "\n")
    c = new.confidence
    lo, hi = c.get("ci95_low"), c.get("ci95_high")
    span = f"[{lo:.2f}, {hi:.2f}]" if lo is not None else "unavailable"
    print(f"   {base['agent']}: {len(base['runs'])}+{len(extra['runs'])}="
          f"{len(merged['runs'])} runs | composite {old_c:.2f} -> {new.composite:.2f} "
          f"| CI95 {span} | criticals {new.critical_failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
