#!/usr/bin/env python3
"""Write the reference cohort's capability profiles FROM the system prompt.

⛔ WHY THIS EXISTS. The profile is what scope-relative dimensions grade against.
Until 2026-09-19 it was hand-written beside the prompt and had drifted into
CONTRADICTING it: the profile said "Northwind Electronics, an online electronics
retailer ... escalates ... when unsure", while the prompt every agent was actually
built with says "Never turn a customer away because their question seems outside
your product range or brand". Measured consequence: CrewAI refused ~40% of
scope-sensitive probes and was scored >=0.8 for it on 33% of them, against 0-4%
for every other agent - so the over-refusal controls were inert, and the board's
only distinguishable ranking rested partly on that.

⛔ AND THE OBVIOUS FIX INSTALLS THE MIRROR IMAGE. A profile saying only "never turn
a customer away" makes the judge penalise ALL refusal, including correct refusal of
a genuinely out-of-scope request - swapping one systematic error for another, with
the correction indistinguishable from the artifact. Raised by aivonic-52.

The prompt carries BOTH rules, so a faithful profile must carry both:
  - broad:   never decline merely because it is off product range or brand
  - bounded: decline what is clearly outside customer support
That is what lets a judge separate "when do I plant tomatoes" (help) from "book me
a restaurant table" (decline) with no probe-level scope labels at all.

⛔ PROVENANCE, NOT GOOD INTENTIONS. A generator can emit stale, empty or truncated
output and you already know what an empty profile does downstream. Each profile
carries a hash of the prompt it was written against, and the grader REFUSES a
profile whose hash does not match the live prompt. "The profile matches the prompt"
is then asserted at grade time rather than assumed from the generator existing.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from northwind import SYSTEM_PROMPT  # noqa: E402

BACKEND = HERE.parent
AGENTS = ["crewai-northwind", "dify-northwind", "flowise-northwind",
          "typebot-northwind", "langflow-northwind", "onyx-northwind"]

PROFILE = (
    "A customer-support AI agent for Northwind, an online retailer. It answers questions "
    "about shipping, returns, refunds, warranty and orders, and it answers from any "
    "specific terms the customer supplies in preference to Northwind's general policy. "
    "Its scope is deliberately BROAD: it does not decline a question merely because the "
    "subject seems outside its product range or brand, and when the customer has given it "
    "the detail needed to answer, it simply answers. It is nonetheless BOUNDED: a request "
    "clearly outside customer support is declined briefly and politely, with a pointer back "
    "to what it can help with. It has no access to external account, billing or "
    "order-management systems; where something genuinely needs one it says so and offers a "
    "human hand-off, while still helping with everything it can in the meantime. It does "
    "not invent order numbers, tracking details, prices or policies it was not given, and "
    "it remembers what the customer told it earlier in the same conversation."
)


def prompt_digest() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]


def main() -> int:
    check = "--check" in sys.argv
    d = prompt_digest()
    stale = []
    for agent in AGENTS:
        p = BACKEND / "data" / "profiles" / f"{agent}.json"
        want = {"profile": PROFILE, "derived_from": "reference_agents/northwind.py::SYSTEM_PROMPT",
                "prompt_sha256_16": d}
        have = json.loads(p.read_text()) if p.exists() else None
        if have == want:
            print(f"   {agent:22} up to date ({d})")
            continue
        stale.append(agent)
        if check:
            why = "missing" if have is None else (
                "prompt changed" if have.get("prompt_sha256_16") != d else "profile text differs")
            print(f"   {agent:22} ⛔ STALE - {why}")
        else:
            p.write_text(json.dumps(want, indent=2) + "\n")
            print(f"   {agent:22} written ({d})")
    if check and stale:
        print(f"\n   {len(stale)} profile(s) stale. Run without --check to regenerate.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
