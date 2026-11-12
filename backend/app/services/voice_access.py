"""Read-only voice authorization against the existing schema; authentication is supplied by G07."""

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request

from app.core.config import database_path
from app.errors import ServiceError


@dataclass(frozen=True)
class VoicePrincipal:
    guardian_id: int
    child_grant_id: int | None = None


class VoiceAuthenticator(Protocol):
    async def authenticate(self, request: Request, *, write: bool) -> VoicePrincipal:
        """Verify the live server cookie, expiry/revocation, role and CSRF on writes."""
        ...


class UnconfiguredVoiceAuthenticator:
    async def authenticate(self, request: Request, *, write: bool) -> VoicePrincipal:
        raise ServiceError("VOICE_AUTH_NOT_CONFIGURED", "语音业务认证尚未接入", 503)


@dataclass(frozen=True)
class VoiceSnapshot:
    session_id: int
    version_id: int
    page_number: int
    revision: int
    text: str
    event_id: int | None = None


def _deny():
    return ServiceError("RESOURCE_NOT_FOUND", "请求的资源不存在", 404)


def read_voice_source(principal: VoicePrincipal, *, session_id: int | None = None,
                      page_number: int | None = None, revision: int | None = None,
                      event_id: int | None = None) -> VoiceSnapshot:
    try:
        with closing(sqlite3.connect(f"{database_path().as_uri()}?mode=ro", uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            event = None
            if event_id is not None:
                event = db.execute("SELECT * FROM interaction_events WHERE id=?", (event_id,)).fetchone()
                if event is None:
                    raise _deny()
                session_id = event["session_id"]
            row = db.execute("""SELECT s.*, p.guardian_id, v.status AS version_status,
                v.reviewed_by_id, v.reviewed_at, b.source_type, b.owner_guardian_id
                FROM reading_sessions s JOIN child_profiles p ON p.id=s.profile_id
                JOIN accounts a ON a.id=p.guardian_id AND a.role='GUARDIAN'
                JOIN story_versions v ON v.id=s.version_id JOIN storybooks b ON b.id=v.storybook_id
                WHERE s.id=? AND p.guardian_id=?""", (session_id, principal.guardian_id)).fetchone()
            if row is None:
                raise _deny()
            if principal.child_grant_id is not None and principal.child_grant_id != row["grant_id"]:
                raise _deny()
            if row["mode"] == "CHILD_SOLO":
                grant = db.execute("""SELECT g.* FROM child_grants g JOIN child_grant_books b
                    ON b.grant_id=g.id AND b.version_id=? WHERE g.id=? AND g.profile_id=?""",
                    (row["version_id"], row["grant_id"], row["profile_id"])).fetchone()
                now = int(time.time() * 1000)
                if grant is None or grant["revoked_at"] is not None or not grant["issued_at"] <= now < grant["expires_at"]:
                    raise _deny()
            elif principal.child_grant_id is not None:
                raise _deny()
            if row["version_status"] not in ("PUBLISHED", "APPROVED_PRIVATE", "RETIRED"):
                raise _deny()
            if row["source_type"] == "ORIGINAL":
                if (row["owner_guardian_id"] != principal.guardian_id or row["version_status"] == "PUBLISHED"
                        or row["reviewed_by_id"] is None or row["reviewed_at"] is None):
                    raise _deny()
            elif row["version_status"] not in ("PUBLISHED", "RETIRED"):
                raise _deny()
            if row["status"] != "IN_PROGRESS":
                raise ServiceError("SESSION_CONFLICT", "阅读会话已结束或尚未开始", 409)
            if event is not None:
                page_number, revision = event["page_no"], event["revision_after"]
                if (event["version_id"] != row["version_id"] or event["event_type"] not in ("ASK", "QUESTION", "ANSWER", "FEEDBACK")
                        or event["result_status"] not in ("SUCCESS", "FALLBACK") or not event["output_text"]):
                    raise _deny()
            if page_number != row["current_page_no"] or revision != row["revision"]:
                raise ServiceError("SESSION_CONFLICT", "阅读进度已变化，请刷新后重试", 409)
            page = db.execute("SELECT body_text FROM story_pages WHERE version_id=? AND page_no=?",
                              (row["version_id"], page_number)).fetchone()
            if page is None:
                raise _deny()
            return VoiceSnapshot(session_id, row["version_id"], page_number, revision,
                                 event["output_text"] if event is not None else page["body_text"], event_id)
    except sqlite3.Error:
        raise ServiceError("SERVICE_UNAVAILABLE", "业务数据暂不可用", 503) from None


def recheck_source(principal: VoicePrincipal, snapshot: VoiceSnapshot) -> None:
    current = read_voice_source(principal, session_id=snapshot.session_id,
                               page_number=snapshot.page_number, revision=snapshot.revision,
                               event_id=snapshot.event_id)
    if current != snapshot:
        raise ServiceError("SESSION_CONFLICT", "朗读内容已变化，请刷新后重试", 409)
