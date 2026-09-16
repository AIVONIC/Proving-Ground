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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
