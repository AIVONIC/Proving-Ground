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
    # ``url`` means the id is threaded only by substituting it into
    # ``RestAdapterConfig.session_endpoint``; nothing is added to body/header/query.
    send_in: Literal["body", "header", "query", "url"] = "body"
    send_key: str = "session_id"


class LoginConfig(BaseModel):
    """How to obtain a session, so none has to be stored.

    A cookie pasted into a config file is a credential with a shelf life sitting
    in version control. This describes the login instead; the adapter performs it
    at run start and again if the session lapses mid-run.

    String values support ${ENV_VAR} so real credentials stay out of the repo.
    """

    endpoint: str
    method: Literal["POST", "GET"] = "POST"
    # fastapi-users and most OAuth-password flows want form encoding, not JSON.
    form: dict[str, str] = Field(default_factory=dict)
    json_body: dict[str, Any] = Field(default_factory=dict)
    # Exactly one of these says where the credential comes back.
    capture_cookie: str | None = None
    capture_path: str | None = None
    # How to present it on subsequent requests.
    header: str = "Cookie"
    template: str = "{name}={value}"


class RestAdapterConfig(BaseModel):
    """Everything needed to drive a third-party chat API as a black box."""

    name: str
    endpoint: str
    # Optional second URL, used from the turn a session id has been captured
    # onward, with "{{session_id}}" substituted. Some chat APIs model a
    # conversation as a resource: one URL starts it and a per-conversation URL
    # continues it (Typebot's /startChat then /sessions/{id}/continueChat, and
    # anything else shaped that way). Without this the adapter would re-POST to
    # the start URL every turn, which grades a brand-new conversation each time
    # and shows up as a memory failure rather than as a broken contract.
    session_endpoint: str | None = None
    method: Literal["POST", "GET"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    login: LoginConfig | None = None

    # JSON body with template variables. String values may contain "{{message}}",
    # "{{session_id}}", and any key from ``static_vars``. Example:
    #   {"message": "{{message}}", "stream": false}
    body_template: dict[str, Any] = Field(default_factory=dict)
    # Body fields sent ONLY on the opening turn of a conversation, deep-merged
    # into ``body_template`` before substitution. Some APIs take
    # conversation-creation parameters on the first request (which assistant,
    # which model, which project) and REJECT them afterwards: Onyx fails
    # validation if a request carries both a session id and session-creation
    # info. Folding them into body_template would therefore break every turn
    # after the first, and folding them nowhere would grade the vendor's default
    # assistant instead of the one under test -- a silent substitution that the
    # transcript would not reveal.
    first_turn_body: dict[str, Any] = Field(default_factory=dict)
    # Extra constant substitution values (e.g. an agent_id the API requires).
    static_vars: dict[str, str] = Field(default_factory=dict)

    history: HistoryConfig = Field(default_factory=HistoryConfig)
    session: SessionConfig | None = None

    # Dot-path into the JSON response holding the reply text, e.g. "response",
    # "data.reply", or "choices.0.message.content". List indices are numeric keys.
    # A "*" segment means "every element/value here": the remaining path is
    # applied to each and the leaves are joined with ``response_text_join``. That
    # is for APIs that return a reply as a sequence of parts rather than one
    # string (chat bubbles, content blocks). Taking element 0 of such a reply is
    # worse than an error, because a truncated answer still reads like an answer
    # and would be graded as one.
    response_text_path: str = "response"
    response_text_join: str = "\n\n"
    # Optional dot-path to a token count, if the API reports one.
    response_tokens_path: str | None = None

    timeout_s: float = 30.0
