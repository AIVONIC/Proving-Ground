"""Convenience factory for grading our own agents.

Our production agents are the first grading subjects (the Phase 1 gate). They are
FastAPI apps exposing an HTTP chat endpoint, so they are just a specific case of
the REST adapter. This factory fills in the known request/response shape so an
Aivonic agent can be graded by id alone.

NOTE: the exact endpoint path and response field are confirmed against a live
agent during the Phase 1 gate and adjusted here if they differ. Keeping this in
one factory means any contract fix is a one-line change, not a per-call edit.
"""

from __future__ import annotations

from .config import HistoryConfig, RestAdapterConfig, SessionConfig
from .rest import RestApiAdapter


def aivonic_agent_config(
    name: str,
    base_url: str,
    *,
    chat_path: str = "/api/chat",
    session_capture: str = "session_id",
    response_path: str = "response",
) -> RestAdapterConfig:
    """Build a RestAdapterConfig for one Aivonic agent.

    ``base_url`` is the agent's reachable host (e.g. an internal container URL for
    grading, or the public demo host). Server-side session memory is used so the
    agent's real memory behavior is exercised rather than a replayed transcript.
    """
    return RestAdapterConfig(
        name=name,
        endpoint=base_url.rstrip("/") + chat_path,
        method="POST",
        body_template={"message": "{{message}}"},
        history=HistoryConfig(mode="server_session"),
        session=SessionConfig(capture_path=session_capture, send_in="body", send_key="session_id"),
        response_text_path=response_path,
        timeout_s=30.0,
    )


def aivonic_adapter(name: str, base_url: str, **kwargs) -> RestApiAdapter:
    return RestApiAdapter(aivonic_agent_config(name, base_url, **kwargs))
