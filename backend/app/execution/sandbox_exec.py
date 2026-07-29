"""Execution grading for black-box agents via an owner-provided tool sandbox.

The universal harness (harness.py) grades agents that let us hand them tools. Many
real agents instead call their OWN tools (SPARK calls its own booking skill). For
those, the vendor points the agent at a test/sandbox environment we can observe,
and we grade like this: drive the agent through a task over its normal chat
interface, then read the sandbox to verify the effect actually happened.

The verifier is an interface, so the SAME dimension grades SPARK (verifier reads our
mock Cal.com) or any third party (verifier reads their staging system / a service's
test API). Nothing here is SPARK-specific.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Protocol

import httpx

from app.dimensions.base import Turn
from app.execution.harness import ExecResult


class EffectVerifier(Protocol):
    """Reads a tool sandbox to confirm an expected effect occurred."""
    def reset(self) -> Awaitable[None]: ...
    def verify(self, expected: dict) -> Awaitable[tuple[float, str]]: ...  # 0..1, detail


@dataclass
class SandboxExecTask:
    """Drive `turns` through the agent, then check `expected` in the sandbox."""
    id: str
    turns: list[str]
    expected: dict = field(default_factory=dict)
    label: str = ""


class SandboxExecutionDimension:
    """Chat-driven execution grading against an owner-provided sandbox."""
    id = "execution"

    def __init__(self, verifier: EffectVerifier, reliability_weight: float = 0.15):
        self.verifier = verifier
        self.rw = reliability_weight

    async def run_task(self, adapter, task: SandboxExecTask) -> ExecResult:
        await self.verifier.reset()
        await adapter.reset()  # fresh agent session per task
        history: list[Turn] = []
        transcript, transport_errors = [], 0
        for msg in task.turns:
            reply = await adapter.send(history, msg)
            if not reply.ok:
                transport_errors += 1  # our-side transport fault, not the agent's doing
            text = reply.response_text or ""
            history.extend([Turn("user", msg), Turn("agent", text)])
            transcript.append({"user": msg, "agent": text[:400], "ok": reply.ok})
        completion, detail = await self.verifier.verify(task.expected)
        reliability = 1.0 if not transport_errors else max(0.0, 1.0 - transport_errors / max(1, len(task.turns)))
        score = completion * ((1.0 - self.rw) + self.rw * reliability)
        transcript.append({"verify": detail, "completion": completion})
        return ExecResult(task.id, round(completion, 3), round(reliability, 3),
                          len(task.turns), round(score, 3), transcript)

    async def run(self, adapter, tasks: list[SandboxExecTask]) -> list[ExecResult]:
        return [await self.run_task(adapter, t) for t in tasks]

    @staticmethod
    def subscore(results: list[ExecResult]) -> float:
        if not results:
            return 0.0
        return round(10.0 * sum(r.score for r in results) / len(results), 2)


class CalcomVerifier:
    """Reads the mock Cal.com store to verify a booking effect. The reference verifier
    for the owner-provided-sandbox tier; a third party would supply an equivalent that
    reads their staging calendar."""

    def __init__(self, mock_base: str):
        self.base = mock_base.rstrip("/")

    async def reset(self) -> None:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{self.base}/_sandbox/reset")

    async def verify(self, expected: dict) -> tuple[float, str]:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self.base}/_sandbox/bookings")
        bookings = r.json().get("bookings", [])
        if not bookings:
            return 0.0, "no booking was created in the sandbox"
        email = (expected.get("attendee_email") or "").lower()
        by_email = [b for b in bookings if not email or (b.get("attendee_email", "").lower() == email)]
        if not by_email:
            got = ", ".join(b.get("attendee_email", "?") for b in bookings)
            return 0.4, f"a booking was created but for the wrong attendee ({got}, wanted {email})"
        want = expected.get("start_contains")
        if want:
            if any(want in b.get("start", "") for b in by_email):
                return 1.0, f"verified: booking created for {email} at {want}"
            got = ", ".join(b.get("start", "?") for b in by_email)
            return 0.6, f"booked for {email} but not the requested time (got {got}, wanted {want})"
        return 1.0, f"verified: booking created for {email}"


class AgentMailMockVerifier:
    """Reads the email sandbox (agentmail_mock) to verify a send effect. The agent's
    email tool is pointed at the mock via AGENTMAIL_API_BASE; we confirm a message
    actually went to the expected recipient rather than trusting the agent's 'sent!'."""

    def __init__(self, mock_base: str):
        self.base = mock_base.rstrip("/")

    async def reset(self) -> None:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{self.base}/_sandbox/reset")

    async def verify(self, expected: dict) -> tuple[float, str]:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self.base}/_sandbox/emails")
        emails = r.json().get("emails", [])
        if not emails:
            return 0.0, "no email was sent in the sandbox"
        want_to = (expected.get("to") or "").lower()
        matches = [e for e in emails if not want_to or want_to in e.get("to", [])]
        if not matches:
            got = ", ".join(",".join(e.get("to", [])) for e in emails)
            return 0.4, f"an email was sent but to the wrong recipient ({got}, wanted {want_to})"
        needle = (expected.get("body_contains") or "").lower()
        if needle:
            if any(needle in (e.get("text", "") + " " + e.get("subject", "")).lower() for e in matches):
                return 1.0, f"verified: email sent to {want_to} containing '{needle}'"
            return 0.7, f"email sent to {want_to} but missing expected content '{needle}'"
        return 1.0, f"verified: email sent to {want_to}"


class SearchMockVerifier:
    """Reads the search sandbox to verify the agent actually issued a query, rather than
    fabricating a 'looked it up'. The agent's web-search skill points here via
    SEARCH_API_BASE. Web search has no persistent effect like a booking, so the observable
    effect IS the outbound query, recorded by the sandbox."""

    def __init__(self, mock_base: str):
        self.base = mock_base.rstrip("/")

    async def reset(self) -> None:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{self.base}/_sandbox/reset")

    async def verify(self, expected: dict) -> tuple[float, str]:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self.base}/_sandbox/searches")
        searches = r.json().get("searches", [])
        if not searches:
            return 0.0, "no search query was issued in the sandbox (the agent answered without searching)"
        needle = (expected.get("query_contains") or "").lower()
        if needle:
            if any(needle in (s.get("query", "") or "").lower() for s in searches):
                return 1.0, f"verified: a real search was issued containing '{needle}'"
            got = "; ".join(s.get("query", "") for s in searches)
            return 0.6, f"a search was issued but without '{needle}' (got: {got})"
        return 1.0, f"verified: {len(searches)} real search query(ies) issued"


class BrowserVerifier:
    """Reads the browse sandbox to verify the agent actually fetched the page it was
    given, rather than answering from memory or fabricating. The browser skill takes a
    URL directly, so the task hands it a sandbox URL and this confirms the GET landed."""

    def __init__(self, mock_base: str):
        self.base = mock_base.rstrip("/")

    async def reset(self) -> None:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{self.base}/_sandbox/reset")

    async def verify(self, expected: dict) -> tuple[float, str]:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self.base}/_sandbox/fetches")
        fetches = r.json().get("fetches", [])
        if not fetches:
            return 0.0, "the agent did not fetch the page (no GET reached the sandbox)"
        return 1.0, f"verified: the agent fetched the sandbox page ({len(fetches)} GET)"


class StripeMockVerifier:
    """Reads the checkout sandbox (calcom_mock's /v1/checkout/sessions store) to verify a
    session was actually created. The mock-backed counterpart to StripeTestVerifier: use
    this when the agent's checkout tool points at our mock; use StripeTestVerifier when it
    runs against real Stripe test mode. reset() clears via /_sandbox/reset (shared store)."""

    def __init__(self, mock_base: str):
        self.base = mock_base.rstrip("/")

    async def reset(self) -> None:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{self.base}/_sandbox/reset")

    async def verify(self, expected: dict) -> tuple[float, str]:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self.base}/_sandbox/sessions")
        sessions = r.json().get("sessions", [])
        if not sessions:
            return 0.0, "no checkout session was created in the sandbox"
        needle = (expected.get("params_contains") or "").lower()
        if needle:
            if any(needle in (s.get("params", "") or "").lower() for s in sessions):
                return 1.0, f"verified: checkout session created containing '{needle}'"
            return 0.6, f"a checkout session was created but without expected '{needle}'"
        return 1.0, f"verified: checkout session {sessions[-1].get('id')} created"


class StripeTestVerifier:
    """Verifies a Stripe checkout session was actually created, in TEST MODE. The agent
    must run with a Stripe test key (sk_test_...). Stripe is append-only, so reset()
    records a cutoff timestamp and verify() only counts sessions created after it, so a
    prior task's session never counts for this one."""

    def __init__(self, secret_key: str):
        self.key = secret_key
        self._cutoff = 0

    async def reset(self) -> None:
        import time
        self._cutoff = int(time.time())

    async def verify(self, expected: dict) -> tuple[float, str]:
        import asyncio
        try:
            import stripe
        except ImportError:
            return 0.0, "stripe library not available in the grader environment"
        stripe.api_key = self.key

        def _list():
            return stripe.checkout.Session.list(created={"gte": self._cutoff}, limit=20)

        try:
            sessions = (await asyncio.to_thread(_list)).data
        except Exception as e:
            return 0.0, f"could not read Stripe test sessions: {type(e).__name__}: {e}"
        if not sessions:
            return 0.0, "no checkout session was created in test mode"
        want_mode = expected.get("mode")
        min_amt = expected.get("min_amount_total")
        for s in sessions:
            # Stripe returns StripeObject, not a dict; use attribute access, not .get()
            s_mode = getattr(s, "mode", None)
            s_amt = getattr(s, "amount_total", None) or 0
            if want_mode and s_mode != want_mode:
                continue
            if min_amt and s_amt < min_amt:
                continue
            return 1.0, f"verified: checkout session {s.id} created (mode={s_mode}, amount_total={s_amt})"
        return 0.5, f"a checkout session was created but did not match expected (found {len(sessions)})"
