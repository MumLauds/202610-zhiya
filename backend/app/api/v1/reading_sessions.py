"""Guardian reading sessions: normal creation and recovery (no continuation switching)."""

import json
import hashlib
import secrets
import sqlite3
import time
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.v1.auth import (
    Principal,
    SESSION_SECONDS,
    authorized_db,
    error,
    iso_utc,
    set_session_cookies,
    token_digest,
)
from app.api.v1.storybooks import VISIBLE, page_object, readable_story_version
from app.services.cleanup import delete_session_rows, purge_voice_media
from app.services.reading_reports import (
    fact_summary,
    generate_report,
    report_object,
)

router = APIRouter(prefix="/api/v1/reading-sessions")
MAX_ID = 9007199254740991
MAX_ANSWER_LENGTH = 200
CHILD_SHORT_ANSWERS = ("是", "不是", "不知道")


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    childProfileId: int = Field(ge=1, le=MAX_ID, strict=True)
    storyVersionId: int = Field(ge=1, le=MAX_ID, strict=True)
    mode: Literal["PARENT_CHILD"]


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # TIME_LIMIT is server-triggered and CONTINUED belongs to continuation switching.
    reason: Literal["USER_FINISH"]
    expectedRevision: int = Field(ge=0, le=MAX_ID, strict=True)
    clientEventId: str = Field(min_length=1, max_length=64)


class ReadingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eventType: Literal["STARTED", "PAGE_CHANGED", "ANSWER", "FEEDBACK"]
    pageNumber: int = Field(ge=1, le=MAX_ID, strict=True)
    expectedRevision: int = Field(ge=0, le=MAX_ID, strict=True)
    clientEventId: str = Field(min_length=1, max_length=64)
    inputText: str | None = None
    feedback: Literal["UNDERSTOOD", "NOT_UNDERSTOOD", "SKIPPED"] | None = None

    @field_validator("inputText", mode="before")
    @classmethod
    def clean_input(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_event_fields(self):
        if self.eventType == "ANSWER":
            if self.inputText is None or not 1 <= len(self.inputText) <= MAX_ANSWER_LENGTH or self.feedback is not None:
                raise ValueError("ANSWER 事件需要 1～200 字符 inputText 且不可携带 feedback")
        elif self.eventType == "FEEDBACK":
            if self.feedback is None or self.inputText is not None:
                raise ValueError("FEEDBACK 事件需要 feedback 且不可携带 inputText")
        elif self.inputText is not None or self.feedback is not None:
            raise ValueError("当前事件类型不支持 inputText 或 feedback")
        return self


class BranchChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branchId: int = Field(ge=1, le=MAX_ID, strict=True)
    expectedRevision: int = Field(ge=0, le=MAX_ID, strict=True)
    clientEventId: str = Field(min_length=1, max_length=64)


class ModeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["CHILD_SOLO"]
    allowedStoryVersionIds: list[Annotated[int, Field(ge=1, le=MAX_ID, strict=True)]] = Field(min_length=1, max_length=100)
    timeLimitSeconds: int = Field(ge=60, le=1800, strict=True)


def current_question(db: sqlite3.Connection, session: sqlite3.Row) -> sqlite3.Row | None:
    questions = db.execute(
        """SELECT output_text, response_json FROM interaction_events
           WHERE session_id = ? AND version_id = ? AND page_no = ? AND event_type = 'QUESTION'
             AND result_status IN ('SUCCESS', 'FALLBACK') AND output_text IS NOT NULL
           ORDER BY id DESC""",
        (session["id"], session["version_id"], session["current_page_no"]),
    )
    return next((question for question in questions if question["output_text"].strip()), None)


def child_answer_options(db: sqlite3.Connection, session: sqlite3.Row) -> list[str]:
    question = current_question(db, session)
    if question is None:
        choices = db.execute(
            """SELECT choice_label FROM story_branches
               WHERE version_id = ? AND from_page_no = ? AND review_status = 'APPROVED' ORDER BY id""",
            (session["version_id"], session["current_page_no"]),
        )
        options = (choice["choice_label"] for choice in choices)
    else:
        data = json.loads(question["response_json"]).get("data")
        if not isinstance(data, dict) or "answerOptions" not in data:
            return list(CHILD_SHORT_ANSWERS)
        options = data["answerOptions"]
        if not isinstance(options, list):
            return []
    labels = (option.strip() for option in options if isinstance(option, str))
    return list(dict.fromkeys(label for label in labels if 1 <= len(label) <= MAX_ANSWER_LENGTH))


def session_object(row):
    expiry = row["grant_expires_at"]
    return {
        "readingSessionId": row["id"], "storyVersionId": row["version_id"],
        "currentPage": row["current_page_no"], "visitedPageNumbers": json.loads(row["visited_pages_json"]),
        "mode": row["mode"], "status": row["status"], "revision": row["revision"],
        "expiresAt": iso_utc(expiry) if expiry is not None else None,
    }


# Retired versions remain readable for existing sessions (history, reports and replay),
# while new session creation still rejects retired LIBRARY versions and WITHDRAWN blocks all access.
SESSION_ACCESS = """(v.status = 'PUBLISHED' OR
    (v.status = 'APPROVED_PRIVATE' AND b.owner_guardian_id = ?) OR
    (v.status = 'RETIRED' AND v.reviewed_by_id IS NOT NULL AND v.reviewed_at IS NOT NULL
       AND ((b.source_type = 'ORIGINAL' AND b.owner_guardian_id = ?)
         OR b.source_type = 'LIBRARY')))"""


@router.post("", status_code=201)
def create_session(payload: SessionCreate, request: Request):
    try:
        with authorized_db(request, write=True, guardian_only=True) as (db, principal, failure):
            if failure:
                return failure
            owner = principal.guardian_id
            profile = db.execute("SELECT 1 FROM child_profiles WHERE id = ? AND guardian_id = ?",
                                 (payload.childProfileId, owner)).fetchone()
            version = db.execute(
                "SELECT v.id FROM story_versions v JOIN storybooks b ON b.id = v.storybook_id "
                "WHERE v.id = ? AND " + VISIBLE +
                " AND EXISTS (SELECT 1 FROM story_pages p WHERE p.version_id = v.id AND p.page_no = 1)",
                (payload.storyVersionId, owner, True, owner),
            ).fetchone()
            if not profile or not version:
                return error(request, 404, "RESOURCE_NOT_FOUND", "档案或绘本不可用")
            cursor = db.execute(
                """INSERT INTO reading_sessions
                (profile_id, version_id, current_page_no, mode, status, created_at)
                VALUES (?, ?, 1, 'PARENT_CHILD', 'CREATED', ?)""",
                (payload.childProfileId, payload.storyVersionId, int(time.time() * 1000)),
            )
            row = db.execute("SELECT *, NULL AS grant_expires_at FROM reading_sessions WHERE id = ?",
                             (cursor.lastrowid,)).fetchone()
            return {"data": session_object(row), "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")


@router.get("")
def list_sessions(request: Request, page: int = Query(default=1, ge=1, le=MAX_ID),
                  pageSize: int = Query(default=20, ge=1, le=50)):
    try:
        with authorized_db(request, guardian_only=True) as (db, principal, failure):
            if failure:
                return failure
            owner = principal.guardian_id
            offset = min((page - 1) * pageSize, 9223372036854775807)
            rows = db.execute(
                """SELECT s.*, g.expires_at AS grant_expires_at FROM reading_sessions s
                LEFT JOIN child_grants g ON g.id = s.grant_id
                JOIN child_profiles p ON p.id = s.profile_id
                JOIN story_versions v ON v.id = s.version_id
                JOIN storybooks b ON b.id = v.storybook_id
                WHERE p.guardian_id = ? AND """ + SESSION_ACCESS +
                " ORDER BY s.created_at DESC, s.id DESC LIMIT ? OFFSET ?",
                (owner, owner, owner, pageSize, offset),
            ).fetchall()
            return {"data": {"items": [session_object(row) for row in rows],
                             "page": page, "pageSize": pageSize}, "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)


@router.get("/{session_id}")
def get_session(request: Request, session_id: int = Path(ge=1, le=MAX_ID)):
    try:
        with authorized_db(request) as (db, principal, failure):
            if failure:
                return failure
            row = owned_session(db, session_id, principal)
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            event_query = "SELECT id, event_type, page_no FROM interaction_events WHERE session_id = ?"
            event_params = [session_id]
            if principal.role == "CHILD":
                event_query += " AND page_no = ?"
                event_params.append(row["current_page_no"])
            events = db.execute(event_query + " ORDER BY id", event_params).fetchall()
            report = None
            if principal.role == "GUARDIAN":
                report_row = db.execute("SELECT * FROM reading_reports WHERE session_id = ?", (session_id,)).fetchone()
                report = report_object(session_id, report_row)
            data = {"session": session_object(row),
                    "events": [{"interactionEventId": e["id"], "eventType": e["event_type"],
                                "pageNumber": e["page_no"]} for e in events],
                    "report": report}
            if principal.role == "CHILD" and row["status"] == "IN_PROGRESS":
                data["answerOptions"] = child_answer_options(db, row)
            return {"data": data, "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)


@router.put("/{session_id}/mode-settings")
def update_mode_settings(payload: ModeSettings, request: Request, session_id: int = Path(ge=1, le=MAX_ID)):
    """API-020: enter CHILD_SOLO in CREATED (revoking guardian session), or shorten expiry from another device."""
    unique_ids = list(dict.fromkeys(payload.allowedStoryVersionIds))
    child_token: str | None = None
    try:
        with authorized_db(request, write=True, guardian_only=True) as (db, principal, failure):
            if failure:
                return failure
            owner = principal.guardian_id
            now = int(time.time() * 1000)
            row = db.execute(
                """SELECT s.*, v.status AS version_status FROM reading_sessions s
                JOIN child_profiles p ON p.id = s.profile_id
                JOIN story_versions v ON v.id = s.version_id
                WHERE s.id = ? AND p.guardian_id = ?""",
                (session_id, owner),
            ).fetchone()
            if not row or row["version_status"] == "WITHDRAWN":
                return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            if row["version_id"] not in unique_ids:
                return error(request, 422, "VALIDATION_ERROR", "授权版本范围须包含当前会话绘本")
            if row["status"] == "CREATED" and row["mode"] == "PARENT_CHILD" and row["grant_id"] is None:
                placeholders = ",".join("?" * len(unique_ids))
                grantable = db.execute(
                    f"""SELECT v.id FROM story_versions v JOIN storybooks b ON b.id = v.storybook_id
                        WHERE v.id IN ({placeholders}) AND {VISIBLE}
                          AND EXISTS (SELECT 1 FROM story_pages p WHERE p.version_id = v.id AND p.page_no = 1)""",
                    (*unique_ids, owner, True, owner),
                ).fetchall()
                if len(grantable) != len(unique_ids):
                    return error(request, 404, "RESOURCE_NOT_FOUND", "包含不可授权的绘本版本")
                expires_at = now + payload.timeLimitSeconds * 1000
                child_token = secrets.token_urlsafe(32)
                db.execute(
                    "UPDATE auth_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                    (now, principal.auth_session_id),
                )
                cursor = db.execute(
                    """INSERT INTO child_grants
                       (profile_id, issued_by_session_id, token_digest, issued_at, expires_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (row["profile_id"], principal.auth_session_id, token_digest(child_token), now, expires_at),
                )
                grant_id = cursor.lastrowid
                db.executemany(
                    "INSERT INTO child_grant_books (grant_id, version_id) VALUES (?, ?)",
                    [(grant_id, vid) for vid in unique_ids],
                )
                db.execute(
                    "UPDATE reading_sessions SET mode = 'CHILD_SOLO', grant_id = ? WHERE id = ?",
                    (grant_id, session_id),
                )
                result_ids = unique_ids
            elif row["mode"] == "CHILD_SOLO" and row["grant_id"] is not None and row["status"] in ("CREATED", "IN_PROGRESS"):
                grant = db.execute("SELECT * FROM child_grants WHERE id = ?", (row["grant_id"],)).fetchone()
                if not grant or grant["revoked_at"] is not None or grant["expires_at"] <= now:
                    return error(request, 409, "SESSION_CONFLICT", "儿童授权已失效，无法调整时长")
                existing_ids = [
                    r[0] for r in db.execute(
                        "SELECT version_id FROM child_grant_books WHERE grant_id = ? ORDER BY version_id",
                        (grant["id"],),
                    )
                ]
                if set(unique_ids) != set(existing_ids):
                    return error(request, 409, "SESSION_CONFLICT", "运行中不可修改授权绘本范围")
                # Shortening an existing grant preserves access to LIBRARY versions that retired after issuance,
                # while WITHDRAWN remains rejected immediately.
                placeholders = ",".join("?" * len(existing_ids))
                accessible = db.execute(
                    f"""SELECT v.id FROM story_versions v JOIN storybooks b ON b.id = v.storybook_id
                        WHERE v.id IN ({placeholders}) AND {SESSION_ACCESS}""",
                    (*existing_ids, owner, owner),
                ).fetchall()
                if len(accessible) != len(existing_ids):
                    return error(request, 404, "RESOURCE_NOT_FOUND", "授权绘本版本不可用")
                expires_at = now + payload.timeLimitSeconds * 1000
                if expires_at > grant["expires_at"]:
                    return error(request, 409, "SESSION_CONFLICT", "仅可缩短儿童授权时长，不可延长")
                db.execute("UPDATE child_grants SET expires_at = ? WHERE id = ?", (expires_at, grant["id"]))
                grant_id = grant["id"]
                result_ids = existing_ids
            else:
                return error(request, 409, "SESSION_CONFLICT", "当前会话状态不支持设置儿童模式")
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
    response = JSONResponse(status_code=200, content={
        "data": {
            "mode": "CHILD_SOLO",
            "childGrantId": grant_id,
            "allowedStoryVersionIds": result_ids,
            "expiresAt": iso_utc(expires_at),
        },
        "requestId": request.state.request_id,
    })
    if child_token is not None:
        # Retain cookies longer than child timeLimitSeconds so API-021 parent unlock can still authenticate after expiry.
        set_session_cookies(response, child_token, SESSION_SECONDS)
    return response


@router.delete("/{session_id}", status_code=204)
def delete_session(request: Request, session_id: int = Path(ge=1, le=MAX_ID)):
    """API-014: delete a guardian-owned reading session, its derived rows, and revoke its active grant."""
    try:
        with authorized_db(request, write=True, guardian_only=True, settle_guardian=False) as (db, principal, failure):
            if failure:
                return failure
            owner = principal.guardian_id
            row = db.execute(
                """SELECT s.id, s.grant_id FROM reading_sessions s
                JOIN child_profiles p ON p.id = s.profile_id
                WHERE s.id = ? AND p.guardian_id = ?""",
                (session_id, owner),
            ).fetchone()
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            now = int(time.time() * 1000)
            delete_session_rows(db, session_id)
            if row["grant_id"] is not None:
                db.execute(
                    "UPDATE child_grants SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
                    (now, row["grant_id"]),
                )
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
    purge_voice_media(request.app.state, [session_id])
    return Response(status_code=204)


def request_digest(path: str, body: dict) -> str:
    canonical = json.dumps({"method": "POST", "path": path, "body": body},
                           sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def owned_session(db, session_id: int, principal: Principal):
    if principal.role == "CHILD":
        return db.execute(
            """SELECT s.*, g.expires_at AS grant_expires_at FROM reading_sessions s
            JOIN child_grants g ON g.id = s.grant_id
            JOIN child_profiles p ON p.id = s.profile_id
            JOIN story_versions v ON v.id = s.version_id
            JOIN storybooks b ON b.id = v.storybook_id
            WHERE s.id = ? AND s.grant_id = ? AND s.profile_id = ?
              AND EXISTS (SELECT 1 FROM child_grant_books gb WHERE gb.grant_id = ? AND gb.version_id = s.version_id)
              AND """ + SESSION_ACCESS,
            (session_id, principal.grant_id, principal.profile_id, principal.grant_id,
             principal.guardian_id, principal.guardian_id),
        ).fetchone()
    return db.execute(
        """SELECT s.*, g.expires_at AS grant_expires_at FROM reading_sessions s
        LEFT JOIN child_grants g ON g.id = s.grant_id
        JOIN child_profiles p ON p.id = s.profile_id
        JOIN story_versions v ON v.id = s.version_id
        JOIN storybooks b ON b.id = v.storybook_id
        WHERE s.id = ? AND p.guardian_id = ? AND """ + SESSION_ACCESS,
        (session_id, principal.guardian_id, principal.guardian_id, principal.guardian_id),
    ).fetchone()


def replay_session_event(db, request: Request, session_id: int, client_event_id: str, digest: str,
                         principal: Principal | None = None, *, page_version_id: int | None = None):
    """Shared SESSION_EVENT lookup for events/completions (and later questions/guidance/branches).

    Returns a response when the key was already used, otherwise None.
    """
    previous = db.execute("""SELECT request_digest, status, response_status, response_body, deadline_at
                          FROM request_executions WHERE scope_type = 'SESSION_EVENT'
                          AND scope_id = ? AND client_request_id = ?""",
                          (session_id, client_event_id)).fetchone()
    if not previous:
        return None
    if previous["request_digest"] != digest:
        return error(request, 409, "IDEMPOTENCY_CONFLICT", "请求标识已用于其他操作")
    if previous["status"] == "RUNNING":
        now = int(time.time() * 1000)
        if previous["deadline_at"] <= now:
            failure = error(request, 504, "REQUEST_OUTCOME_UNKNOWN", "请求结果未知，请先查询会话")
            db.execute("""UPDATE request_executions SET status = 'UNKNOWN', completed_at = ?,
                          response_status = 504, response_body = ? WHERE scope_type = 'SESSION_EVENT'
                          AND scope_id = ? AND client_request_id = ? AND status = 'RUNNING'
                          AND deadline_at <= ?""",
                       (now, failure.body.decode('utf-8'), session_id, client_event_id, now))
            return failure
        return error(request, 409, "REQUEST_IN_PROGRESS", "请求正在处理", retryable=True, headers={"Retry-After": "1"})
    if previous["status"] == "UNKNOWN":
        return error(request, 504, "REQUEST_OUTCOME_UNKNOWN", "请求结果未知，请先查询会话")
    if (previous["status"] == "SUCCEEDED" and page_version_id is not None
            and readable_story_version(db, principal, page_version_id) is None):
        return error(request, 404, "RESOURCE_NOT_FOUND", "绘本不可用")
    body = json.loads(previous["response_body"])
    body["requestId"] = request.state.request_id
    if principal is not None and principal.role == "CHILD" and isinstance(body.get("data"), dict) and "report" in body["data"]:
        body["data"]["report"] = None
    return JSONResponse(status_code=200 if previous["status"] == "SUCCEEDED" else previous["response_status"],
                        content=body)


def record_session_event(db, owner: int, session_id: int, client_event_id: str, digest: str,
                         now: int, status_code: int, response: dict, *,
                         status: str = "SUCCEEDED", execution_token: str | None = None) -> str:
    token = execution_token or str(uuid4())
    db.execute("""INSERT INTO request_executions
        (owner_account_id, session_id, scope_type, scope_id, client_request_id, request_digest,
         status, execution_token, started_at, deadline_at, completed_at, response_status, response_body)
        VALUES (?, ?, 'SESSION_EVENT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (owner, session_id, session_id, client_event_id, digest, status, token, now, now + 25000,
         now, status_code, json.dumps(response, ensure_ascii=False)))
    return token


def fail_session_event(db, request: Request, owner: int, session_id: int, client_event_id: str,
                       digest: str, status_code: int, code: str, message: str):
    failure = error(request, status_code, code, message)
    record_session_event(db, owner, session_id, client_event_id, digest,
                         int(time.time() * 1000), status_code, json.loads(failure.body), status="FAILED")
    return failure


@router.post("/{session_id}/events", status_code=201)
def add_reading_event(payload: ReadingEvent, request: Request, session_id: int = Path(ge=1, le=MAX_ID)):
    digest = request_digest(f"/api/v1/reading-sessions/{session_id}/events", payload.model_dump(exclude_none=True))
    try:
        with authorized_db(request, write=True) as (db, principal, failure):
            if failure:
                return failure
            row = owned_session(db, session_id, principal)
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            replay = replay_session_event(db, request, session_id, payload.clientEventId, digest, principal)
            if replay is not None:
                return replay
            if row["revision"] != payload.expectedRevision:
                return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                          digest, 409, "SESSION_CONFLICT", "会话状态已变化")
            now = int(time.time() * 1000)
            visited = json.loads(row["visited_pages_json"])
            if payload.eventType == "STARTED":
                valid = row["status"] == "CREATED" and payload.pageNumber == row["start_page_number"]
                next_page_no = payload.pageNumber
                if valid:
                    visited.append(payload.pageNumber)
            elif payload.eventType == "PAGE_CHANGED":
                page = db.execute("SELECT default_next_page_number FROM story_pages WHERE version_id = ? AND page_no = ?",
                                  (row["version_id"], row["current_page_no"])).fetchone()
                valid = row["status"] == "IN_PROGRESS" and page and page[0] == payload.pageNumber
                next_page_no = payload.pageNumber
                if valid:
                    visited.append(payload.pageNumber)
            elif payload.eventType == "ANSWER":
                valid = row["status"] == "IN_PROGRESS" and payload.pageNumber == row["current_page_no"]
                next_page_no = row["current_page_no"]
                if valid and principal.role == "CHILD" and payload.inputText not in child_answer_options(db, row):
                    return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                              digest, 422, "VALIDATION_ERROR", "请选择当前页已审核的回答选项")
            else:  # FEEDBACK
                valid = row["status"] == "IN_PROGRESS" and payload.pageNumber == row["current_page_no"]
                next_page_no = row["current_page_no"]
                if valid and current_question(db, row) is None:
                    return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                              digest, 409, "PROMPT_REQUIRED", "当前页尚未有引导问题，无法提交反馈")
            if not valid:
                return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                          digest, 409, "SESSION_CONFLICT", "当前状态或页面不允许此操作")
            revision = row["revision"] + 1
            db.execute("""UPDATE reading_sessions SET status = 'IN_PROGRESS', current_page_no = ?,
                       started_at = COALESCE(started_at, ?), revision = ?, visited_pages_json = ? WHERE id = ?""",
                       (next_page_no, now, revision, json.dumps(visited), session_id))
            cursor = db.execute("""INSERT INTO interaction_events
                (session_id, version_id, request_key, request_digest, response_json, page_no, event_type,
                 feedback, input_text, result_status, revision_after, created_at)
                VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, 'SUCCESS', ?, ?)""",
                (session_id, row["version_id"], payload.clientEventId, digest, payload.pageNumber,
                 payload.eventType, payload.feedback, payload.inputText, revision, now))
            response = {"data": {"interactionEventId": cursor.lastrowid, "revision": revision}, "requestId": request.state.request_id}
            db.execute("UPDATE interaction_events SET response_json = ? WHERE id = ?",
                       (json.dumps(response, ensure_ascii=False), cursor.lastrowid))
            record_session_event(db, principal.guardian_id, session_id, payload.clientEventId, digest, now, 201, response)
            return JSONResponse(status_code=201, content=response)
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")


@router.post("/{session_id}/branches", status_code=201)
def choose_branch(payload: BranchChoice, request: Request, session_id: int = Path(ge=1, le=MAX_ID)):
    """API-018: select an APPROVED branch from the current page and advance the reading session."""
    digest = request_digest(f"/api/v1/reading-sessions/{session_id}/branches", payload.model_dump())
    try:
        with authorized_db(request, write=True) as (db, principal, failure):
            if failure:
                return failure
            row = owned_session(db, session_id, principal)
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            replay = replay_session_event(db, request, session_id, payload.clientEventId, digest, principal,
                                          page_version_id=row["version_id"])
            if replay is not None:
                return replay
            if row["status"] != "IN_PROGRESS" or row["revision"] != payload.expectedRevision:
                return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                          digest, 409, "SESSION_CONFLICT", "会话状态或进度已变化")
            branch = db.execute(
                """SELECT from_page_no, next_page_no, review_status FROM story_branches
                   WHERE id = ? AND version_id = ?""",
                (payload.branchId, row["version_id"]),
            ).fetchone()
            if not branch:
                return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                          digest, 404, "RESOURCE_NOT_FOUND", "分支不存在")
            if branch["from_page_no"] != row["current_page_no"] or branch["review_status"] != "APPROVED":
                return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                          digest, 409, "SESSION_CONFLICT", "当前页面或审核状态不允许选择该分支")
            next_page = db.execute(
                "SELECT * FROM story_pages WHERE version_id = ? AND page_no = ?",
                (row["version_id"], branch["next_page_no"]),
            ).fetchone()
            if not next_page:
                return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                          digest, 404, "RESOURCE_NOT_FOUND", "目标页面不可用")
            now = int(time.time() * 1000)
            revision = row["revision"] + 1
            visited = json.loads(row["visited_pages_json"])
            visited.append(next_page["page_no"])
            db.execute(
                "UPDATE reading_sessions SET current_page_no = ?, revision = ?, visited_pages_json = ? WHERE id = ?",
                (next_page["page_no"], revision, json.dumps(visited), session_id),
            )
            cursor = db.execute(
                """INSERT INTO interaction_events
                (session_id, version_id, request_key, request_digest, response_json, page_no, branch_id,
                 event_type, result_status, revision_after, created_at)
                VALUES (?, ?, ?, ?, '{}', ?, ?, 'BRANCH_CHOSEN', 'SUCCESS', ?, ?)""",
                (session_id, row["version_id"], payload.clientEventId, digest, branch["from_page_no"],
                 payload.branchId, revision, now),
            )
            response = {
                "data": {
                    "readingSessionId": session_id,
                    "branchId": payload.branchId,
                    "currentPage": next_page["page_no"],
                    "revision": revision,
                    "interactionEventId": cursor.lastrowid,
                    "nextPage": page_object(next_page),
                    "visitedPageNumbers": visited,
                },
                "requestId": request.state.request_id,
            }
            db.execute("UPDATE interaction_events SET response_json = ? WHERE id = ?",
                       (json.dumps(response, ensure_ascii=False), cursor.lastrowid))
            record_session_event(db, principal.guardian_id, session_id, payload.clientEventId, digest, now, 201, response)
            return JSONResponse(status_code=201, content=response)
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")


@router.post("/{session_id}/completions")
def finish_session(payload: Completion, request: Request, background: BackgroundTasks,
                   session_id: int = Path(ge=1, le=MAX_ID)):
    """API-019 USER_FINISH (DCL-006 candidate rules). Report generation runs after commit."""
    digest = request_digest(f"/api/v1/reading-sessions/{session_id}/completions", payload.model_dump())
    try:
        with authorized_db(request, write=True) as (db, principal, failure):
            if failure:
                return failure
            row = owned_session(db, session_id, principal)
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            replay = replay_session_event(db, request, session_id, payload.clientEventId, digest, principal)
            if replay is not None:
                return replay
            now = int(time.time() * 1000)
            generation = None
            generation_token = None
            if row["status"] == "FINISHED":
                # New key after finish: existing terminal state and current report, no model call.
                report = db.execute("SELECT * FROM reading_reports WHERE session_id = ?", (session_id,)).fetchone()
            elif row["status"] == "IN_PROGRESS" and row["revision"] == payload.expectedRevision:
                visited = json.loads(row["visited_pages_json"])
                facts = fact_summary(visited)
                generation_token = str(uuid4())
                db.execute("""UPDATE reading_sessions SET status = 'FINISHED', end_reason = 'USER_FINISH',
                           ended_at = ?, revision = revision + 1 WHERE id = ? AND revision = ?""",
                           (now, session_id, row["revision"]))
                db.execute("""INSERT INTO reading_reports (session_id, status, summary, activities_json, updated_at)
                           VALUES (?, 'PENDING', ?, '[]', ?)""", (session_id, facts, now))
                pages = list(dict.fromkeys(visited))
                texts = [r[0] for r in db.execute(
                    "SELECT body_text FROM story_pages WHERE version_id = ? AND page_no IN (%s) ORDER BY page_no"
                    % ",".join("?" * len(pages)), (row["version_id"], *pages))] if pages else []
                generation = (facts, texts)
                report = db.execute("SELECT * FROM reading_reports WHERE session_id = ?", (session_id,)).fetchone()
            else:
                return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                          digest, 409, "SESSION_CONFLICT", "阅读进度已更新，请刷新后再结束")
            current = db.execute(
                """SELECT s.*, g.expires_at AS grant_expires_at FROM reading_sessions s
                   LEFT JOIN child_grants g ON g.id = s.grant_id WHERE s.id = ?""",
                (session_id,),
            ).fetchone()
            response = {"data": {"session": session_object(current), "report": report_object(session_id, report)},
                        "requestId": request.state.request_id}
            record_session_event(db, principal.guardian_id, session_id, payload.clientEventId, digest, now, 200, response,
                                 execution_token=generation_token)
            if principal.role == "CHILD":
                response["data"]["report"] = None
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
    if generation is not None:
        background.add_task(generate_report, session_id, *generation,
                            getattr(request.app.state, "model_adapter", None),
                            generation_token=generation_token)
    return JSONResponse(status_code=200, content=response)
