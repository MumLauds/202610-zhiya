"""Short-lived, bounded, process-local audio; cleared on app shutdown/restart."""

import secrets
import threading
import time
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass

from app.errors import ServiceError
from app.services.voice_access import VoicePrincipal, VoiceSnapshot

MEDIA_TTL_SECONDS = 300
MAX_MEDIA_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class VoiceMedia:
    principal: VoicePrincipal
    snapshot: VoiceSnapshot
    voice_id: str
    speech_rate: str
    model: str
    audio: bytes
    expires_at: float


class VoiceMediaStore:
    def __init__(self):
        self.items: dict[str, VoiceMedia] = {}
        self.inflight: set[int] = set()
        self._lock = threading.RLock()

    def expire(self):
        with self._lock:
            now = time.time()
            for key, value in list(self.items.items()):
                if value.expires_at <= now:
                    self.items.pop(key, None)

    @contextmanager
    def claim(self, session_id: int):
        with self._lock:
            if session_id in self.inflight:
                raise ServiceError("VOICE_REQUEST_IN_PROGRESS", "该会话正在处理语音，请稍后再试", 409)
            if len(self.inflight) >= 4:
                raise ServiceError("VOICE_BUSY", "语音服务繁忙，请稍后再试", 429)
            self.inflight.add(session_id)
        try:
            yield
        finally:
            with self._lock:
                self.inflight.discard(session_id)

    def find(self, principal, snapshot, voice_id, speech_rate, model):
        with self._lock:
            self.expire()
            for key, value in self.items.items():
                if (value.principal, value.snapshot, value.voice_id, value.speech_rate, value.model) == (
                        principal, snapshot, voice_id, speech_rate, model):
                    return key, value
            return None

    def get(self, resource_key: str) -> VoiceMedia | None:
        with self._lock:
            self.expire()
            return self.items.get(resource_key)

    def put(self, principal, snapshot, voice_id, speech_rate, model, audio):
        with self._lock:
            self.expire()
            if len(audio) > MAX_MEDIA_BYTES:
                raise ServiceError("MEDIA_TOO_LARGE", "音频超过资源限制", 503)
            while self.items and sum(len(item.audio) for item in self.items.values()) + len(audio) > MAX_MEDIA_BYTES:
                self.items.pop(next(iter(self.items)), None)
            key = "audio-" + secrets.token_urlsafe(24)
            value = VoiceMedia(principal, snapshot, voice_id, speech_rate, model, audio, time.time() + MEDIA_TTL_SECONDS)
            self.items[key] = value
            return key, value

    def purge_sessions(self, session_ids: Iterable[int]) -> None:
        targets = set(session_ids)
        if not targets:
            return
        with self._lock:
            for key in [k for k, v in self.items.items() if v.snapshot.session_id in targets]:
                self.items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self.items.clear()
