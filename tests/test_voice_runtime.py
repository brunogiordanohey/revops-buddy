"""Exercise real Pipecat/WebSocket plumbing with offline speech-provider doubles."""
import asyncio
import base64
import json

import pytest
from starlette.websockets import WebSocket
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame, TranscriptionFrame, TTSSpeakFrame, InterimTranscriptionFrame
from pipecat.processors.frame_processor import FrameProcessor

from revops_buddy.voice import run_voice_session


@pytest.mark.asyncio
@pytest.mark.parametrize("stt_provider", ["deepgram", "elevenlabs"])
@pytest.mark.parametrize("interrupt", [False, True])
async def test_addressed_audio_reaches_answer_and_returns_pcm(monkeypatch, stt_provider, interrupt):
    import pipecat.services.deepgram.stt as deepgram
    import pipecat.services.elevenlabs.tts as elevenlabs
    import pipecat.services.elevenlabs.stt as elevenlabs_stt

    class OfflineSTT(FrameProcessor):
        Settings = elevenlabs_stt.ElevenLabsRealtimeSTTService.Settings
        def __init__(self, **kwargs):
            super().__init__()
            self.received_audio = 0

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                self.received_audio += 1
                if self.received_audio > 1:
                    await self.push_frame(InterimTranscriptionFrame(text="Angel, stop.", user_id="offline", timestamp=""), direction)
                    await self.push_frame(TranscriptionFrame(text="Angel, stop.", user_id="offline", timestamp="", finalized=True), direction)
                    return
                await self.push_frame(TranscriptionFrame(
                    text="Angels, say hello", user_id="offline", timestamp="", finalized=True,
                ), direction)
            else:
                await self.push_frame(frame, direction)

    class OfflineTTS(FrameProcessor):
        def __init__(self, **kwargs):
            super().__init__()

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSSpeakFrame):
                await self.push_frame(OutputAudioRawFrame(
                    audio=b"\x01\x00" * 1600, sample_rate=16000, num_channels=1,
                ), direction)
            else:
                await self.push_frame(frame, direction)

    monkeypatch.setattr(deepgram, "DeepgramSTTService", OfflineSTT)
    monkeypatch.setattr(elevenlabs_stt, "ElevenLabsRealtimeSTTService", OfflineSTT)
    monkeypatch.setattr(elevenlabs, "ElevenLabsTTSService", OfflineTTS)
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    socket = WebSocket({"type": "websocket", "path": "/audio", "query_string": b"", "headers": []}, incoming.get, outgoing.put)
    await incoming.put({"type": "websocket.connect"})
    await socket.accept()
    await outgoing.get()
    transcript, requests = [], []
    stop_recorded = asyncio.Event()

    async def record(speaker, text):
        transcript.append((speaker, text))
        if text == "Angel, stop.":
            stop_recorded.set()

    async def answer(text, context):
        requests.append((text, context))
        return "Hello from Angels."

    cancelled = asyncio.Event()
    async def stream_answer(text, context):
        try:
            yield await answer(text, context)
            await asyncio.Future()
        finally:
            cancelled.set()

    task = asyncio.create_task(run_voice_session(
        socket, session_id="offline", expected_bot_id="offline-bot",
        settings={"api_keys": {"deepgram": "offline", "elevenlabs": "offline"}, "tts_voice_id": "offline", "stt_provider": stt_provider},
        on_transcript=record, answer=answer,
        answer_stream=stream_answer if interrupt else None,
    ))
    try:
        await _send_audio(incoming)
        response = await asyncio.wait_for(outgoing.get(), timeout=10)
        assert response["type"] == "websocket.send"
        assert response["bytes"]
        assert requests[0][0] == "say hello"
        assert transcript == [("user", "Angels, say hello"), ("Angels", "Hello from Angels.")]
        if interrupt:
            await _send_audio(incoming)
            await asyncio.wait_for(cancelled.wait(), timeout=3)
            await asyncio.wait_for(stop_recorded.wait(), timeout=3)
            assert len(requests) == 1
            assert len([entry for entry in transcript if entry[0] == "Angels"]) == 1
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=10)


ACK_PHRASES = ("One moment.", "Sure, one second.")
_STT_SCRIPT = ("Angels, say hello",)


class ScriptedSTT(FrameProcessor):
    """Emits one transcription per audio frame from a fixed script; last entry repeats."""

    def __init__(self, script=None, **kwargs):
        super().__init__()
        self.script = list(script if script is not None else _STT_SCRIPT)
        self.count = 0

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if not isinstance(frame, InputAudioRawFrame):
            await self.push_frame(frame, direction)
            return
        self.count += 1
        text = self.script[min(self.count - 1, len(self.script) - 1)]
        if "stop" in text:
            await self.push_frame(InterimTranscriptionFrame(text=text, user_id="offline", timestamp=""), direction)
        await self.push_frame(TranscriptionFrame(text=text, user_id="offline", timestamp="", finalized=True), direction)


class PassthroughTTS(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


def _patch_providers(monkeypatch):
    import pipecat.services.deepgram.stt as deepgram
    import pipecat.services.elevenlabs.stt as elevenlabs_stt
    import pipecat.services.elevenlabs.tts as elevenlabs
    monkeypatch.setattr(deepgram, "DeepgramSTTService", ScriptedSTT)
    monkeypatch.setattr(elevenlabs_stt, "ElevenLabsRealtimeSTTService", ScriptedSTT)
    monkeypatch.setattr(elevenlabs, "ElevenLabsTTSService", PassthroughTTS)


async def _send_audio(incoming):
    await incoming.put({"type": "websocket.receive", "text": json.dumps({"protocol_version": 1, "start_time": 0})})
    await incoming.put({"type": "websocket.receive", "bytes": b"\x01\x00" * 1600})


async def _start_session(monkeypatch, *, stream=None, answer=None, script=("Angels, say hello",), settings_extra=None):
    global _STT_SCRIPT
    _STT_SCRIPT = tuple(script)
    _patch_providers(monkeypatch)
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    socket = WebSocket({"type": "websocket", "path": "/audio", "query_string": b"", "headers": []}, incoming.get, outgoing.put)
    await incoming.put({"type": "websocket.connect"})
    await socket.accept()
    await outgoing.get()
    transcript, requests = [], []

    async def record(speaker, text):
        transcript.append((speaker, text))

    settings = {
        "api_keys": {"deepgram": "offline", "elevenlabs": "offline"},
        "tts_voice_id": "offline",
        "stt_provider": "deepgram",
    }
    settings.update(settings_extra or {})
    task = asyncio.create_task(run_voice_session(
        socket, session_id="offline", expected_bot_id="offline-bot",
        settings=settings, on_transcript=record, answer=answer or (lambda t, c: _unreached()), answer_stream=stream,
    ))
    return incoming, outgoing, transcript, requests, task


def _unreached():
    raise AssertionError("answer called unexpectedly")


@pytest.mark.asyncio
async def test_http_only_tts_model_uses_http_service_and_closes_session(monkeypatch):
    import pipecat.services.elevenlabs.tts as elevenlabs
    import aiohttp

    recorded = {}
    closed = asyncio.Event()

    class RecordingHttpTTS(FrameProcessor):
        Settings = elevenlabs.ElevenLabsHttpTTSService.Settings
        def __init__(self, **kwargs):
            super().__init__()
            recorded.update(kwargs)
            settings = kwargs.get("settings")
            recorded["settings_voice"] = getattr(settings, "voice", None)
            recorded["settings_model"] = getattr(settings, "model", None)

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSSpeakFrame):
                await self.push_frame(OutputAudioRawFrame(
                    audio=b"\x01\x00" * 1600, sample_rate=16000, num_channels=1,
                ), direction)
            else:
                await self.push_frame(frame, direction)

    class FakeSession:
        async def close(self):
            closed.set()

    monkeypatch.setattr(elevenlabs, "ElevenLabsHttpTTSService", RecordingHttpTTS)
    monkeypatch.setattr(aiohttp, "ClientSession", lambda: FakeSession())

    async def stream(text, context):
        yield "Fast answer."

    incoming, outgoing, transcript, requests, task = await _start_session(
        monkeypatch, stream=stream, settings_extra={"tts_model": "eleven_v4_turbo", "tts_transport": "http"},
    )
    try:
        await _send_audio(incoming)
        await asyncio.sleep(0.25)
        assert recorded["api_key"] == "offline"
        assert recorded["sample_rate"] == 16000
        assert recorded["settings_voice"] == "offline"
        assert recorded["settings_model"] == "eleven_v4_turbo"
        assert isinstance(recorded["aiohttp_session"], FakeSession)
        assert transcript == [("user", "Angels, say hello"), ("Angels", "Fast answer.")]
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=10)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_default_tts_model_still_uses_websocket_service(monkeypatch):
    import pipecat.services.elevenlabs.tts as elevenlabs

    calls = []

    def fake_http_service(*args, **kwargs):
        calls.append(kwargs)
        raise AssertionError("ElevenLabsHttpTTSService must not be used for default model")

    monkeypatch.setattr(elevenlabs, "ElevenLabsHttpTTSService", fake_http_service)
    async def stream(text, context):
        yield "Fast answer."

    incoming, outgoing, transcript, requests, task = await _start_session(monkeypatch, stream=stream)
    try:
        await _send_audio(incoming)
        await asyncio.sleep(0.25)
        assert transcript == [("user", "Angels, say hello"), ("Angels", "Fast answer.")]
        assert calls == []
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_fast_answer_emits_no_acknowledgment(monkeypatch):
    async def stream(text, context):
        yield "Fast answer."

    incoming, outgoing, transcript, requests, task = await _start_session(
        monkeypatch, stream=stream, settings_extra={"progress_ack_delay_seconds": 0.9},
    )
    try:
        await _send_audio(incoming)
        await asyncio.sleep(0.25)
        assert transcript == [("user", "Angels, say hello"), ("Angels", "Fast answer.")]
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_slow_first_chunk_emits_one_ack_before_answer(monkeypatch):
    seen_contexts = []
    async def stream(text, context):
        seen_contexts.append(list(context))
        await asyncio.sleep(0.35)
        yield "Slow answer."

    incoming, outgoing, transcript, requests, task = await _start_session(
        monkeypatch, stream=stream, settings_extra={"progress_ack_delay_seconds": 0.1},
    )
    try:
        await _send_audio(incoming)
        await asyncio.sleep(0.6)
        assert transcript == [
            ("user", "Angels, say hello"),
            ("Angels", ACK_PHRASES[0]),
            ("Angels", "Slow answer."),
        ]
        assert all(all(entry["speaker"] != "Angels" for entry in ctx) for ctx in seen_contexts)
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_streaming_after_first_chunk_emits_no_acknowledgment(monkeypatch):
    async def stream(text, context):
        yield "First part."
        await asyncio.sleep(0.5)
        yield "Second part."

    incoming, outgoing, transcript, requests, task = await _start_session(
        monkeypatch, stream=stream, settings_extra={"progress_ack_delay_seconds": 0.1},
    )
    try:
        await _send_audio(incoming)
        await asyncio.sleep(0.8)
        assert transcript == [
            ("user", "Angels, say hello"),
            ("Angels", "First part."),
            ("Angels", "Second part."),
        ]
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_stop_during_ack_wait_cancels_ack_and_stream(monkeypatch):
    cancelled = asyncio.Event()
    async def stream(text, context):
        try:
            await asyncio.sleep(10)
            yield "Never arrives."
        except asyncio.CancelledError:
            cancelled.set()
            raise

    incoming, outgoing, transcript, requests, task = await _start_session(
        monkeypatch, stream=stream, script=("Angels, say hello", "Angel, stop."),
        settings_extra={"progress_ack_delay_seconds": 0.1},
    )
    try:
        await _send_audio(incoming)
        await asyncio.sleep(0.05)
        await _send_audio(incoming)
        await asyncio.wait_for(cancelled.wait(), timeout=3)
        await asyncio.sleep(0.3)
        assert transcript == [("user", "Angels, say hello"), ("user", "Angel, stop.")]
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_disconnect_during_ack_wait_cancels_ack_and_stream(monkeypatch):
    cancelled = asyncio.Event()
    async def stream(text, context):
        try:
            await asyncio.sleep(10)
            yield "Never arrives."
        except asyncio.CancelledError:
            cancelled.set()
            raise

    incoming, outgoing, transcript, requests, task = await _start_session(
        monkeypatch, stream=stream, settings_extra={"progress_ack_delay_seconds": 0.1},
    )
    await _send_audio(incoming)
    await asyncio.sleep(0.05)
    await incoming.put({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(task, timeout=10)
    await asyncio.wait_for(cancelled.wait(), timeout=3)
    assert transcript == [("user", "Angels, say hello")]


@pytest.mark.asyncio
async def test_invalid_ack_delay_is_rejected(monkeypatch):
    for value in (-1, "soon"):
        with pytest.raises(ValueError):
            await run_voice_session(
                None, session_id="offline", expected_bot_id="offline-bot",
                settings={
                    "api_keys": {"deepgram": "offline", "elevenlabs": "offline"},
                    "tts_voice_id": "offline",
                    "stt_provider": "deepgram",
                    "progress_ack_delay_seconds": value,
                },
                on_transcript=lambda s, t: asyncio.sleep(0), answer=lambda t, c: asyncio.sleep(0),
            )
