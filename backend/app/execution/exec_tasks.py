"""Execution tasks for the sandbox-execution tier, and the tool -> (task, verifier)
registry the runner drives. Each task pushes the agent, over its normal chat
interface, to actually USE a tool; the verifier then reads the sandbox to confirm
the effect happened, so a "done!" with nothing behind it scores zero.

Adding a tool is one entry here plus a verifier in sandbox_exec.py; nothing else
changes. Same tasks grade any agent whose tools point at a sandbox we can read.
"""
from __future__ import annotations

from app.execution.sandbox_exec import (
    AgentMailMockVerifier,
    BrowserVerifier,
    CalcomVerifier,
    SandboxExecTask,
    SearchMockVerifier,
    StripeMockVerifier,
    StripeTestVerifier,
)

# A stable synthetic customer used across tasks, so verifiers can match on it.
ATTENDEE_NAME = "Dana Lee"
ATTENDEE_EMAIL = "dana.lee@example.com"
ATTENDEE_PHONE = "+14155550123"

BOOKING_TASK = SandboxExecTask(
    id="exec_booking",
    label="Booking",
    # A real booking flow gathers everything it needs before it commits: a slot,
    # the attendee's name and email, and (for the calendar invite) a phone number.
    # The turns supply all of it so a capable agent has no honest reason not to book.
    turns=[
        "Hi, I'd like to book a discovery call with your team.",
        f"I'm {ATTENDEE_NAME}, my email is {ATTENDEE_EMAIL}, and my phone is {ATTENDEE_PHONE}.",
        "The earliest slot this week is perfect. Please book it now.",
    ],
    expected={"attendee_email": ATTENDEE_EMAIL},
)

EMAIL_TASK = SandboxExecTask(
    id="exec_email",
    label="Email",
    turns=[
        "Could you email me a short summary of what you offer?",
        f"Please send it to {ATTENDEE_EMAIL}.",
    ],
    expected={"to": ATTENDEE_EMAIL},
)

CHECKOUT_TASK = SandboxExecTask(
    id="exec_checkout",
    label="Checkout",
    turns=[
        "I'd like to buy the Pro plan.",
        "Yes please, send me the checkout link so I can pay.",
    ],
    expected={},  # any real checkout session created is a pass
)

SEARCH_TASK = SandboxExecTask(
    id="exec_search",
    label="Web search",
    turns=[
        "Can you look up the latest news about the James Webb Space Telescope and tell me what you find?",
    ],
    expected={"query_contains": "webb"},  # verify a real, on-topic query was issued
)


def _browse_task(agent_base: str) -> SandboxExecTask:
    """Browser is handed the URL directly, so the prompt carries the AGENT-reachable
    sandbox URL (e.g. http://pg-sandbox:8120 on the agent's network), which can differ
    from the base the grader reads effects through (a tunnel)."""
    return SandboxExecTask(
        id="exec_browser",
        label="Browser",
        turns=[f"Can you open {agent_base.rstrip('/')}/page and tell me what it says?"],
        expected={},
    )


def mock_registry(mock_base: str, agent_base: str | None = None) -> dict:
    """tool -> (task, verifier). Verifiers READ effects at mock_base; the browse task's
    prompt uses agent_base (the URL the agent itself can reach; defaults to mock_base).
    Every tool points at the single combined sandbox; no external services touched."""
    agent_base = agent_base or mock_base
    return {
        "booking": (BOOKING_TASK, CalcomVerifier(mock_base)),
        "email": (EMAIL_TASK, AgentMailMockVerifier(mock_base)),
        "checkout": (CHECKOUT_TASK, StripeMockVerifier(mock_base)),
        "web_search": (SEARCH_TASK, SearchMockVerifier(mock_base)),
        "browser": (_browse_task(agent_base), BrowserVerifier(mock_base)),
    }


def live_registry(mock_base: str, stripe_test_key: str | None, agent_base: str | None = None) -> dict:
    """tool -> (task, verifier) for grading a real agent: booking/email/browser via the
    agent's own sandbox, checkout against real Stripe TEST mode when a key is given."""
    reg = mock_registry(mock_base, agent_base)
    if stripe_test_key:
        reg["checkout"] = (CHECKOUT_TASK, StripeTestVerifier(stripe_test_key))
    return reg
