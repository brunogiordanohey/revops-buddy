"""Regression coverage for delayed speaker commits and conversational timing."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import BotStoppedSpeakingFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection

from revops_buddy.conversation import ActivationGate
from revops_buddy.voice import _user_speaker
from revops_buddy.voice.protocol import MeetingBaasFrameSerializer
from test_voice_runtime import PassthroughTTS, _send_audio, _start_session


async def participants(serializer, *people):
    await serializer.deserialize(json.dumps(list(people)))


@pytest.mark.asyncio
async def test_recent_speaker_survives_stopping_and_expires(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("revops_buddy.voice.protocol.time.monotonic", lambda: now[0])
    serializer = MeetingBaasFrameSerializer()
    await participants(serializer, {"name": "Casey", "isSpeaking": True})
    now[0] = 11.0
    await participants(serializer, {"name": "Casey", "isSpeaking": False})
    assert serializer.active_speakers == []
    assert serializer.recent_speakers == {"Casey": 10.0}
    assert serializer.speaker_for_utterance() == "Casey"
    assert _user_speaker(serializer) == "Casey"
    assert serializer.speaker_for_utterance(window_seconds=0.5) is None
    now[0] = 16.0
    assert serializer.speaker_for_utterance() == "Casey"
    now[0] = 16.01
    assert serializer.speaker_for_utterance() is None
    assert _user_speaker(serializer) == "user"


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", [0.0, 0.4, 0.5, 0.6])
async def test_recent_speakers_reject_overlap_and_choose_latest(monkeypatch, gap):
    now = [10.0]
    monkeypatch.setattr("revops_buddy.voice.protocol.time.monotonic", lambda: now[0])
    serializer = MeetingBaasFrameSerializer()
    await participants(serializer, {"name": "Casey", "isSpeaking": True})
    now[0] += gap
    await participants(serializer, {"name": "Alex", "isSpeaking": True})
    await participants(serializer)
    assert serializer.speaker_for_utterance() == ("Alex" if gap > 0.5 else None)


@pytest.mark.asyncio
async def test_recent_speakers_ignore_self_and_refresh_timestamp(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("revops_buddy.voice.protocol.time.monotonic", lambda: now[0])
    serializer = MeetingBaasFrameSerializer()
    await participants(serializer, {"name": "Angels", "isSpeaking": True, "isSelf": True})
    assert serializer.recent_speakers == {}
    assert serializer.speaker_for_utterance() is None
    for stamp in (10.0, 15.0):
        now[0] = stamp
        await participants(serializer, {"name": "Casey", "isSpeaking": True, "isSelf": False})
    now[0] = 20.0
    assert serializer.recent_speakers == {"Casey": 15.0}
    assert serializer.speaker_for_utterance() == "Casey"


def test_user_speaker_prefers_recent_then_active_then_generic():
    assert _user_speaker(SimpleNamespace(
        speaker_for_utterance=lambda: "Casey", active_speakers=["Alex"],
    )) == "Casey"
    assert _user_speaker(SimpleNamespace(
        speaker_for_utterance=lambda: None, active_speakers=["Alex"],
    )) == "Alex"
    assert _user_speaker(SimpleNamespace(active_speakers=["Alex"])) == "Alex"
    assert _user_speaker(SimpleNamespace(active_speakers=["Alex", "Casey"])) == "user"
    assert _user_speaker(object()) == "user"


@pytest.mark.parametrize("extend", [False, True])
def test_followup_window_starts_at_end_of_answer(extend):
    now = [0.0]
    gate = ActivationGate(clock=lambda: now[0])
    assert gate.observe("Angels, show the deal").activated
    now[0] = 12.0
    if extend:
        gate.extend_followup()
    now[0] = 16.0
    assert gate.observe("And when does it close?").activated is extend


def test_extended_window_still_requires_explicit_followup_and_expires():
    now = [0.0]
    gate = ActivationGate(clock=lambda: now[0])
    assert gate.observe("Angels, show the deal").activated
    now[0] = 12.0
    gate.extend_followup()
    now[0] = 14.0
    assert not gate.observe("The team is discussing lunch").activated
    now[0] = 20.01
    assert not gate.observe("And when does it close?").activated


@pytest.mark.asyncio
async def test_voice_runtime_extends_followup_on_bot_stopped(monkeypatch):
    extended = asyncio.Event()
    now = [0.0]
    gate = ActivationGate(clock=lambda: now[0])
    original_extend = gate.extend_followup

    def extend():
        original_extend()
        extended.set()

    monkeypatch.setattr(gate, "extend_followup", extend)
    monkeypatch.setattr("revops_buddy.voice.ActivationGate", lambda **kwargs: gate)
    original_process = PassthroughTTS.process_frame

    async def process(self, frame, direction):
        await original_process(self, frame, direction)
        if isinstance(frame, TTSSpeakFrame):
            now[0] = 12.0
            await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)

    monkeypatch.setattr(PassthroughTTS, "process_frame", process)

    async def stream(text, context):
        yield "The deal is open."

    incoming, _, _, _, task = await _start_session(monkeypatch, stream=stream)
    try:
        await _send_audio(incoming)
        await asyncio.wait_for(extended.wait(), timeout=3)
        now[0] = 16.0
        assert gate.observe("And when does it close?").activated
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("override, expected", [(None, 1.6), (0.3, 0.3)])
async def test_progress_ack_default_and_setting_override(monkeypatch, override, expected):
    observed = asyncio.Event()
    timeouts = []
    original_wait = asyncio.wait

    async def wait(fs, *, timeout=None, **kwargs):
        if timeout is not None:
            timeouts.append(timeout)
            observed.set()
        return await original_wait(fs, timeout=timeout, **kwargs)

    monkeypatch.setattr("revops_buddy.voice.asyncio.wait", wait)

    async def stream(text, context):
        await observed.wait()
        yield "The deal is open."

    settings = {} if override is None else {"progress_ack_delay_seconds": override}
    incoming, _, _, _, task = await _start_session(monkeypatch, stream=stream, settings_extra=settings)
    try:
        await _send_audio(incoming)
        await asyncio.wait_for(observed.wait(), timeout=3)
        assert timeouts == [expected]
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=10)


def test_stop_command_is_not_reopened_by_bot_stopping():
    from revops_buddy.conversation import ActivationGate
    now = [0.0]
    gate = ActivationGate(clock=lambda: now[0])
    assert gate.observe("Angels, what's our open pipeline?").activated
    now[0] = 3.0
    gate.observe("Angels, stop.")          # closes the window
    gate.extend_followup()                 # bot stops speaking after the interruption
    now[0] = 5.0
    assert not gate.observe("What about that?").activated
