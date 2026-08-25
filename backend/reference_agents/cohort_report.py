"""Print the reference cohort as one comparison: same agent, N platforms.

One agent specification (`northwind.py`: gpt-4o-mini and one system prompt) built
on every platform, so the framework is the only variable. This reads the promoted
board entries rather than the run artifacts, because the board is what is
published and a finding that disagrees with the published board is worse than no
finding at all.

    python -m reference_agents.cohort_report          # from backend/
    python reference_agents/cohort_report.py

WHY THE CONFIDENCE COLUMN IS NOT DECORATION. Each entry carries a 95% interval
from its 3 runs. A gap between two platforms smaller than their intervals is not
a difference, it is noise dressed as a result -- and a platform comparison is
exactly the kind of publication where a founder will (rightly) check. So every
spread is printed beside the widest interval in the cohort, and the ones that do
not clear it are marked rather than quietly reported as findings.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.leaderboard.store import load  # noqa: E402

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

    # The widest 95% interval in the cohort is the bar a spread has to clear
    # before it is worth calling a difference.
    widths = [
        (e["ci95"][1] - e["ci95"][0])
        for e in cohort
        if e.get("ci95") and e["ci95"][0] is not None and e["ci95"][1] is not None
    ]
    bar = max(widths) / 10.0 if widths else 0.0  # composite is /100, dimensions /10

    rows = []
    for key, label in DIMS:
        vals = [e["subscores"].get(key) for e in cohort]
        if any(v is None for v in vals):
            continue
        rows.append((key, label, vals, max(vals) - min(vals)))
    rows.sort(key=lambda r: -r[3])

    print(f"Per dimension, widest split first  (bar = {bar:.2f}, "
          "the widest 95% interval in the cohort)\n")
    print(f"{'':<26}" + "".join(f"{n:>{w}}" for n in names) + f"{'spread':>9}  ")
    for key, label, vals, spread in rows:
        best, worst = max(vals), min(vals)
        cells = ""
        for v in vals:
            mark = "*" if v == best else ("." if v == worst else " ")
            cells += f"{v:>{w-1}.2f}{mark}"
        flag = "" if spread > bar else "   (within noise)"
        print(f"{label:<26}{cells}{spread:>9.2f}{flag}")

    print("\n  *  best in cohort on that dimension        .  worst in cohort")
    print("  A spread at or under the bar is not a finding: the runs themselves "
          "move by more than that.\n")

    real = [r for r in rows if r[3] > bar]
    if real:
        print("Splits that clear the bar:")
        for key, label, vals, spread in real:
            hi = names[vals.index(max(vals))]
            lo = names[vals.index(min(vals))]
            print(f"  {label:<26} {max(vals):.2f} ({hi}) vs {min(vals):.2f} ({lo})   "
                  f"spread {spread:.2f}")
    else:
        print("No dimension splits by more than run-to-run variance.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
