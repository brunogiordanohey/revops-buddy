"""MeetingBaas PCM audio and participant metadata serialization."""
import json
import time
from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame
from pipecat.serializers.base_serializer import FrameSerializer

class MeetingBaasFrameSerializer(FrameSerializer):
    """Raw mono PCM with admission and participant metadata on text frames."""

    def __init__(self, sample_rate: int = 16000):
        super().__init__()
        self.sample_rate = sample_rate
        self.recording_started = False
        self.active_speakers: list[str] = []
        self.recent_speakers: dict[str, float] = {}

    def speaker_for_utterance(self, window_seconds: float = 6.0) -> str | None:
        """Attribute delayed STT commits unless recent speakers are ambiguous."""
        now = time.monotonic()
        recent = sorted(
            ((seen, name) for name, seen in self.recent_speakers.items()
             if now - seen <= window_seconds),
            reverse=True,
        )
        if not recent or (len(recent) > 1 and recent[0][0] - recent[1][0] <= 0.5):
            return None
        return recent[0][1]

    async def deserialize(self, data: str | bytes):
        if isinstance(data, bytes):
            return InputAudioRawFrame(audio=data, sample_rate=self.sample_rate, num_channels=1)
        try:
            message = json.loads(data)
        except (ValueError, TypeError, RecursionError):
            return None
        if isinstance(message, dict) and "protocol_version" in message:
            if message.get("start_time") is not None:
                self.recording_started = True
        elif isinstance(message, list):
            self.active_speakers = [p["name"] for p in message if isinstance(p, dict)
                and isinstance(p.get("name"), str) and p.get("isSpeaking") is True and not p.get("isSelf")]
            now = time.monotonic()
            for name in self.active_speakers:
                self.recent_speakers[name] = now
        return None

    async def serialize(self, frame: Frame):
        if self.recording_started and isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        return None


__all__ = ["MeetingBaasFrameSerializer"]
