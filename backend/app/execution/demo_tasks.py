"""Example execution tasks with a mock sandbox, for harness validation.

These prove the harness end to end with in-memory tools. Wiring real agents
(a tool-calling adapter over a skill agent, or a third party's tool API) reuses
the same ExecutionTask shape; only the tool handlers change from mock to real.
"""
from __future__ import annotations

from app.execution.harness import ExecutionTask, SandboxToolEnv, Tool


def _book_call(state: dict, date: str = "", time: str = "", **extra):
    if extra:
        raise ValueError(f"unexpected argument(s): {list(extra)}")
    if not date or not time:
        raise ValueError("both 'date' and 'time' are required")
    parts = time.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"'time' must be HH:MM, got {time!r}")
    state.setdefault("bookings", []).append({"date": date, "time": time})
    return {"status": "booked", "date": date, "time": time}


BOOK_TOOL = Tool(
    name="book_call",
    schema={
        "name": "book_call",
        "description": "Book a call on the team calendar at a specific date and time.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO date, e.g. 2026-07-21"},
                "time": {"type": "string", "description": "24h HH:MM, e.g. 14:00"},
            },
            "required": ["date", "time"],
        },
    },
    handler=_book_call,
)


def _booked_tue_2pm(env: SandboxToolEnv) -> float:
    for b in env.state.get("bookings", []):
        if b["date"] == "2026-07-21" and b["time"] == "14:00":
            return 1.0
    return 0.0


BOOKING_TASK = ExecutionTask(
    id="exec_book_call",
    prompt="Book a call for Tuesday, July 21 (2026-07-21) at 14:00.",
    tools=[BOOK_TOOL],
    success=_booked_tue_2pm,
    optimal_steps=1,
)

DEMO_TASKS = [BOOKING_TASK]
