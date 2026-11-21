import asyncio


import json


from types import SimpleNamespace


from urllib.parse import parse_qs, urlsplit


import httpx


import pytest


from fastapi.testclient import TestClient


from starlette.websockets import WebSocketDisconnect, WebSocketState


from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame, EndFrame


from revops_buddy.config import Settings


from revops_buddy.meetingbaas import MeetingBaasClient, MeetingBaasError, DuplexWebSocket





from revops_buddy.voice.protocol import MeetingBaasFrameSerializer


@pytest.mark.asyncio
async def test_client_requests_and_errors():
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/status'):
            return httpx.Response(200, json={'data': {'status': 'in_call_recording'}})
        return httpx.Response(200, json={'success': True, 'data': {'bot_id': 'bot'}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = MeetingBaasClient('private-key', 'https://example.test/', http_client=http)
        assert await client.create_bot('https://meet.google.com/abc-defg-hij', 'Angels', 'wss://host/out', 'wss://host/in', {'id': 'session'}) == 'bot'
        request = requests[0]
        assert str(request.url) == 'https://example.test/v2/bots'
        assert request.headers['x-meeting-baas-api-key'] == 'private-key'
        payload = json.loads(request.content)
        assert payload == {
            'meeting_url': 'https://meet.google.com/abc-defg-hij', 'bot_name': 'Angels', 'extra': {'id': 'session'},
            'streaming_enabled': True, 'streaming_config': {'mode': 'audio', 'output_url': 'wss://host/out', 'input_url': 'wss://host/in', 'audio_frequency': 16000},
            'timeout_config': {'waiting_room_timeout': 600, 'no_one_joined_timeout': 600},
        }
        await client.leave('bot')
        assert requests[-1].method == 'POST'
        assert requests[-1].url.path == '/v2/bots/bot/leave'
        assert await client.status('bot') == 'in_call_recording'
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, text='private-key body-secret'))) as http:
        with pytest.raises(MeetingBaasError) as error:
            await MeetingBaasClient('private-key', http_client=http).status('bot')
        assert '403' in str(error.value)
        assert 'private-key' not in str(error.value)
        assert 'body-secret' not in str(error.value)


@pytest.mark.asyncio
async def test_serializer_metadata_and_recording_gate():
    serializer = MeetingBaasFrameSerializer()
    audio = OutputAudioRawFrame(audio=b'\x01\x00' * 320, sample_rate=16000, num_channels=1)
    assert await serializer.serialize(audio) is None
    assert await serializer.deserialize(json.dumps({'protocol_version': 1, 'start_time': None})) is None
    assert not serializer.recording_started
    assert await serializer.serialize(audio) is None
    await serializer.deserialize(json.dumps({'protocol_version': 1, 'start_time': 0}))
    assert serializer.recording_started
    assert await serializer.serialize(audio) == audio.audio
    frame = await serializer.deserialize(audio.audio)
    assert isinstance(frame, InputAudioRawFrame)
    assert (frame.audio, frame.sample_rate, frame.num_channels) == (audio.audio, 16000, 1)
    await serializer.deserialize(json.dumps([
        {'name': 'Casey', 'isSpeaking': True, 'isSelf': False},
        {'name': 'Angels', 'isSpeaking': True, 'isSelf': True},
        {'name': 'Quiet', 'isSpeaking': False}, None,
    ]))
    assert serializer.active_speakers == ['Casey']
    for text in ('not json', '{}', 'null', '42', '[null]'):
        assert await serializer.deserialize(text) is None
    assert await serializer.serialize(EndFrame()) is None


class Socket:
    def __init__(self):
        self.client_state = self.application_state = WebSocketState.CONNECTED
        self.sent = []
        self.closed = []
    async def receive(self):
        return {'type': 'websocket.receive', 'bytes': b'input'}
    async def send_bytes(self, value): self.sent.append(value)
    async def send_text(self, value): self.sent.append(value)
    async def close(self, code=1000, reason=None):
        self.closed.append(code)
        self.application_state = WebSocketState.DISCONNECTED


@pytest.mark.asyncio
async def test_duplex_routes_and_closes_both_once():
    a, b = Socket(), Socket()
    ws = DuplexWebSocket(a, b)
    assert ws.client_state == ws.application_state == WebSocketState.CONNECTED
    assert (await ws.receive())['bytes'] == b'input'
    await ws.send_bytes(b'output')
    await ws.send_text('text')
    assert a.sent == [] and b.sent == [b'output', 'text']
    b.client_state = WebSocketState.DISCONNECTED
    assert ws.client_state == WebSocketState.DISCONNECTED
    await ws.close(code=1011)
    await ws.close()
    assert a.closed == b.closed == [1011]
    assert ws.application_state == WebSocketState.DISCONNECTED


