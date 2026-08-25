"""Print the reference cohort as one comparison: same agent, N platforms.

One agent specification (`northwind.py`: gpt-4o-mini and one system prompt) built
on every platform, so the framework is the only variable. This reads the promoted
board entries rather than the run artifacts, because the board is what is
published and a finding that disagrees with the published board is worse than no
finding at all.

    python -m reference_agents.cohort_report          # from backend/
    python reference_agents/cohort_report.py

WHY THE RANGES ARE THE POINT. A gap between two platforms smaller than their own
run-to-run movement is not a difference, it is noise dressed as a result, and a
platform comparison is exactly the publication where a founder will (rightly)
check. So each dimension score is printed with the range across its 3 runs, taken
from the run artifacts, and a split counts only when the two ranges DO NOT
OVERLAP.

That test is deliberately plain rather than clever. With three runs there is no
honest t-test to run, and "these two ranges do not overlap across three runs" is
something the vendor can verify from the same artifacts instead of having to
trust a confidence interval we computed. It is also strict: it will refuse to
call splits that are probably real, which is the right direction to be wrong in
when the number is going in an email to the person who built the thing.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import json  # noqa: E402
import statistics  # noqa: E402

from app.leaderboard.store import load  # noqa: E402

RUNS_DIR = BACKEND / "data" / "runs"


def per_run_scores(entry: dict) -> dict[str, list[float]]:
    """Rebuild each dimension's score for each individual run, and VERIFY it.

    A dimension score is usually the mean of its probe scores times ten, so the
    3-run mean of that reconstruction should equal the promoted subscore. Usually
    is not always: ``latency_and_reliability`` folds in measured latency and does
    NOT reconstruct this way (10.00 against a published 9.07). Reconstructing
    three dimensions, finding them exact and generalising is how a wrong range
    gets printed under a right number -- so every dimension is checked against the
    published subscore here, and any that disagrees by more than rounding is
    dropped, leaving it with no range rather than a fabricated one.

    Returns {} when the artifact is missing, so an entry promoted before
    provenance was recorded degrades to "no range" instead of inventing one.
    """
    name = entry.get("run_artifact")
    if not name or not (RUNS_DIR / name).exists():
        return {}
    runs = json.loads((RUNS_DIR / name).read_text()).get("runs", [])
    out: dict[str, list[float]] = {}
    for one in runs:
        for dim, probes in one.items():
            scores = [p["score"] for p in probes
                      if isinstance(p, dict) and p.get("score") is not None]
            if scores:
                out.setdefault(dim, []).append(statistics.mean(scores) * 10.0)

    published = entry.get("subscores", {})
    verified = {}
    for dim, vals in out.items():
        pub = published.get(dim)
        # 0.02 admits the aggregate's own rounding and nothing else.
        if pub is not None and abs(statistics.mean(vals) - pub) <= 0.02:
            verified[dim] = vals
    return verified


DIMS = [
    ("grounding", "Grounding"),
    ("instruction_following", "Instruction following"),
    ("honesty_and_escalation", "Honesty & escalation"),
    ("conversational_quality", "Conversational quality"),
    ("task_success", "Task success"),
    ("memory", "Memory"),
    ("robustness", "Robustness"),
    ("safety_and_harm", "Safety & harm"),
    ("bias_and_fairness", "Bias & fairness"),
    ("privacy_and_data_handling", "Privacy & data handling"),
    ("security", "Security"),
    ("latency_and_reliability", "Latency & reliability"),
]


def short(entry: dict) -> str:
    """'Northwind (Dify)' -> 'Dify'."""
    name = entry["name"]
    return name[name.index("(") + 1:name.rindex(")")] if "(" in name else name


def main() -> int:
    cohort = [e for e in load() if e.get("reference")]
    if not cohort:
        raise SystemExit("no reference entries on the board")
    cohort.sort(key=lambda e: -e["composite"])
    names = [short(e) for e in cohort]
    w = max(12, max(len(n) for n in names) + 1)

    print(f"\nNORTHWIND REFERENCE COHORT — {len(cohort)} platforms, one agent specification")
    print("gpt-4o-mini, one system prompt, held identical. The framework is the only variable.\n")

    print(f"{'':<26}" + "".join(f"{n:>{w}}" for n in names) + f"{'spread':>9}")
    print(f"{'Composite':<26}" + "".join(f"{e['composite']:>{w}.2f}" for e in cohort)
          + f"{max(e['composite'] for e in cohort) - min(e['composite'] for e in cohort):>9.2f}")
    print(f"{'Tier':<26}" + "".join(f"{e.get('tier','-'):>{w}}" for e in cohort))
    print(f"{'Median latency (ms)':<26}"
          + "".join(f"{e.get('latency_ms', float('nan')):>{w}.0f}" for e in cohort))
    print()
    # Version on its own lines rather than squeezed into a column: a truncated
    # build string ("Flowise 1.8") names a version that does not exist, and the
    # whole point of recording it is that 1.8.2 and 3.x are not the same product.
    print("Platform builds")
    for e, n in zip(cohort, names):
        print(f"  {n:<{w}} {e.get('platform_version') or 'NOT RECORDED'}")
    print()

    ranges = [per_run_scores(e) for e in cohort]
    missing = [n for n, r in zip(names, ranges) if not r]
    if missing:
        print(f"NOTE: no run artifact recorded for {', '.join(missing)}; "
              "their scores are printed without a range and are excluded from the "
              "overlap test rather than being compared as if they had one.\n")

    rows = []
    for key, label in DIMS:
        vals = [e["subscores"].get(key) for e in cohort]
        if any(v is None for v in vals):
            continue
        lohi = [(min(r[key]), max(r[key])) if r.get(key) else None for r in ranges]
        rows.append((key, label, vals, max(vals) - min(vals), lohi))
    rows.sort(key=lambda r: -r[3])

    print("Per dimension, widest split first. Each cell is the 3-run mean, with the\n"
          "run-to-run range beneath it. A split counts only when two ranges do not overlap.\n")
    for key, label, vals, spread, lohi in rows:
        best, worst = max(vals), min(vals)
        cells = ""
        for v in vals:
            mark = "*" if v == best else ("." if v == worst else " ")
            cells += f"{v:>{w-1}.2f}{mark}"
        print(f"{label:<26}{cells}{spread:>9.2f}")
        rng = "".join(
            (f"{lo:.2f}-{hi:.2f}".rjust(w) if r else "-".rjust(w))
            for r, (lo, hi) in ((x, x or (0, 0)) for x in lohi)
        )
        print(f"{'':<26}{rng}")
    print("\n  *  best in cohort on that dimension        .  worst in cohort\n")

    real = []
    for key, label, vals, spread, lohi in rows:
        hi_i, lo_i = vals.index(max(vals)), vals.index(min(vals))
        a, b = lohi[hi_i], lohi[lo_i]
        if a and b and a[0] > b[1]:          # best's worst run beats worst's best run
            real.append((label, vals[hi_i], names[hi_i], a, vals[lo_i], names[lo_i], b))

    if real:
        print("Splits where the ranges do not overlap:")
        for label, hv, hn, hr, lv, ln, lr in real:
            print(f"  {label:<26} {hv:.2f} ({hn}, worst run {hr[0]:.2f}) "
                  f"beats {lv:.2f} ({ln}, best run {lr[1]:.2f})")
    else:
        print("No dimension separates the platforms beyond their own run-to-run movement.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
