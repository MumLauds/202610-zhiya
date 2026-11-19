"""Internal Bailian speech adapter; callers own authorization and content safety."""

import asyncio
import base64
import io
import json
import logging
import struct
import wave
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from app.errors import ServiceError
from app.speech_config import SpeechSettings
from app.speech_options import MODEL_RATES, validate_speech_rate, validate_voice_id

logger = logging.getLogger("zhiya.voice")
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
AUDIO_HOST = "dashscope-result-bj.oss-cn-beijing.aliyuncs.com"


@dataclass(frozen=True)
class Transcription:
    text: str
    reliable: bool = False


def _error(kind: str, suffix: str, status: int = 503) -> ServiceError:
    return ServiceError(f"{kind}_{suffix}", "语音服务调用失败，请检查输入或稍后再试", status)


def _normalize_tts_wav(data: bytes) -> bytes:
    """Repair only the exact 24 kHz PCM placeholder header observed from Bailian."""
    placeholder = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 0x7FFFFFBF, b"WAVE", b"fmt ",
        16, 1, 1, 24000, 48000, 2, 16, b"data", 0x7FFFFF9B,
    )
    if len(data) > 44 and len(data) % 2 == 0 and data.startswith(placeholder):
        # The completed, size-bounded download contains whole 16-bit mono frames.
        return (data[:4] + struct.pack("<I", len(data) - 8) + data[8:40]
                + struct.pack("<I", len(data) - 44) + data[44:])
    return data


def _tts_wav(data: bytes) -> bool:
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            return (source.getcomptype() == "NONE" and source.getnframes() > 0
                    and source.getframerate() == 24000 and source.getnchannels() == 1
                    and source.getsampwidth() == 2
                    and len(source.readframes(source.getnframes()))
                    == source.getnframes() * source.getnchannels() * source.getsampwidth())
    except (wave.Error, EOFError, RuntimeError):
        return False


class VoiceGateway:
    def __init__(self, settings: SpeechSettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            transport=transport if transport is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            timeout=settings.timeout_seconds, follow_redirects=False, trust_env=False,
        )

    async def __aenter__(self) -> "VoiceGateway":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def transcribe(self, *, audio: bytes, media_type: str) -> Transcription:
        valid = isinstance(audio, bytes) and 0 < len(audio) <= MAX_AUDIO_BYTES
        if valid and media_type == "audio/wav":
            try:
                with wave.open(io.BytesIO(audio), "rb") as source:
                    valid = (source.getcomptype() == "NONE" and source.getsampwidth() == 2
                             and source.getnchannels() == 1 and source.getframerate() == 16000
                             and source.getnframes() > 0
                             and len(source.readframes(source.getnframes())) == source.getnframes() * 2)
            except (wave.Error, EOFError, RuntimeError):
                valid = False
        elif valid and media_type == "audio/mpeg":
            valid = audio.startswith(b"ID3") or (len(audio) > 1 and audio[0] == 255 and audio[1] & 0xE0 == 0xE0)
        else:
            valid = False
        if not valid:
            raise _error("ASR", "INVALID_INPUT", 422)
        return await self._run("ASR", self._transcribe(audio, media_type))

    async def synthesize(self, *, text: str, voice_id: str | None = None, speech_rate: str = "NORMAL") -> bytes:
        try:
            valid = isinstance(text, str) and 1 <= len(text.strip()) <= 600
            if valid:
                text.encode("utf-8")
        except UnicodeEncodeError:
            valid = False
        if not valid:
            raise _error("TTS", "INVALID_INPUT", 422)
        voice = self._settings.tts_voice if voice_id is None else validate_voice_id(voice_id, self._settings.tts_model)
        rate = validate_speech_rate(speech_rate)
        return await self._run("TTS", self._synthesize(text.strip(), voice, rate))

    async def _run(self, kind, operation):
        if not getattr(self._settings, "asr_configured" if kind == "ASR" else "tts_configured"):
            operation.close()
            raise _error(kind, "NOT_CONFIGURED")
        call_id, started, outcome = uuid4().hex, perf_counter(), "CANCELLED"
        try:
            async with asyncio.timeout(self._settings.timeout_seconds):
                result = await operation
            outcome = "OK"
            return result
        except (TimeoutError, httpx.TimeoutException):
            outcome = f"{kind}_TIMEOUT"
            raise _error(kind, "TIMEOUT", 504) from None
        except httpx.RequestError:
            outcome = f"{kind}_UNAVAILABLE"
            raise _error(kind, "UNAVAILABLE") from None
        except ServiceError as exc:
            outcome = exc.code
            raise
        finally:
            logger.info("voice_call call_id=%s outcome=%s duration_ms=%d", call_id, outcome,
                        int((perf_counter() - started) * 1000))

    async def _request(self, kind, method, url, *, payload=None, limit=MAX_RESPONSE_BYTES):
        headers = {"Accept-Encoding": "identity"}
        if method == "POST":
            headers["Authorization"] = f"Bearer {self._settings.api_key}"
        async with self._client.stream(method, url, headers=headers, json=payload) as response:
            if response.status_code in (401, 403):
                raise _error(kind, "AUTH_FAILED")
            if response.status_code == 429:
                raise _error(kind, "RATE_LIMITED")
            if response.status_code >= 500:
                raise _error(kind, "UNAVAILABLE")
            if response.status_code != 200:
                raise _error(kind, "PROVIDER_ERROR")
            if response.headers.get("Content-Encoding", "").strip().lower() not in ("", "identity"):
                raise _error(kind, "INVALID_RESPONSE")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > limit:
                    raise _error(kind, "INVALID_RESPONSE")
                body.extend(chunk)
        return bytes(body)

    async def _transcribe(self, audio, media_type):
        body = await self._request("ASR", "POST", self._settings.asr_api_url, payload={
            "model": self._settings.asr_model, "stream": False,
            "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {
                "data": f"data:{media_type};base64,{base64.b64encode(audio).decode('ascii')}"}}]}],
            "asr_options": {"enable_itn": False},
        })
        try:
            payload = json.loads(body)
            choice = payload["choices"][0]
            message = choice["message"]
            text = message["content"]
            valid = (not payload.get("code") and payload.get("status_code", 200) == 200
                     and message["role"] == "assistant" and not message.get("refusal") and not message.get("tool_calls")
                     and choice["finish_reason"] == "stop" and isinstance(text, str) and 1 <= len(text.strip()) <= 200)
            if valid:
                text.encode("utf-8")
        except (ValueError, TypeError, KeyError, IndexError, RecursionError, AttributeError):
            valid = False
        if not valid:
            raise _error("ASR", "RECOGNITION_FAILED", 422)
        return Transcription(text.strip())

    async def _synthesize(self, text, voice, rate):
        speech_input = {"text": text, "voice": voice, "format": "wav", "sample_rate": 24000,
                        "rate": MODEL_RATES[rate]}
        body = await self._request("TTS", "POST", self._settings.tts_api_url, payload={
            "model": self._settings.tts_model,
            "input": speech_input,
        })
        try:
            payload = json.loads(body)
            output = payload["output"]
            url = output["audio"]["url"]
            if not isinstance(url, str):
                raise ValueError
            url.encode("utf-8")
            parts = urlsplit(url)
            valid = (not payload.get("code") and payload.get("status_code", 200) == 200
                     and output["finish_reason"] == "stop" and parts.scheme in ("http", "https")
                     and parts.hostname == AUDIO_HOST and parts.username is None and parts.password is None
                     and "#" not in url and parts.port in (None, 80 if parts.scheme == "http" else 443)
                     and bool(parts.query) and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url))
        except (ValueError, TypeError, KeyError, IndexError, RecursionError, AttributeError):
            valid = False
        if not valid:
            raise _error("TTS", "INVALID_RESPONSE")
        safe_url = urlunsplit(("https", AUDIO_HOST, parts.path, parts.query, ""))
        audio = await self._request("TTS", "GET", safe_url, limit=MAX_DOWNLOAD_BYTES)
        audio = _normalize_tts_wav(audio)
        if not _tts_wav(audio):
            raise _error("TTS", "INVALID_RESPONSE")
        return audio
