"""Generate the public certificate index from the leaderboard.

A certificate is NOT the scorecard. The scorecard is the private diagnostic you
hand the graded party: every probe that lost points, the judge's reasoning, the
agent's own replies. A certificate exists to be checked by a THIRD party - the
buyer, not the vendor - so it carries only what a buyer needs to trust a claim:
the grade, its interval, the date, the version tested, and whether the claim is
current.

⛔ THE CODE IS STABLE PER AGENT, AND THAT IS THE WHOLE POINT.

`report.py`'s scorecard slug seeds on graded_at + composite + run filename, so it
changes on every re-grade. Correct for a report (a link to a specific grade never
rots) and exactly wrong for a certificate: a vendor embeds a verification link
once, and it has to survive the next re-grade or the badge on their site 404s the
day their score improves. So the code seeds on the agent id alone.

No secrecy is needed or wanted here. The board is public, and a buyer being able
to look up a vendor is the feature, not a leak - which is the opposite of the
scorecard, where the unguessable token is load-bearing.

Status is deliberately NOT computed here. This file has no clock, like every
other generator in this engine, and a certificate written with a baked-in
"valid" would keep asserting it after it aged out. The serving layer decides
current-vs-expired at request time against graded_at.

    python -m app.leaderboard.certs --out ../frontend/certs.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.scoring.config import CRITICAL_CAP
from app.scoring.scorer import compute_composite
from app.leaderboard.store import load

VALIDITY_DAYS = 90          # matches the site; see methodology #13


def code_for(agent_id: str) -> str:
    """Stable for the life of the agent. Survives every re-grade."""
    return "pg-" + hashlib.sha256(f"cert|{agent_id}".encode()).hexdigest()[:8]


def build(entries: list[dict]) -> dict:
    certs = {}
    for e in entries:
        if e.get("composite") is None or not e.get("graded_at"):
            continue
        ci = e.get("ci95") or [e["composite"], e["composite"]]
        # ⛔ THE CAP MUST TRAVEL WITH THE GRADE. The board explains a capped
        # composite in full (render.py::_cap_line) because publishing 40 beside
        # subscores of 8-9 is a damaging claim by omission. A certificate is the
        # artefact a BUYER checks, so it cannot be less honest than the page a
        # visitor browses. Recomputed through the production scorer with the
        # critical count zeroed, never a second implementation of the weighting.
        cf = int(e.get("critical_failures") or 0)
        capped_from = None
        if cf > 0:
            unc, _, _ = compute_composite(e.get("subscores") or {}, 0)
            if unc > CRITICAL_CAP:
                capped_from = round(unc, 2)
        certs[code_for(e["id"])] = {
            "code": code_for(e["id"]),
            "agent": e["name"],
            "vendor": e.get("vendor", ""),
            "platform_version": e.get("platform_version") or "",
            "composite": e["composite"],
            "ci95": ci,
            "tier": e.get("tier", "none"),
            "runs": e.get("runs"),
            "judge_labs": e.get("judge_labs") or [],
            "critical_failures": cf,
            "capped": capped_from is not None,
            "cap": CRITICAL_CAP if capped_from is not None else None,
            "capped_from": capped_from,
            "graded_at": e["graded_at"],
            "validity_days": VALIDITY_DAYS,
            "ranked": bool(e.get("ranked", True)),
            "tools_verified": e.get("tools_verified") or [],
            "tools_declared": e.get("tools") or [],
        }
    return {"validity_days": VALIDITY_DAYS, "certificates": certs}


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the public certificate index.")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    data = build(load())
    Path(a.out).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(data['certificates'])} certificate(s) -> {a.out}")
    for c in sorted(data["certificates"].values(), key=lambda x: x["agent"]):
        print(f"  {c['code']}  {c['agent']:10} {c['composite']:<7} graded {c['graded_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
