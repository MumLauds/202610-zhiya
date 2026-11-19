"""Environment-only configuration for DashScope speech calls."""

import math
import os
from dataclasses import dataclass, field

from app.config import DASHSCOPE_CHAT_URL, validate_key, validate_text, validate_url
from app.speech_options import DEFAULT_TTS_MODEL, MODEL_VOICES


@dataclass(frozen=True)
class SpeechSettings:
    api_key: str = field(default="", repr=False)
    asr_api_url: str = DASHSCOPE_CHAT_URL
    asr_model: str = "qwen3-asr-flash"
    tts_api_url: str = ""
    tts_model: str = DEFAULT_TTS_MODEL
    tts_voice: str = "wenhuaiqing_v3.1"
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        validate_key(self.api_key, "DASHSCOPE_API_KEY")
        for name in ("asr_api_url", "tts_api_url"):
            validate_url(getattr(self, name), name.upper())
        for name in ("asr_model", "tts_model", "tts_voice"):
            validate_text(getattr(self, name), name.upper())
        if self.tts_model and self.tts_model not in MODEL_VOICES:
            raise ValueError("TTS_MODEL must be a supported model or empty to disable TTS")
        if self.tts_model and self.tts_voice and self.tts_voice not in tuple(voice["voiceId"] for voice in MODEL_VOICES[self.tts_model]):
            raise ValueError("TTS_VOICE must be a supported voice or empty to disable TTS")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 20:
            raise ValueError("VOICE_TIMEOUT_SECONDS must be greater than 0 and at most 20")

    @classmethod
    def from_env(cls) -> "SpeechSettings":
        try:
            timeout = float(os.getenv("VOICE_TIMEOUT_SECONDS", "20"))
        except ValueError:
            raise ValueError("VOICE_TIMEOUT_SECONDS must be a valid number") from None
        defaults = cls()
        return cls(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            **{name: os.getenv(name.upper(), getattr(defaults, name))
               for name in ("asr_api_url", "tts_api_url")},
            **{name: os.getenv(name.upper(), getattr(defaults, name)).strip()
               for name in ("asr_model", "tts_model", "tts_voice")},
            timeout_seconds=timeout,
        )

    @property
    def asr_configured(self) -> bool:
        return bool(self.api_key and self.asr_api_url and self.asr_model.strip())

    @property
    def tts_configured(self) -> bool:
        return bool(self.api_key and self.tts_api_url and self.tts_model.strip() and self.tts_voice.strip())
