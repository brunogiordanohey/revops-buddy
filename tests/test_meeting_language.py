"""Angels must speak the meeting language even when transcription mislabels a line."""

from __future__ import annotations

from revops_buddy.config import Settings
from revops_buddy.conversation import AnswerService


class _RevOps:
    def tool_definitions(self):
        return []

    def call_tool(self, name, arguments):
        return {}


def test_stt_language_is_pinned_outside_grounded_demo(monkeypatch):
    monkeypatch.setenv("ANGELS_GROUNDED_DEMO", "false")
    monkeypatch.delenv("MEETING_LANGUAGE", raising=False)
    assert Settings.from_env({}).voice_settings({})["stt_language"] == "en"


def test_meeting_language_is_configurable(monkeypatch):
    settings = Settings.from_env({"MEETING_LANGUAGE": "es"})
    assert settings.voice_settings({})["stt_language"] == "es"
    assert settings.voice_settings({})["meeting_language"] == "es"


def test_prompt_pins_answer_language_regardless_of_context():
    service = AnswerService(_RevOps(), {"anthropic_api_key": "k", "anthropic_model": "m", "meeting_language": "en"})
    system = service._request_kwargs([{"role": "user", "content": "x"}])["system"]
    assert "Always answer in English" in system
    service = AnswerService(_RevOps(), {"anthropic_api_key": "k", "anthropic_model": "m", "meeting_language": "es"})
    assert "Always answer in Spanish" in service._request_kwargs([{"role": "user", "content": "x"}])["system"]


def test_spanish_meetings_use_the_spanish_voice_when_configured():
    from dataclasses import replace

    env = {"TTS_VOICE_ID": "en-voice", "TTS_VOICE_ID_ES": "es-voice"}
    settings = Settings.from_env(env)
    assert settings.voice_settings({})["tts_voice_id"] == "en-voice"
    assert replace(settings, meeting_language="es").voice_settings({})["tts_voice_id"] == "es-voice"
    assert replace(settings, meeting_language="it").voice_settings({})["tts_voice_id"] == "en-voice"
    fallback = replace(Settings.from_env({"TTS_VOICE_ID": "en-voice"}), meeting_language="es")
    assert fallback.voice_settings({})["tts_voice_id"] == "en-voice"
