"""Agent adapters: how the grader talks to an agent it does not control."""

from __future__ import annotations

from .aivonic import aivonic_adapter, aivonic_agent_config
from .base import AgentAdapter, AgentReply, Turn
from .config import (
    AuthConfig,
    HistoryConfig,
    RestAdapterConfig,
    SessionConfig,
)
from .rest import RestApiAdapter
from .socketio_adapter import SocketIOAdapter, aivonic_socketio_adapter

__all__ = [
    "AgentAdapter",
    "AgentReply",
    "Turn",
    "RestApiAdapter",
    "RestAdapterConfig",
    "AuthConfig",
    "HistoryConfig",
    "SessionConfig",
    "SocketIOAdapter",
    "aivonic_adapter",
    "aivonic_agent_config",
    "aivonic_socketio_adapter",
]


def build_adapter(kind: str, config: dict) -> AgentAdapter:
    """Registry entrypoint: build an adapter from a stored spec.

    ``kind`` selects the transport; ``config`` is the serialized adapter config
    from the agent registry. Only ``rest`` exists today; ``widget`` arrives in a
    later phase, and dispatching here keeps callers unchanged when it does.
    """
    if kind == "rest":
        return RestApiAdapter(RestAdapterConfig(**config))
    if kind == "socketio":
        return SocketIOAdapter(**config)
    raise ValueError(f"unknown adapter kind: {kind!r}")
