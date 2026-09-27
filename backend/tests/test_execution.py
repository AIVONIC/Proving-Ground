"""Execution harness: verified effects + reliability axis."""
import asyncio

from app.execution.harness import ExecutionDimension
from app.execution.demo_tasks import BOOKING_TASK


class ScriptedAgent:
    """Replays a fixed list of actions, then finalizes."""
    def __init__(self, actions):
        self._actions = list(actions)

    async def act(self, prompt, tools, transcript):
        if self._actions:
            return self._actions.pop(0)
        return {"final": "done"}


def _run(agent, task):
    return asyncio.run(ExecutionDimension().run_task(agent, task))


def test_clean_skill_agent_scores_full():
    # A deterministic skill: one correct call, verified effect.
    agent = ScriptedAgent([{"tool": "book_call", "args": {"date": "2026-07-21", "time": "14:00"}}])
    r = _run(agent, BOOKING_TASK)
    assert r.completion == 1.0
    assert r.reliability == 1.0
    assert r.score == 1.0


def test_hallucinated_call_lowers_reliability():
    # Same end effect, but a bad first call (hallucinated arg) errors first.
    agent = ScriptedAgent([
        {"tool": "book_call", "args": {"day": "tuesday"}},              # invalid -> error
        {"tool": "book_call", "args": {"date": "2026-07-21", "time": "14:00"}},
    ])
    r = _run(agent, BOOKING_TASK)
    assert r.completion == 1.0          # it still got there
    assert r.reliability == 0.5         # one of two calls failed
    assert r.score < 1.0                # and it is penalized for the fumble
    assert r.score == round(1.0 * (0.8 + 0.2 * 0.5), 3)


def test_talks_but_does_nothing_scores_zero():
    # Says it will book, never calls the tool. The whole point of execution grading.
    agent = ScriptedAgent([{"final": "I have booked your call!"}])
    r = _run(agent, BOOKING_TASK)
    assert r.completion == 0.0
    assert r.score == 0.0


def test_unknown_tool_is_an_error_not_a_crash():
    agent = ScriptedAgent([{"tool": "cancel_order", "args": {}}])
    r = _run(agent, BOOKING_TASK)
    assert r.completion == 0.0
    assert r.reliability == 0.0
    assert r.calls[0]["error"] and "unknown tool" in r.calls[0]["error"]


def test_subscore_scales_to_ten():
    results = [_run(ScriptedAgent([{"tool": "book_call",
              "args": {"date": "2026-07-21", "time": "14:00"}}]), BOOKING_TASK)]
    assert ExecutionDimension.subscore(results) == 10.0
