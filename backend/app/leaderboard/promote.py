"""Promote a grade run artifact into a leaderboard entry.

    python -m app.leaderboard.promote --run data/runs/spark_XX.json \
        --id spark --name SPARK --vendor "Aivonic Labs AB" \
        --category "Sales & Support" --access "Socket.IO" \
        --graded-at 2026-07-14 --self-operated

Only a full 12-dimension run (a real tier assigned, not "incomplete") is eligible;
a partial run is rejected so the board never shows a tier computed on a subset.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.dimensions.catalog import REGISTRY
from app.leaderboard.store import upsert


def _median_latency_ms(run: dict) -> float | None:
    """Median per-probe latency across every probe of every run. Reported on the
    board alongside quality (the scatter axis), never folded into the composite."""
    lats = [
        pr["latency_ms"]
        for one_run in run.get("runs", [])
        for probes in one_run.values()
        for pr in probes
        if isinstance(pr, dict) and pr.get("latency_ms")
    ]
    if not lats:
        return None
    lats.sort()
    n = len(lats)
    return round((lats[n // 2] if n % 2 else (lats[n // 2 - 1] + lats[n // 2]) / 2), 1)


def entry_from_run(run: dict, meta: dict) -> dict:
    g = run["grade"]
    subs = g.get("subscores", {})
    if len(subs) < len(REGISTRY) or g.get("incomplete"):
        raise SystemExit(
            f"refusing to promote {meta['id']}: run graded {len(subs)}/{len(REGISTRY)} "
            "dimensions. A public entry needs a full grade."
        )
    conf = g.get("confidence", {})
    lat = _median_latency_ms(run)
    return {
        "id": meta["id"],
        "name": meta["name"],
        "vendor": meta["vendor"],
        "category": meta.get("category", ""),
        "access": meta.get("access", ""),
        "composite": round(float(g["composite"]), 2),
        "tier": g["tier"],
        "subscores": {k: round(float(v), 2) for k, v in subs.items()},
        "critical_failures": int(g.get("critical_failures", 0)),
        "runs": int(conf.get("runs", 1)),
        "ci95": [conf.get("ci95_low"), conf.get("ci95_high")],
        "graded_at": meta["graded_at"],
        "self_operated": bool(meta.get("self_operated", False)),
        "reference": bool(meta.get("reference", False)),
        "tools": list(meta.get("tools") or []),
        "tools_verified": list(meta.get("tools_verified") or []),
        **({"latency_ms": lat} if lat is not None else {}),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote a grade run to the leaderboard.")
    ap.add_argument("--run", required=True, help="path to a grade run artifact JSON")
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--category", default="")
    ap.add_argument("--access", default="")
    ap.add_argument("--graded-at", required=True, help="YYYY-MM-DD (passed in; the engine has no clock)")
    ap.add_argument("--tools", default="",
                    help="comma-separated executing tools the agent actually invokes (e.g. "
                         "'Email,Web search,Booking,Checkout'); empty means conversation-only")
    ap.add_argument("--tools-verified", default="",
                    help="comma-separated tools whose execution was verified in a sandbox "
                         "(subset of --tools); shown as 'N of M verified' on the board")
    ap.add_argument("--self-operated", action="store_true",
                    help="mark an agent the operator runs itself (shown transparently)")
    ap.add_argument("--reference", action="store_true",
                    help="mark an operator-built reference agent (a build on a third-party "
                         "platform, not that vendor's official product), shown transparently")
    a = ap.parse_args()

    run = json.loads(Path(a.run).read_text())
    meta = {
        "id": a.id, "name": a.name, "vendor": a.vendor, "category": a.category,
        "access": a.access, "graded_at": a.graded_at, "self_operated": a.self_operated,
        "reference": a.reference,
        "tools": [t.strip() for t in a.tools.split(",") if t.strip()],
        "tools_verified": [t.strip() for t in a.tools_verified.split(",") if t.strip()],
    }
    board = upsert(entry_from_run(run, meta))
    g = run["grade"]
    print(f"promoted {a.id}: {g['composite']} {g['tier']} -> {len(board)} on the board")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
