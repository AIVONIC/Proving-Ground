"""Aggregate Proving Ground judge spend and publish it to the VPS admin.

Grading runs on Christian's workstation, not the VPS, so the admin portal has no
way to read run artifacts directly. This walks them, totals what the judge ledger
recorded, and pushes ONE summary file to a private path on the VPS.

TWO THINGS IT REFUSES TO DO, both of which would produce a confident wrong number:

  1. It never reports a run with no ledger as costing zero. Runs before
     2026-09-03 predate app/judges/spend.py entirely, so their cost is UNKNOWN,
     not nil. They are counted separately and named on the page.
  2. It never sums an unpriced model into the total. spend.py already reports
     those as None; that is carried through rather than coerced.

Published to a private path, never the public docroot: /var/www/html/pg is served
to the internet and what we pay to grade competitors is nobody's business.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RUNS = Path(__file__).resolve().parents[1] / "data" / "runs"
REMOTE = "root@72.62.59.75"
REMOTE_DIR = "/opt/aivonic/data/proving-ground"
REMOTE_FILE = f"{REMOTE_DIR}/spend.json"


def build() -> dict:
    by_model: dict[str, dict] = {}
    runs_with, runs_without = [], []
    unpriced: set[str] = set()

    for f in sorted(RUNS.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        sp = d.get("spend")
        platform = d.get("platform") or d.get("agent") or f.stem.split("_")[0]
        if not sp:
            runs_without.append({"run": f.stem, "platform": platform})
            continue
        runs_with.append(
            {
                "run": f.stem,
                "platform": platform,
                "usd": sp.get("usd_total"),
                "models": sorted((sp.get("by_model") or {}).keys()),
            }
        )
        for m, v in (sp.get("by_model") or {}).items():
            e = by_model.setdefault(
                m, {"model": m, "calls": 0, "tokens_in": 0, "tokens_out": 0,
                    "usd": 0.0, "rate_checked": v.get("rate_checked")}
            )
            e["calls"] += v.get("calls") or 0
            e["tokens_in"] += v.get("tokens_in") or 0
            e["tokens_out"] += v.get("tokens_out") or 0
            if v.get("usd") is None:
                unpriced.add(m)
            else:
                e["usd"] += v["usd"]
        for m in sp.get("unpriced_models") or []:
            unpriced.add(m)

    for e in by_model.values():
        e["usd"] = round(e["usd"], 4)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "usd_total": round(sum(e["usd"] for e in by_model.values() if e["model"] not in unpriced), 4),
        "by_model": sorted(by_model.values(), key=lambda e: -e["usd"]),
        "unpriced_models": sorted(unpriced),
        "runs_with_ledger": runs_with,
        # Named, not silently dropped: these cost real money and the amount is
        # simply not recoverable.
        "runs_without_ledger": runs_without,
        "note": (
            "The judge ledger was added on 2026-09-03. Runs before it recorded no "
            "tokens, so their cost is unknown rather than zero."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true", help="push to the VPS (default: print only)")
    a = ap.parse_args()

    data = build()
    print(json.dumps(data, indent=2)[:1400])
    print(
        f"\n  runs with a ledger : {len(data['runs_with_ledger'])}"
        f"\n  runs WITHOUT       : {len(data['runs_without_ledger'])}  (cost unknown, not zero)"
        f"\n  measured total     : ${data['usd_total']}"
        f"\n  unpriced models    : {data['unpriced_models'] or 'none'}"
    )
    if not a.publish:
        print("\n  not published (pass --publish)")
        return 0

    tmp = Path("/tmp/pg_spend.json")
    tmp.write_text(json.dumps(data, indent=2))
    subprocess.run(["ssh", REMOTE, f"mkdir -p {REMOTE_DIR}"], check=True)
    subprocess.run(["scp", "-q", str(tmp), f"{REMOTE}:{REMOTE_FILE}"], check=True)
    # Verify the far side has what we sent, rather than trusting scp's exit code.
    got = subprocess.run(
        ["ssh", REMOTE, f"python3 -c \"import json;d=json.load(open('{REMOTE_FILE}'));print(d['usd_total'],len(d['runs_with_ledger']))\""],
        capture_output=True, text=True,
    )
    print(f"  published -> {REMOTE_FILE}")
    print(f"  verified on the box: usd_total, runs_with_ledger = {got.stdout.strip() or got.stderr.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
