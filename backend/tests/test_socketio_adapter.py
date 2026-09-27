"""Socket.IO adapter round-trip against a local echo server that mimics the
agent protocol (client emits `message`, server replies on `agent_response`)."""

from __future__ import annotations

import socketio
import pytest
from aiohttp import web

from app.adapters.socketio_adapter import SocketIOAdapter


async def _start_echo_server():
    sio = socketio.AsyncServer(async_mode="aiohttp")
    app = web.Application()
    sio.attach(app, socketio_path="socket.io")
    seen = {"connects": 0}

    @sio.event
    async def connect(sid, environ, auth):
        seen["connects"] += 1

    @sio.event
    async def message(sid, data):
        await sio.emit("agent_response", {"type": "agent_response", "content": f"echo:{data['content']}"}, room=sid)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port, seen


@pytest.mark.asyncio
async def test_socketio_roundtrip_and_reset():
    runner, port, seen = await _start_echo_server()
    try:
        adapter = SocketIOAdapter("test", f"http://127.0.0.1:{port}", agent_id="a1")
        r1 = await adapter.send([], "hello")
        assert r1.ok and r1.response_text == "echo:hello"
        assert r1.latency_ms >= 0

        r2 = await adapter.send([], "again")
        assert r2.ok and r2.response_text == "echo:again"

        await adapter.reset()          # drops the connection, new session
        r3 = await adapter.send([], "fresh")
        assert r3.ok and r3.response_text == "echo:fresh"
        assert seen["connects"] == 2   # one initial, one after reset

        await adapter.aclose()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_socketio_streamed_reply_is_assembled():
    """Replies that arrive as stream_chunk events must be concatenated on stream_end."""
    sio = socketio.AsyncServer(async_mode="aiohttp")
    app = web.Application()
    sio.attach(app, socketio_path="socket.io")

    @sio.event
    async def message(sid, data):
        mid = "m1"
        for i, part in enumerate(["Hello ", "there, ", "friend."]):
            await sio.emit("stream_chunk", {"messageId": mid, "chunk": part, "chunkIndex": i}, room=sid)
        await sio.emit("stream_end", {"messageId": mid}, room=sid)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        adapter = SocketIOAdapter("test", f"http://127.0.0.1:{port}", agent_id="a1")
        r = await adapter.send([], "hi")
        assert r.ok and r.response_text == "Hello there, friend."
        await adapter.aclose()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_socketio_timeout_is_not_agent_fault():
    """A server that never replies yields a retryable transport error, not a low score."""
    sio = socketio.AsyncServer(async_mode="aiohttp")
    app = web.Application()
    sio.attach(app, socketio_path="socket.io")

    @sio.event
    async def message(sid, data):
        return  # never emits agent_response

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        adapter = SocketIOAdapter("test", f"http://127.0.0.1:{port}", agent_id="a1", timeout_s=1.0)
        r = await adapter.send([], "hello?")
        assert not r.ok and r.error == "timeout_no_agent_response"
        await adapter.aclose()
    finally:
        await runner.cleanup()
