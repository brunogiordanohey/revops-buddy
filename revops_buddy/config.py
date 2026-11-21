"""Environment settings for a single-company voice agent."""
from dataclasses import dataclass, fields
import json
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    api_token: str | None = None
    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = "sqlite:///revops_buddy.db"
    data_mode: str = "synthetic"
    public_base_url: str | None = None
    bot_name: str = "Angels"
    meeting_language: str = "en"
    presync_max_age_seconds: int = 300
    mb_join_retries: int = 1
    meetingbaas_api_key: str | None = None
    meetingbaas_api_base_url: str = "https://api.meetingbaas.com"
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    anthropic_effort: str = "low"
    deepgram_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    tts_voice_id: str | None = None
    tts_voice_id_es: str | None = None
    tts_model: str = "eleven_flash_v2_5"
    tts_transport: str = "dialogue"
    tts_stability: float = 0.4
    tts_similarity: float = 0.75
    tts_text_normalization: str = "auto"
    hubspot_access_token: str | None = None
    company_config_path: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        if env is None:
            from dotenv import load_dotenv
            load_dotenv()
        source = os.environ if env is None else env
        values = {}
        for field in fields(cls):
            raw = source.get(field.name.upper(), "").strip()
            if raw:
                values[field.name] = (int(raw) if field.type is int else float(raw) if field.type is float else raw)
        settings = cls(**values)
        if settings.data_mode not in {"live", "synthetic"}:
            raise ValueError("DATA_MODE must be live or synthetic")
        if settings.meeting_language not in {"en", "es"}:
            raise ValueError("MEETING_LANGUAGE must be en or es")
        if settings.tts_transport not in {"dialogue", "http"}:
            raise ValueError("TTS_TRANSPORT must be dialogue or http")
        if settings.tts_text_normalization not in {"auto", "on", "off"}:
            raise ValueError("Invalid TTS_TEXT_NORMALIZATION")
        if not all(0 <= value <= 1 for value in (settings.tts_stability, settings.tts_similarity)):
            raise ValueError("TTS controls must be between zero and one")
        if not 1 <= settings.port <= 65535 or settings.presync_max_age_seconds < 0 or settings.mb_join_retries not in {0, 1}:
            raise ValueError("Invalid port, sync age, or join retry count")
        return settings

    @property
    def public_wss_base(self) -> str:
        base = (self.public_base_url or "").rstrip("/")
        parsed = urlsplit(base)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("PUBLIC_BASE_URL must be a public HTTPS origin")
        return "wss://" + parsed.netloc

    def company_config(self) -> dict:
        path = (Path(__file__).parent / "fixtures/company.json" if self.data_mode == "synthetic"
                else Path(self.company_config_path) if self.company_config_path else None)
        if path is None:
            raise ValueError("COMPANY_CONFIG_PATH is required in live mode")
        payload = json.loads(path.read_text())
        return {**payload.get("company_config", payload), "data_mode": self.data_mode}

    def voice_settings(self, company_config: dict | None = None) -> dict:
        return {
            "anthropic_api_key": self.anthropic_api_key, "anthropic_model": self.anthropic_model,
            "anthropic_effort": self.anthropic_effort, "bot_name": self.bot_name,
            "meeting_language": self.meeting_language, "stt_language": self.meeting_language,
            "stt_provider": "deepgram", "tts_provider": "elevenlabs", "sample_rate": 16000,
            "transport_protocol": "meetingbaas", "company_config": company_config or {},
            "tts_voice_id": self.tts_voice_id_es if self.meeting_language == "es" and self.tts_voice_id_es else self.tts_voice_id,
            "tts_model": self.tts_model, "tts_transport": self.tts_transport,
            "tts_stability": self.tts_stability, "tts_similarity": self.tts_similarity,
            "tts_text_normalization": self.tts_text_normalization,
            "api_keys": {"deepgram": self.deepgram_api_key, "elevenlabs": self.elevenlabs_api_key},
        }

    def providers(self) -> dict[str, bool]:
        return {"meetingbaas": bool(self.meetingbaas_api_key),
                "anthropic": bool(self.anthropic_api_key and self.anthropic_model),
                "deepgram": bool(self.deepgram_api_key),
                "elevenlabs": bool(self.elevenlabs_api_key and self.tts_voice_id),
                "hubspot": bool(self.hubspot_access_token)}
