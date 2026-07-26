"""Config-driven REST adapter.

Drives any vendor chat API described by a ``RestAdapterConfig``. No per-vendor
code: request shape, response extraction, and session threading are all data.
"""

from __future__ import annotations

import copy
import time
from typing import Any

import httpx

from .base import AgentAdapter, AgentReply, Turn
from .config import RestAdapterConfig


def dig(obj: Any, path: str) -> Any:
    """Follow a dot-path through nested dicts/lists.

    Numeric segments index lists ("choices.0.message.content"). Returns None if
    any segment is missing rather than raising, so a malformed reply degrades to
    an empty extraction instead of crashing a grading run.
    """
    cur = obj
    for seg in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return None
    return cur


def set_at(obj: dict, path: str, value: Any) -> None:
    """Set ``value`` at a dot-path inside a dict, creating intermediate dicts."""
    segs = path.split(".")
    cur = obj
    for seg in segs[:-1]:
        cur = cur.setdefault(seg, {})
    cur[segs[-1]] = value


def substitute(node: Any, variables: dict[str, str]) -> Any:
    """Recursively replace "{{var}}" tokens in every string within a JSON tree."""
    if isinstance(node, str):
        out = node
        for key, val in variables.items():
            out = out.replace("{{" + key + "}}", val)
        return out
    if isinstance(node, dict):
        return {k: substitute(v, variables) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, variables) for v in node]
    return node


class RestApiAdapter(AgentAdapter):
    """Grade an agent reachable over HTTP."""

    def __init__(self, config: RestAdapterConfig, client: httpx.AsyncClient | None = None):
        super().__init__(name=config.name)
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=config.timeout_s)
        self._owns_client = client is None
        self._session_id: str | None = None

    async def reset(self) -> None:
        self._session_id = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _render_history(self, history: list[Turn]) -> list[dict[str, str]]:
        h = self.config.history
        return [
            {
                h.role_key: h.user_role if t.role == "user" else h.agent_role,
                h.content_key: t.content,
            }
            for t in history
        ]

    def _build_request(self, history: list[Turn], message: str) -> tuple[dict, dict, dict]:
        """Return (json_body, headers, query_params) for one call."""
        cfg = self.config
        variables = {"message": message, **cfg.static_vars}
        if self._session_id is not None:
            variables["session_id"] = self._session_id

        body = substitute(copy.deepcopy(cfg.body_template), variables)

        if cfg.history.mode == "client_history" and cfg.history.inject_at:
            # Render prior turns AND the current user message: a stateless agent must
            # receive the message it is being asked, as the final turn of the array.
            rendered = self._render_history(history)
            h = cfg.history
            rendered.append({h.role_key: h.user_role, h.content_key: message})
            set_at(body, cfg.history.inject_at, rendered)

        headers = dict(cfg.headers)
        params: dict[str, str] = {}

        # Auth.
        a = cfg.auth
        if a.type == "bearer" and a.token:
            headers["Authorization"] = f"Bearer {a.token}"
        elif a.type == "header" and a.token:
            headers[a.key] = a.token
        elif a.type == "query" and a.token:
            params[a.key] = a.token

        # Thread an established session id.
        if cfg.session and self._session_id is not None:
            where = cfg.session.send_in
            key = cfg.session.send_key
            if where == "body":
                set_at(body, key, self._session_id)
            elif where == "header":
                headers[key] = self._session_id
            else:
                params[key] = self._session_id

        return body, headers, params

    async def send(self, history: list[Turn], message: str) -> AgentReply:
        cfg = self.config
        body, headers, params = self._build_request(history, message)

        start = time.perf_counter()
        try:
            if cfg.method == "GET":
                resp = await self._client.get(cfg.endpoint, headers=headers, params={**params, **body})
            else:
                resp = await self._client.post(cfg.endpoint, headers=headers, params=params, json=body)
            latency_ms = (time.perf_counter() - start) * 1000.0
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            latency_ms = (time.perf_counter() - start) * 1000.0
            return AgentReply("", latency_ms, error=f"http_{e.response.status_code}", raw=e.response.text[:2000])
        except Exception as e:  # timeout, connection, decode
            latency_ms = (time.perf_counter() - start) * 1000.0
            return AgentReply("", latency_ms, error=f"{type(e).__name__}: {e}")

        # Capture a server session id on first reply.
        if cfg.session and self._session_id is None:
            captured = dig(data, cfg.session.capture_path)
            if captured is not None:
                self._session_id = str(captured)

        text = dig(data, cfg.response_text_path)
        tokens = dig(data, cfg.response_tokens_path) if cfg.response_tokens_path else None
        if text is None:
            return AgentReply(
                "", latency_ms,
                error=f"no_text_at_path:{cfg.response_text_path}",
                raw=data,
            )
        return AgentReply(str(text), latency_ms, tokens=tokens, raw=data)
