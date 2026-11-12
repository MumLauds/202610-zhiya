"""G08 voice and model-generated speech-rate choices; no player time stretching."""

import json
from copy import deepcopy
from pathlib import Path

from app.errors import ServiceError

DEFAULT_TTS_MODEL = "qwen-audio-3.1-tts-flash"
MODEL_VOICES = {
    DEFAULT_TTS_MODEL: tuple(json.loads(Path(__file__).with_name("voice_catalog.json").read_text(encoding="utf-8"))["voices"]),
}
VOICE_IDS = tuple(voice["voiceId"] for voices in MODEL_VOICES.values() for voice in voices)
SPEECH_RATES = ("SLOW", "NORMAL", "FAST")
MODEL_RATES = {"SLOW": 0.9, "NORMAL": 1.0, "FAST": 1.2}


def get_speech_options(model: str = DEFAULT_TTS_MODEL) -> dict:
    """Return fresh presentation data; no credentials, network or profile storage."""
    return {
        "voices": deepcopy(list(MODEL_VOICES.get(model, ()))),
        "speechRates": list(SPEECH_RATES) if model == DEFAULT_TTS_MODEL else [],
        "defaultSpeechRate": "NORMAL",
    }


def validate_voice_id(voice_id: str, model: str = DEFAULT_TTS_MODEL) -> str:
    if not isinstance(voice_id, str) or voice_id not in tuple(voice["voiceId"] for voice in MODEL_VOICES.get(model, ())):
        raise ServiceError("TTS_INVALID_VOICE", "请选择支持的朗读声线", 422)
    return voice_id


def validate_speech_rate(speech_rate: str) -> str:
    if not isinstance(speech_rate, str) or speech_rate not in SPEECH_RATES:
        raise ServiceError("TTS_INVALID_RATE", "请选择支持的朗读语速", 422)
    return speech_rate
