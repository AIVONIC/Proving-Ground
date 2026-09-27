"""Agent adapter interface.

The single net-new abstraction in the project. Every reused grading module
downstream only ever sees a normalized reply, so it does not care whether the
agent is one of ours, a third party's REST API, or (later) a browser widget.

A grading "conversation" is a sequence of ``send`` calls sharing one session.
Call ``reset`` to begin a fresh, independent session (used between probes that
must not share memory, and for cross-session memory tests).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "agent"]


@dataclass
class Turn:
    """One message in a conversation."""

    role: Role
    content: str


@dataclass
class AgentReply:
    """Normalized result of a single turn.

    ``response_text`` is the only field every dimension relies on. ``latency_ms``
    is always measured as wall-clock around the call, so it is available even for
    agents that report nothing about themselves. ``error`` is set (and
    ``response_text`` left empty) when the call failed; dimensions decide how to
    score a failed turn (a timeout is itself a reliability signal).
    """

    response_text: str
    latency_ms: float
    tokens: int | None = None
    raw: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class AgentAdapter(ABC):
    """Base class for anything we can grade.

    ``name`` identifies the target in reports. Subclasses hold whatever session
    state their transport needs and reset it in ``reset``.
    """

    name: str = field(default="agent")

    @abstractmethod
    async def send(self, history: list[Turn], message: str) -> AgentReply:
        """Send ``message`` given prior ``history`` and return a normalized reply.

        Implementations that rely on server-side session memory may ignore
        ``history`` and thread a captured session id instead; implementations
        that are stateless should send ``history`` to the agent each call.
        """

    async def reset(self) -> None:
        """Begin a fresh session. Default is a no-op for stateless adapters."""

    async def aclose(self) -> None:
        """Release transport resources. Default is a no-op."""

    async def __aenter__(self) -> "AgentAdapter":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()
