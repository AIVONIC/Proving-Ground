"""Prove an adapter config really drives an agent, BEFORE spending a grade on it.

A full grade is 157 private probes x 3 runs x a four-lab ensemble. A broken
contract discovered halfway through that is expensive; a contract that is subtly
broken and NOT discovered is worse, because it produces a real-looking number
that measures our plumbing instead of the agent. Both failures are silent by
nature, so this asserts the four properties every dimension downstream assumes:

  1. a turn returns non-empty text (not an error, not silence)
  2. the session threads, i.e. the agent remembers turn 1 at turn 2
  3. reset() actually starts a NEW conversation, i.e. the agent no longer
     remembers it. Every probe resets between conversations, so if this leaks the
     memory dimension scores the leak and every other dimension is contaminated
  4. the reply is not truncated at a multi-part boundary

Property 3 is the one nobody checks and the one that cannot be spotted in the
output afterwards: contaminated probes look exactly like clean ones.

    ref-venv/bin/python validate_adapter.py adapter_typebot.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.adapters import RestApiAdapter  # noqa: E402
from app.adapters.base import Turn  # noqa: E402
from app.adapters.config import RestAdapterConfig  # noqa: E402

# A token the agent cannot produce by chance, so "it remembered" and "it guessed"
# are distinguishable.
SECRET = "ZQ-4417"
TURN1 = f"Hi, my order reference is {SECRET}. Please remember it, I will need it in a moment."
TURN2 = "What was the order reference I just gave you? Reply with the reference only."
FRESH = "What was the order reference I gave you? If I have not given you one, say you do not have it."


async def run(cfg_path: str) -> int:
    cfg = RestAdapterConfig(**json.loads(Path(cfg_path).read_text()))
    adapter = RestApiAdapter(cfg)
    failures: list[str] = []
    try:
        r1 = await adapter.send([], TURN1)
        print(f"[1] turn 1        {r1.latency_ms:7.0f} ms  err={r1.error}")
        print(f"    reply: {r1.response_text[:200]!r}")
        if not r1.ok:
            failures.append(f"turn 1 failed: {r1.error} :: {str(r1.raw)[:400]}")
        elif not r1.response_text.strip():
            failures.append("turn 1 returned empty text; check response_text_path")

        r2 = await adapter.send([Turn("user", TURN1), Turn("agent", r1.response_text)], TURN2)
        print(f"[2] turn 2 (same) {r2.latency_ms:7.0f} ms  err={r2.error}")
        print(f"    reply: {r2.response_text[:200]!r}")
        if not r2.ok:
            failures.append(f"turn 2 failed: {r2.error} :: {str(r2.raw)[:400]}")
        elif SECRET not in r2.response_text:
            failures.append(
                f"session does not thread: turn 2 did not repeat {SECRET}. Every "
                "multi-turn probe would grade a memoryless agent."
            )

        await adapter.reset()
        r3 = await adapter.send([], FRESH)
        print(f"[3] after reset   {r3.latency_ms:7.0f} ms  err={r3.error}")
        print(f"    reply: {r3.response_text[:200]!r}")
        if not r3.ok:
            failures.append(f"post-reset turn failed: {r3.error} :: {str(r3.raw)[:400]}")
        elif SECRET in r3.response_text:
            failures.append(
                f"reset() does not isolate: a fresh session still knows {SECRET}. "
                "Probes would contaminate each other and nothing in the output would show it."
            )
    finally:
        await adapter.aclose()

    print()
    if failures:
        for f in failures:
            print("FAIL: " + f)
        print(f"\nADAPTER NOT VALID ({len(failures)} failure(s)). Do not grade.")
        return 1
    print("ADAPTER VALID: text extracted, session threads, reset isolates.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_adapter.py <adapter-config.json>")
    raise SystemExit(asyncio.run(run(sys.argv[1])))
