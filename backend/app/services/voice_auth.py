"""Bridge G07's live guardian authentication to the speech protocol."""

import json
import sqlite3
from contextlib import closing

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.api.v1.auth import connect
from app.api.v1.profiles import guardian
from app.errors import ServiceError
from app.services.voice_access import VoicePrincipal


class GuardianVoiceAuthenticator:
    async def authenticate(self, request: Request, *, write: bool) -> VoicePrincipal:
        # SQLite connections are created and closed in the same worker thread.
        return await run_in_threadpool(self._authenticate, request, write=write)

    @staticmethod
    def _authenticate(request: Request, *, write: bool) -> VoicePrincipal:
        try:
            with closing(connect()) as db:
                guardian_id, failure = guardian(db, request, write=write)
        except sqlite3.Error:
            raise ServiceError("DEPENDENCY_UNAVAILABLE", "业务数据暂不可用", 503) from None
        if failure is not None:
            error = json.loads(failure.body)["error"]
            raise ServiceError(error["code"], error["message"], failure.status_code)
        return VoicePrincipal(guardian_id)
