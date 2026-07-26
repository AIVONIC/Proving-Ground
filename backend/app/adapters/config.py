"""Declarative config for the REST adapter.

The whole point of a config-driven adapter is that any vendor's chat API can be
graded without writing code: the owner declares their endpoint, how to build the
request, where the reply text lives, and how (if at all) the server threads a
session. Everything here is data, so an agent's adapter config can live in the
registry database and be edited without a deploy.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    """How to authenticate. ``none`` for open endpoints."""

    type: Literal["none", "bearer", "header", "query"] = "none"
    token: str | None = None
    # For type=header or type=query: the name of the header/param carrying the token.
    key: str = "Authorization"


class HistoryConfig(BaseModel):
    """How prior turns reach the agent.

    - ``server_session``: the server remembers the conversation; we send only the
      new message and thread a captured session id (see SessionConfig). History
      is not embedded in the body.
    - ``client_history``: the agent is stateless; we render the full transcript
      into the request body each call, at ``inject_at``.
    - ``none``: single-turn only.
    """

    mode: Literal["server_session", "client_history", "none"] = "server_session"
    # Dot-path in the body template where the rendered history list is inserted
    # (client_history mode only), e.g. "messages".
    inject_at: str | None = None
    # How each Turn maps to the agent's message object.
    role_key: str = "role"
    content_key: str = "content"
    user_role: str = "user"
    agent_role: str = "assistant"


class SessionConfig(BaseModel):
    """How a server-side session id is captured and threaded.

    On the first reply we read the id from ``capture_path`` and send it back on
    every subsequent call at ``send_in`` / ``send_key``. ``reset`` drops it so the
    next call starts a new session.
    """

    capture_path: str  # dot-path into the JSON response, e.g. "session_id" or "data.conversation.id"
    send_in: Literal["body", "header", "query"] = "body"
    send_key: str = "session_id"


class RestAdapterConfig(BaseModel):
    """Everything needed to drive a third-party chat API as a black box."""

    name: str
    endpoint: str
    method: Literal["POST", "GET"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    # JSON body with template variables. String values may contain "{{message}}",
    # "{{session_id}}", and any key from ``static_vars``. Example:
    #   {"message": "{{message}}", "stream": false}
    body_template: dict[str, Any] = Field(default_factory=dict)
    # Extra constant substitution values (e.g. an agent_id the API requires).
    static_vars: dict[str, str] = Field(default_factory=dict)

    history: HistoryConfig = Field(default_factory=HistoryConfig)
    session: SessionConfig | None = None

    # Dot-path into the JSON response holding the reply text, e.g. "response",
    # "data.reply", or "choices.0.message.content". List indices are numeric keys.
    response_text_path: str = "response"
    # Optional dot-path to a token count, if the API reports one.
    response_tokens_path: str | None = None

    timeout_s: float = 30.0
