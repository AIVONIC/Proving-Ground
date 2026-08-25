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

    Numeric segments index lists ("choices.0.message.content"). A "*" segment
    fans out over every element of a list (or every value of a dict), applies the
    rest of the path to each, and returns the leaves as a flat list with misses
    dropped. Returns None if any segment is missing rather than raising, so a
    malformed reply degrades to an empty extraction instead of crashing a run.
    """
    cur = obj
    segs = path.split(".")
    for i, seg in enumerate(segs):
        if cur is None:
            return None
        if seg == "*":
            rest = ".".join(segs[i + 1:])
            if isinstance(cur, dict):
                items = list(cur.values())
            elif isinstance(cur, list):
                items = cur
            else:
                return None
            out: list[Any] = []
            for item in items:
                got = dig(item, rest) if rest else item
                if got is None:
                    continue
                # A nested "*" already returned a list; flatten so the caller
                # always sees leaves, never a ragged tree.
                out.extend(got) if isinstance(got, list) else out.append(got)
            return out
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


def _deep_merge(base: dict, extra: dict) -> None:
    """Merge ``extra`` into ``base`` in place, recursing into nested dicts so a
    first-turn field does not blow away a whole sub-object of the template."""
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


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

    def _build_request(self, history: list[Turn], message: str) -> tuple[str, dict, dict, dict]:
        """Return (url, json_body, headers, query_params) for one call."""
        cfg = self.config
        variables = {"message": message, **cfg.static_vars}
        if self._session_id is not None:
            variables["session_id"] = self._session_id

        template = copy.deepcopy(cfg.body_template)
        if self._session_id is None and cfg.first_turn_body:
            _deep_merge(template, copy.deepcopy(cfg.first_turn_body))
        body = substitute(template, variables)

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

        # Thread an established session id. Once one exists, a configured
        # session_endpoint takes over as the URL for every remaining turn.
        url = cfg.endpoint
        if cfg.session and self._session_id is not None:
            if cfg.session_endpoint:
                url = substitute(cfg.session_endpoint, variables)
            where = cfg.session.send_in
            key = cfg.session.send_key
            if where == "body":
                set_at(body, key, self._session_id)
            elif where == "header":
                headers[key] = self._session_id
            elif where == "query":
                params[key] = self._session_id
            # "url": already threaded by the substitution above and deliberately
            # nowhere else, so an API that rejects unknown body keys still works.

        return url, body, headers, params

    async def send(self, history: list[Turn], message: str) -> AgentReply:
        cfg = self.config
        url, body, headers, params = self._build_request(history, message)

        start = time.perf_counter()
        try:
            if cfg.method == "GET":
                resp = await self._client.get(url, headers=headers, params={**params, **body})
            else:
                resp = await self._client.post(url, headers=headers, params=params, json=body)
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
        if isinstance(text, list):
            # A "*" path fans out to every part of a multi-part reply. Join them
            # into the one string every dimension downstream reads. An empty list
            # is a miss, not an empty answer, so it falls through to the error
            # below rather than being graded as silence.
            parts = [str(t) for t in text if t is not None and str(t) != ""]
            text = cfg.response_text_join.join(parts) if parts else None
        if text is None:
            return AgentReply(
                "", latency_ms,
                error=f"no_text_at_path:{cfg.response_text_path}",
                raw=data,
            )
        return AgentReply(str(text), latency_ms, tokens=tokens, raw=data)
