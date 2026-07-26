"""Verify the REST adapter against a mock vendor API with a non-obvious contract.

The contract here is deliberately awkward: reply text is nested at
``data.reply.text``, the session id is at ``data.conversation_id`` and must be
echoed back in the body under ``convo``, and the request wraps the message in a
nested object. If the adapter handles this, it handles most real APIs.
"""

from __future__ import annotations

import httpx
import pytest

from app.adapters import RestApiAdapter, Turn
from app.adapters.config import HistoryConfig, RestAdapterConfig, SessionConfig


def make_mock_client() -> httpx.AsyncClient:
    seen: dict[str, int] = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["calls"] += 1
        body = {} if not request.content else __import__("json").loads(request.content)
        # Server-side session: mint on first call, expect it echoed after.
        convo = body.get("payload", {}).get("convo")
        if seen["calls"] == 1:
            assert convo is None, "session id should not be sent before it is minted"
            convo = "sess-abc"
        else:
            assert convo == "sess-abc", "adapter must thread the captured session id"
        msg = body["payload"]["msg"]
        return httpx.Response(
            200,
            json={"data": {"reply": {"text": f"echo:{msg}"}, "conversation_id": "sess-abc"}},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def awkward_config() -> RestAdapterConfig:
    return RestAdapterConfig(
        name="mock-vendor",
        endpoint="https://vendor.example/chat",
        method="POST",
        body_template={"payload": {"msg": "{{message}}"}},
        history=HistoryConfig(mode="server_session"),
        session=SessionConfig(capture_path="data.conversation_id", send_in="body", send_key="payload.convo"),
        response_text_path="data.reply.text",
    )


@pytest.mark.asyncio
async def test_extraction_and_session_threading():
    adapter = RestApiAdapter(awkward_config(), client=make_mock_client())

    r1 = await adapter.send([], "hello")
    assert r1.ok and r1.response_text == "echo:hello"
    assert r1.latency_ms >= 0

    # Second call must echo the session id captured from the first reply.
    r2 = await adapter.send([Turn("user", "hello"), Turn("agent", "echo:hello")], "again")
    assert r2.ok and r2.response_text == "echo:again"

    # A reset drops the session so the next call mints a fresh one.
    await adapter.reset()
    assert adapter._session_id is None


@pytest.mark.asyncio
async def test_missing_text_path_is_an_error_not_a_crash():
    cfg = awkward_config()
    cfg.response_text_path = "data.reply.NOPE"
    adapter = RestApiAdapter(cfg, client=make_mock_client())
    r = await adapter.send([], "hi")
    assert not r.ok and r.error and r.error.startswith("no_text_at_path")


@pytest.mark.asyncio
async def test_http_error_is_captured_as_reliability_signal():
    def boom(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    adapter = RestApiAdapter(
        awkward_config(), client=httpx.AsyncClient(transport=httpx.MockTransport(boom))
    )
    r = await adapter.send([], "hi")
    assert not r.ok and r.error == "http_503"
    assert r.latency_ms >= 0
