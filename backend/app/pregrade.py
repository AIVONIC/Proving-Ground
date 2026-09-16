"""Refuse to spend money on a harness that is not wired correctly.

⛔ WHY THIS RUNS BEFORE EVERY GRADE.

A grade costs roughly five dollars and two to four hours. Twice now a full run
has been paid for and then thrown away because the HARNESS was wrong rather than
the agent:

  2026-09-14  Langflow graded 79.32 with memory 5.91. The adapter sent the
              literal string "{{session_id}}" as its session id, so all 471 turns
              across three runs shared ONE server-side session and every probe
              was answered against a transcript of unrelated conversations.
  same day    The obvious fix was also wrong: omit the id and Langflow echoes
              back the FLOW id, which is constant. Capture threads the whole run
              into one session too, while the config reads correctly.

Neither failure raised anything. Both produced a complete, plausible, publishable
number about somebody else's product. The only signal was a memory score three
points below the cohort, and that is indistinguishable from a platform that
genuinely forgets.

So this asks the questions a grade cannot ask itself, with TWO agent calls and
zero judge calls - a few cents against the five dollars it protects:

  1. Does the agent answer at all?
  2. Does it remember within one conversation?          (server_session only)
  3. Does a NEW conversation start clean?               (the one that was missed)

3 is the one that matters and the one nobody thinks to check, because a leaking
session makes memory look BETTER in isolation - the agent recalls everything.
It only shows up as damage when unrelated context bleeds into an unrelated probe.
Testing memory without testing isolation is how the second bug survived the fix
for the first.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass
class PregradeResult:
    ok: bool
    lines: list[str]

    def report(self) -> str:
        return "\n".join(self.lines)


_MARK = "NW-{}"          # a fact the agent cannot know unless it was told
_ASK = "What order reference did I give you, exactly?"


async def check_adapter(factory, *, server_session: bool) -> PregradeResult:
    """Two conversations, four turns total. Costs pennies."""
    lines: list[str] = []
    ok = True
    token = uuid.uuid4().hex[:6].upper()
    mark = _MARK.format(token)

    a = factory()
    try:
        await a.reset()
        r1 = await a.send([], f"My order reference is {mark}. Please remember it.")
        # AFTER the send, not before: in capture mode reset() clears the id and the
        # first reply is what sets it, so reading it early always sees None and the
        # constant-id diagnostic silently never fires. The verdict was still right
        # (the leak test caught it) but the message that explains WHY was missing,
        # which is the difference between a five-minute fix and a two-hour one.
        sid_a = getattr(a, "_session_id", None)
        if r1.error or not r1.response_text.strip():
            return PregradeResult(False, [f"  FAIL  agent did not answer: {r1.error or 'empty reply'}"])
        lines.append(f"  ok    agent answers ({len(r1.response_text)} chars)")

        if not server_session:
            lines.append("  --    history mode is not server_session; isolation not applicable")
            return PregradeResult(True, lines)

        class _T:
            def __init__(s, role, content): s.role, s.content = role, content

        hist = [_T("user", f"My order reference is {mark}. Please remember it."),
                _T("agent", r1.response_text)]
        r2 = await a.send(hist, _ASK)
        remembers = mark in (r2.response_text or "")
        lines.append(f"  {'ok  ' if remembers else 'FAIL'}  recalls within a conversation"
                     f"{'' if remembers else ' - server_session is declared but the server is not keeping it'}")
        ok &= remembers

        await a.reset()
        r3 = await a.send([], _ASK)
        sid_b = getattr(a, "_session_id", None)
        leaks = mark in (r3.response_text or "")
        lines.append(f"  {'FAIL' if leaks else 'ok  '}  new conversation starts clean"
                     f"{' - CONTEXT IS LEAKING BETWEEN CONVERSATIONS' if leaks else ''}")
        ok &= not leaks

        if sid_a is not None and sid_a == sid_b:
            lines.append(f"  FAIL  session id is CONSTANT across conversations ({sid_a}) - "
                         f"every probe in the run will share one server-side session")
            ok = False
        elif sid_a is not None:
            lines.append(f"  ok    session id differs per conversation")
    finally:
        await a.aclose()
    return PregradeResult(ok, lines)
