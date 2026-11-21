"""MeetingBaas REST adapter and paired audio websocket transport."""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx
from starlette.websockets import WebSocketState


class MeetingBaasError(RuntimeError):
    """An upstream failure containing no request or response secrets."""

    def __init__(self, status_code: int, reason: str):
        self.status_code = status_code
        super().__init__(f"MeetingBaas HTTP {status_code}: {reason}")


class MeetingBaasClient:
    def __init__(self, api_key: str | None, base_url: str = "https://api.meetingbaas.com", *, http_client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=10)

    async def aclose(self):
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        if not self.api_key:
            raise MeetingBaasError(503, "API key is not configured")
        try:
            response = await self._client.request(method, self.base_url + path,
                headers={"x-meeting-baas-api-key": self.api_key}, **kwargs)
        except httpx.HTTPError:
            raise MeetingBaasError(502, "upstream unavailable") from None
        if response.is_error:
            # Use the standard reason, never upstream-controlled headers or bodies.
            raise MeetingBaasError(response.status_code, httpx.codes.get_reason_phrase(response.status_code))
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise MeetingBaasError(502, "invalid JSON") from None

    async def create_bot(self, meeting_url: str, bot_name: str, output_url: str, input_url: str, extra: dict, waiting_room_timeout: int | None = None, callback_config: dict | None = None) -> str:
        result = await self._request("POST", "/v2/bots", json={
            **({"callback_config": callback_config} if callback_config is not None else {}),
            "meeting_url": meeting_url, "bot_name": bot_name, "extra": extra,
            "streaming_enabled": True,
            "streaming_config": {"mode": "audio", "output_url": output_url, "input_url": input_url, "audio_frequency": 16000},
            "timeout_config": {"waiting_room_timeout": waiting_room_timeout if waiting_room_timeout is not None else 600, "no_one_joined_timeout": 600},
        })
        data = result.get("data") if isinstance(result, dict) else None
        bot_id = data.get("bot_id") if isinstance(data, dict) else None
        if not isinstance(bot_id, str) or not bot_id:
            raise MeetingBaasError(502, "missing bot id")
        return bot_id

    async def leave(self, bot_id: str):
        await self._request("POST", f"/v2/bots/{quote(bot_id, safe='')}/leave")

    async def status(self, bot_id: str) -> str:
        result = await self._request("GET", f"/v2/bots/{quote(bot_id, safe='')}/status")
        data = result.get("data") if isinstance(result, dict) else None
        status = data.get("status") if isinstance(data, dict) else None
        if not isinstance(status, str):
            raise MeetingBaasError(502, "missing status")
        return status


class DuplexWebSocket:
    """The subset of Starlette's websocket API used by Pipecat."""

    def __init__(self, receive_from, send_to):
        self.receive_from = receive_from
        self.send_to = send_to
        self._closed = False

    def _state(self, name):
        if not self._closed and all(getattr(ws, name) == WebSocketState.CONNECTED for ws in (self.receive_from, self.send_to)):
            return WebSocketState.CONNECTED
        return WebSocketState.DISCONNECTED

    @property
    def client_state(self):
        return self._state("client_state")

    @property
    def application_state(self):
        return self._state("application_state")

    async def receive(self):
        return await self.receive_from.receive()

    async def send_bytes(self, data):
        await self.send_to.send_bytes(data)

    async def send_text(self, data):
        await self.send_to.send_text(data)

    async def close(self, code=1000, reason=None):
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(*(ws.close(code=code, reason=reason) for ws in (self.receive_from, self.send_to)), return_exceptions=True)
