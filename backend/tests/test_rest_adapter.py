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


def make_resource_style_client() -> httpx.AsyncClient:
    """A vendor that models a conversation as a RESOURCE: one URL starts it, a
    per-conversation URL continues it, and the reply comes back as a sequence of
    bubbles rather than one string. Typebot is shaped exactly like this, and so is
    anything else that returns content blocks."""
    seen: dict[str, list[str]] = {"urls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["urls"].append(str(request.url))
        body = __import__("json").loads(request.content)
        msg = body["message"]
        bubbles = [
            {"id": "b1", "type": "text", "content": {"type": "markdown", "markdown": f"part1:{msg}"}},
            {"id": "b2", "type": "text", "content": {"type": "markdown", "markdown": "part2"}},
        ]
        if str(request.url).endswith("/startChat"):
            assert "session" not in body, "no session id before one is minted"
            return httpx.Response(200, json={"sessionId": "sess-9", "messages": bubbles})
        assert str(request.url) == "https://vendor.example/api/sessions/sess-9/continueChat"
        return httpx.Response(200, json={"messages": bubbles})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._pg_seen = seen  # type: ignore[attr-defined]
    return client


def resource_style_config() -> RestAdapterConfig:
    return RestAdapterConfig(
        name="mock-resource-vendor",
        endpoint="https://vendor.example/api/bots/northwind/startChat",
        session_endpoint="https://vendor.example/api/sessions/{{session_id}}/continueChat",
        body_template={"message": "{{message}}"},
        history=HistoryConfig(mode="server_session"),
        session=SessionConfig(capture_path="sessionId", send_in="url"),
        response_text_path="messages.*.content.markdown",
    )


@pytest.mark.asyncio
async def test_session_endpoint_and_multipart_reply():
    client = make_resource_style_client()
    adapter = RestApiAdapter(resource_style_config(), client=client)

    r1 = await adapter.send([], "hello")
    assert r1.ok, r1.error
    # Both bubbles, in order. Taking only the first would silently truncate the
    # answer and still look like a real one.
    assert r1.response_text == "part1:hello\n\npart2"

    r2 = await adapter.send([Turn("user", "hello"), Turn("agent", r1.response_text)], "again")
    assert r2.ok and r2.response_text == "part1:again\n\npart2"

    urls = client._pg_seen["urls"]  # type: ignore[attr-defined]
    assert urls[0].endswith("/bots/northwind/startChat")
    assert urls[1] == "https://vendor.example/api/sessions/sess-9/continueChat"

    # reset() drops the session, so the next turn starts a new conversation at the
    # START url. Without this a "fresh session" probe would keep talking to the old one.
    await adapter.reset()
    await adapter.send([], "third")
    assert urls[2].endswith("/bots/northwind/startChat")


@pytest.mark.asyncio
async def test_empty_multipart_reply_is_an_error_not_silence():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sessionId": "s", "messages": []})

    cfg = resource_style_config()
    adapter = RestApiAdapter(cfg, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = await adapter.send([], "hello")
    assert not r.ok
    assert r.error and r.error.startswith("no_text_at_path:")


def test_dig_wildcard_shapes():
    from app.adapters.rest import dig

    # Fan out over a list, skipping parts that do not carry the field.
    doc = {"messages": [{"c": {"t": "a"}}, {"c": {}}, {"c": {"t": "b"}}]}
    assert dig(doc, "messages.*.c.t") == ["a", "b"]
    # Nested wildcards flatten rather than nesting.
    doc2 = {"m": [{"p": [{"t": "a"}, {"t": "b"}]}, {"p": [{"t": "c"}]}]}
    assert dig(doc2, "m.*.p.*.t") == ["a", "b", "c"]
    # A wildcard over a non-container is a miss, not a crash.
    assert dig({"m": "scalar"}, "m.*.t") is None
    # Numeric indexing still works exactly as before.
    assert dig(doc, "messages.0.c.t") == "a"


@pytest.mark.asyncio
async def test_first_turn_body_is_sent_once_and_then_dropped():
    """An API that takes conversation-creation fields on the opening request and
    rejects them once a session exists (Onyx is exactly this). Getting it wrong
    in the other direction is the dangerous one: no first-turn fields at all
    still returns fluent answers, from the vendor's DEFAULT assistant rather than
    the one under test, and nothing in the transcript says so."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        bodies.append(body)
        if "session" in body:
            # The TEMPLATE's own nested key stays; only the first-turn field goes.
            assert body["setup"] == {"keep": True}, \
                f"creation fields must not be resent with a session: {body['setup']}"
            return httpx.Response(200, json={"answer": "later", "session": "s1"})
        assert body["setup"]["persona_id"] == 7, "opening turn must carry the assistant id"
        assert body["setup"]["keep"] is True, "nested template keys must survive the merge"
        return httpx.Response(200, json={"answer": "first", "session": "s1"})

    cfg = RestAdapterConfig(
        name="mock-creation-vendor",
        endpoint="https://vendor.example/chat",
        body_template={"message": "{{message}}", "setup": {"keep": True}},
        first_turn_body={"setup": {"persona_id": 7}},
        history=HistoryConfig(mode="server_session"),
        session=SessionConfig(capture_path="session", send_in="body", send_key="session"),
        response_text_path="answer",
    )
    adapter = RestApiAdapter(cfg, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await adapter.send([], "one")).response_text == "first"
    assert (await adapter.send([], "two")).response_text == "later"
    # A reset starts a new conversation, so the creation fields must come back.
    await adapter.reset()
    assert (await adapter.send([], "three")).response_text == "first"
    assert [("setup" in b and "persona_id" in b["setup"]) for b in bodies] == [True, False, True]
