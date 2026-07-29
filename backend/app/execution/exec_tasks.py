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
    CalcomVerifier,
    SandboxExecTask,
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


def mock_registry(mock_base: str) -> dict:
    """tool -> (task, verifier) where every tool points at the single combined mock
    (calcom_mock). The self-contained proof/config; no external services touched."""
    return {
        "booking": (BOOKING_TASK, CalcomVerifier(mock_base)),
        "email": (EMAIL_TASK, AgentMailMockVerifier(mock_base)),
        "checkout": (CHECKOUT_TASK, StripeMockVerifier(mock_base)),
    }


def live_registry(mock_base: str, stripe_test_key: str | None) -> dict:
    """tool -> (task, verifier) for grading a real agent: booking/email via the agent's
    own sandbox (mock_base), checkout against real Stripe TEST mode when a key is given
    (falls back to the mock verifier otherwise)."""
    reg = mock_registry(mock_base)
    if stripe_test_key:
        reg["checkout"] = (CHECKOUT_TASK, StripeTestVerifier(stripe_test_key))
    return reg
