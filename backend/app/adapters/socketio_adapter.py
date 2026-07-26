"""Socket.IO adapter.

Our own widget agents speak Socket.IO. The client emits a ``message`` event with
{content, metadata, agentId, sessionId}. Replies arrive one of two ways:
- streamed: a sequence of ``stream_chunk`` events (accumulated by messageId) ended
  by ``stream_end``. This is the normal LLM reply path.
- direct: a single ``agent_response`` event carrying the full text. This is used
  for canned replies (e.g. a Prompt-Guard block) and the connect greeting.

The adapter assembles either into one normalized reply. Each agent is namespaced
in nginx behind ``/ws/<agent_id>/socket.io/``, so ``socketio_path`` is per-agent.

Session memory lives server-side keyed by the connection, so ``history`` is not
resent; ``reset`` drops the connection and starts a fresh session (the server
clears its per-session cache on disconnect).
"""

from __future__ import annotations

import asyncio
import time

import socketio

from .base import AgentAdapter, AgentReply


class SocketIOAdapter(AgentAdapter):
    def __init__(
        self,
        name: str,
        url: str,
        agent_id: str,
        *,
        socketio_path: str = "socket.io",
        source: str = "benchmark",
        send_event: str = "message",
        timeout_s: float = 45.0,
    ):
        super().__init__(name=name)
        self.url = url
        self.agent_id = agent_id
        self.socketio_path = socketio_path
        self.source = source
        self.send_event = send_event
        self.timeout_s = timeout_s

        # Bounded auto-reconnect so a transient drop mid-grade recovers instead of turning
        # into a dead connection (the per-send timeout is still the hard backstop).
        self._sio = socketio.AsyncClient(reconnection=True, reconnection_attempts=3, reconnection_delay=0.5)
        self._session_id: str | None = None
        self._fut: asyncio.Future | None = None
        self._chunks: dict[str, list[str]] = {}
        self._counter = 0

        @self._sio.on("agent_response")
        async def _on_response(data):
            # A complete, non-streamed reply (or the greeting, which we suppress).
            self._resolve(data.get("content", "") if isinstance(data, dict) else str(data))

        @self._sio.on("stream_chunk")
        async def _on_chunk(data):
            if self._fut is None or self._fut.done():
                return
            mid = data.get("messageId", "_")
            self._chunks.setdefault(mid, []).append(data.get("chunk", ""))

        @self._sio.on("stream_end")
        async def _on_end(data):
            mid = data.get("messageId", "_")
            self._resolve("".join(self._chunks.pop(mid, [])))

    def _resolve(self, text: str) -> None:
        if self._fut is not None and not self._fut.done():
            self._fut.set_result(text)

    def _new_session(self) -> None:
        self._counter += 1
        self._session_id = f"pg-{id(self)}-{self._counter}"

    async def _ensure_connected(self) -> None:
        if self._sio.connected:
            return
        if self._session_id is None:
            self._new_session()
        query = (
            f"agentId={self.agent_id}&sessionId={self._session_id}"
            f"&source={self.source}&skipGreeting=true"
        )
        await self._sio.connect(
            f"{self.url}?{query}",
            socketio_path=self.socketio_path,
            transports=["websocket"],
            wait_timeout=min(self.timeout_s, 30.0),
        )

    async def send(self, history, message) -> AgentReply:
        start = time.perf_counter()
        try:
            await self._ensure_connected()
            self._fut = asyncio.get_running_loop().create_future()
            await self._sio.emit(self.send_event, {
                "content": message,
                "metadata": {"inputMode": "text"},
                "agentId": self.agent_id,
                "sessionId": self._session_id,
            })
            text = await asyncio.wait_for(self._fut, timeout=self.timeout_s)
            return AgentReply(str(text), (time.perf_counter() - start) * 1000.0)
        except asyncio.TimeoutError:
            return AgentReply("", (time.perf_counter() - start) * 1000.0, error="timeout_no_agent_response")
        except Exception as e:
            return AgentReply("", (time.perf_counter() - start) * 1000.0, error=f"{type(e).__name__}: {e}")
        finally:
            self._fut = None
            self._chunks.clear()

    async def reset(self) -> None:
        if self._sio.connected:
            await self._sio.disconnect()
        self._chunks.clear()
        self._new_session()

    async def aclose(self) -> None:
        if self._sio.connected:
            await self._sio.disconnect()


def aivonic_socketio_adapter(
    name: str,
    agent_id: str,
    *,
    base_url: str = "https://agents.aivonic.ai",
    **kwargs,
) -> SocketIOAdapter:
    """Preset for our own widget agents. Derives the per-agent nginx path
    ``/ws/<agent_id>/socket.io/`` unless a ``socketio_path`` is passed explicitly."""
    kwargs.setdefault("socketio_path", f"ws/{agent_id}/socket.io")
    return SocketIOAdapter(name, base_url, agent_id, **kwargs)
