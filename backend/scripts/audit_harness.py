#!/usr/bin/env python3
"""Run the pre-grade harness check across every reference adapter at once.

Before a board-wide re-grade, the question is not "is each agent good" but "is
each HARNESS sound". Those are different questions and only the second can be
answered without spending money: two agent calls per adapter, zero judge calls.

    OPENAI_API_KEY=... python scripts/audit_harness.py

Exit 0 only if every reachable adapter passes. An UNREACHABLE adapter is reported
and does NOT pass silently - a stack that is down cannot be graded either, and
"skipped" quietly becomes "assumed fine" by the next reader.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# ⛔ LOAD THE SAME ENV THE GRADE SCRIPTS LOAD, OR THIS GATE LIES. Without it the
# adapter tokens are unset, every authenticated adapter sends an empty bearer, and
# the audit reports a 401 that says nothing about the agent. It reported exactly
# that for dify on 2026-09-18 and I spent the next twenty minutes investigating a
# healthy stack. A pre-flight check that fails for a reason internal to itself is
# worse than none: it trains you to discount the one time it is right.
_ENV = Path(os.environ.get("PG_ENV_FILE", BACKEND / ".env"))
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
else:
    print(f"WARNING: no env file at {_ENV} - adapter tokens will be unset and "
          "authenticated adapters will fail for that reason, not for theirs.")

from app.adapters import RestApiAdapter
from app.adapters.config import RestAdapterConfig
from app.pregrade import check_adapter

REF = Path(__file__).resolve().parents[1] / "reference_agents"


async def main() -> int:
    results = {}
    for f in sorted(REF.glob("adapter_*.json")):
        name = f.stem.replace("adapter_", "")
        try:
            cfg = RestAdapterConfig(**json.loads(f.read_text()))
        except Exception as e:
            results[name] = (False, [f"  FAIL  config invalid: {e}"])
            continue
        server_session = cfg.history.mode == "server_session"
        try:
            r = await check_adapter(lambda c=cfg: RestApiAdapter(c),
                                    server_session=server_session)
            results[name] = (r.ok, r.lines)
        except Exception as e:
            results[name] = (False, [f"  FAIL  {type(e).__name__}: {str(e)[:90]}"])

    print(f"Harness audit, {len(results)} adapter(s). No judge calls.\n")
    bad = []
    for name, (ok, lines) in results.items():
        print(f"{name}")
        for l in lines:
            print(l)
        if not ok:
            bad.append(name)
        print()
    if bad:
        print(f"NOT SAFE TO GRADE: {', '.join(bad)}")
        return 1
    print("All adapters pass. Safe to grade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
