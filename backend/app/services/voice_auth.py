"""Bridge G07's live guardian authentication to the speech protocol."""

import json
import sqlite3

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.api.v1.auth import authorized_db
from app.errors import ServiceError
from app.services.voice_access import VoicePrincipal


class GuardianVoiceAuthenticator:
    async def authenticate(self, request: Request, *, write: bool) -> VoicePrincipal:
        # SQLite connections are created and closed in the same worker thread.
        return await run_in_threadpool(self._authenticate, request, write=write)

    @staticmethod
    def _authenticate(request: Request, *, write: bool) -> VoicePrincipal:
        try:
            with authorized_db(request, csrf=write) as (_, principal, failure):
                if failure is None:
                    return VoicePrincipal(principal.guardian_id, principal.grant_id)
        except sqlite3.Error:
            raise ServiceError("DEPENDENCY_UNAVAILABLE", "业务数据暂不可用", 503) from None
        error = json.loads(failure.body)["error"]
        raise ServiceError(error["code"], error["message"], failure.status_code)
