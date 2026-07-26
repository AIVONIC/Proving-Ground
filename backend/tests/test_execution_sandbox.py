"""Mock Cal.com sandbox + chat-driven execution dimension."""
import asyncio

from fastapi.testclient import TestClient

from app.adapters.base import AgentReply
from app.execution.calcom_mock import app as calcom_app
from app.execution.sandbox_exec import SandboxExecutionDimension, SandboxExecTask


# ── mock Cal.com speaks the real contract and records effects ──
def test_mock_slots_and_booking_roundtrip():
    c = TestClient(calcom_app)
    c.post("/_sandbox/reset")
    slots = c.get("/v2/slots").json()
    assert slots["data"], "should offer availability"
    a_day = next(iter(slots["data"]))
    a_slot = slots["data"][a_day][0]["start"]
    r = c.post("/v2/bookings", json={
        "eventTypeId": 1, "start": a_slot,
        "attendee": {"name": "Test User", "email": "test@example.com", "timeZone": "UTC"},
        "bookingFieldsResponses": {"title": "Aivonic discovery call", "phone": "+46700000000"},
    })
    assert r.status_code == 201 and r.json()["data"]["id"]
    booked = c.get("/_sandbox/bookings").json()
    assert booked["count"] == 1
    assert booked["bookings"][0]["attendee_email"] == "test@example.com"
    c.post("/_sandbox/reset")
    assert c.get("/_sandbox/bookings").json()["count"] == 0


# ── the dimension: drive turns, then verify the sandbox effect ──
class FakeAdapter:
    def __init__(self, replies, fail_turn=None):
        self.replies, self.fail_turn, self.i = replies, fail_turn, 0

    async def reset(self):
        self.i = 0

    async def aclose(self):
        pass

    async def send(self, history, message):
        i = self.i
        self.i += 1
        if self.fail_turn == i:
            return AgentReply(response_text="", latency_ms=5, error="timeout")
        return AgentReply(response_text=self.replies[i] if i < len(self.replies) else "ok", latency_ms=5)


class FakeVerifier:
    def __init__(self, completion, detail="fake"):
        self.completion, self.detail, self.reset_called = completion, detail, False

    async def reset(self):
        self.reset_called = True

    async def verify(self, expected):
        return self.completion, self.detail


def _run(coro):
    return asyncio.run(coro)


TASK = SandboxExecTask(id="book", turns=["book a call", "the earliest works"], expected={})


def test_verified_effect_scores_full():
    v = FakeVerifier(1.0)
    r = _run(SandboxExecutionDimension(v).run_task(FakeAdapter(["ok", "booked"]), TASK))
    assert v.reset_called                 # sandbox was reset before the task
    assert r.completion == 1.0 and r.reliability == 1.0 and r.score == 1.0


def test_no_effect_scores_zero():
    r = _run(SandboxExecutionDimension(FakeVerifier(0.0)).run_task(FakeAdapter(["I have booked it!"]), TASK))
    assert r.completion == 0.0 and r.score == 0.0     # said it booked, sandbox says otherwise


def test_transport_error_lowers_reliability_not_completion():
    r = _run(SandboxExecutionDimension(FakeVerifier(1.0)).run_task(
        FakeAdapter(["ok", "booked"], fail_turn=0), TASK))
    assert r.completion == 1.0
    assert r.reliability == 0.5           # one of two turns failed on our side
    assert r.score == round(1.0 * (0.85 + 0.15 * 0.5), 3)


def test_subscore_scales_to_ten():
    r = _run(SandboxExecutionDimension(FakeVerifier(1.0)).run_task(FakeAdapter(["ok"]), TASK))
    assert SandboxExecutionDimension.subscore([r]) == 10.0
