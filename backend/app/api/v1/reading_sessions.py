"""Guardian and child reading sessions, events, branches, completions, and continuation switching."""

import asyncio
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
from starlette.concurrency import run_in_threadpool
from app.db import yaya_agent as yaya_db
from app.services import yaya_agent as yaya

from app.api.v1.auth import (
    Principal,
    SESSION_SECONDS,
    authorized_db,
    error,
    iso_utc,
    set_session_cookies,
    token_digest,
)
from app.api.v1.storybooks import VISIBLE, owned_generation_version, page_object, readable_story_version
from app.services.cleanup import delete_session_rows, purge_voice_media
from app.services.reading_reports import (
    answer_based_report,
    fact_summary,
    generate_report,
    report_object,
    session_read_pages,
)
from app.services.story_media import MediaDependencyError, ensure_opening_media
from app.services.reading_agent import (
    ANSWER_EARLIER_PAGE_CHARS, ANSWER_PAGE_LOOKAHEAD, MAX_ANSWER_FOLLOWUPS, MAX_RECENT_TURNS,
    RecentReadingTurn, answer_is_unsafe, close_reading_reply,
    generate_answer_reply, reply_to_child_answer,
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
    sourceReadingSessionId: int | None = Field(default=None, ge=1, le=MAX_ID, strict=True)
    expectedRevision: int | None = Field(default=None, ge=0, le=MAX_ID, strict=True)
    clientRequestId: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_continuation_fields(self):
        switch_fields = {"sourceReadingSessionId", "expectedRevision"}
        if self.model_fields_set & switch_fields and (
            self.sourceReadingSessionId is None or self.expectedRevision is None or self.clientRequestId is None
        ):
            raise ValueError("续写切换须同时提供 sourceReadingSessionId、expectedRevision 和 clientRequestId")
        if "clientRequestId" in self.model_fields_set and self.clientRequestId is None:
            raise ValueError("clientRequestId 不可显式传空")
        return self


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


def current_question(db: sqlite3.Connection, session: sqlite3.Row, *, after_id: int = 0,
                     before_id: int = MAX_ID + 1) -> sqlite3.Row | None:
    questions = db.execute(
        """SELECT id, event_type, output_text, response_json FROM interaction_events
           WHERE session_id = ? AND version_id = ? AND page_no = ? AND id > ? AND id < ?
             AND (event_type = 'QUESTION' OR
                  (event_type = 'FEEDBACK' AND feedback IN ('UNDERSTOOD', 'NOT_UNDERSTOOD')))
             AND result_status IN ('SUCCESS', 'FALLBACK') AND output_text IS NOT NULL
             AND COALESCE(strategy_type, json_extract(response_json, '$.data.strategyType'), '')
                 NOT IN ('SKIP', 'SAFETY_BLOCK')
           ORDER BY id DESC""",
        (session["id"], session["version_id"], session["current_page_no"], after_id, before_id),
    )
    return next((question for question in questions if question["output_text"].strip()), None)


def current_page_arrival_id(db: sqlite3.Connection, session: sqlite3.Row) -> int:
    # BRANCH_CHOSEN is recorded on the source page, so the boundary is the latest
    # successful arrival event in this session/version, not the latest one on this page.
    return db.execute(
        """SELECT COALESCE(MAX(id), 0) FROM interaction_events WHERE session_id = ? AND version_id = ?
           AND event_type IN ('STARTED', 'PAGE_CHANGED', 'BRANCH_CHOSEN') AND result_status = 'SUCCESS'""",
        (session["id"], session["version_id"]),
    ).fetchone()[0]


def current_parent_question(
    db: sqlite3.Connection, session: sqlite3.Row, *, arrival_id: int | None = None, before_id: int = MAX_ID + 1,
) -> sqlite3.Row | None:
    if arrival_id is None:
        arrival_id = current_page_arrival_id(db, session)
    question = current_question(db, session, after_id=arrival_id, before_id=before_id)
    # An answer without a follow-up does not replace an already-emitted question.
    followups = db.execute(
        """SELECT id, event_type, output_text FROM interaction_events
           WHERE session_id = ? AND version_id = ? AND page_no = ? AND id > ? AND id < ?
           AND event_type = 'ANSWER' AND result_status = 'SUCCESS' AND output_text IS NOT NULL
           ORDER BY id DESC""",
        (session["id"], session["version_id"], session["current_page_no"], question["id"] if question else arrival_id,
         before_id),
    )
    return next((followup for followup in followups if followup["output_text"].strip()), question)


def parent_visit_questions(db, session, arrival_id: int) -> tuple[str, ...]:
    # The full local exclusion list is not included in the model prompt.
    questions = db.execute(
        """SELECT output_text FROM interaction_events
           WHERE session_id = ? AND version_id = ? AND page_no = ? AND id > ? AND output_text IS NOT NULL
             AND ((event_type = 'ANSWER' AND result_status = 'SUCCESS')
                  OR ((event_type = 'QUESTION' OR (event_type = 'FEEDBACK' AND feedback IN ('UNDERSTOOD', 'NOT_UNDERSTOOD')))
                      AND result_status IN ('SUCCESS', 'FALLBACK')
                      AND COALESCE(strategy_type, json_extract(response_json, '$.data.strategyType'), '')
                          NOT IN ('SKIP', 'SAFETY_BLOCK')))
           GROUP BY output_text ORDER BY MAX(id)""",
        (session["id"], session["version_id"], session["current_page_no"], arrival_id),
    )
    return tuple(row["output_text"].strip() for row in questions if row["output_text"].strip())


def parent_visit_turns(db, session, arrival_id: int) -> tuple[RecentReadingTurn, ...]:
    rows = db.execute(
        """SELECT id, input_text, output_text, json_extract(response_json, '$.data.replyText') AS reply
           FROM interaction_events
           WHERE session_id = ? AND version_id = ? AND page_no = ? AND id > ?
             AND event_type = 'ANSWER' AND result_status = 'SUCCESS'
           ORDER BY id DESC LIMIT ?""",
        (session["id"], session["version_id"], session["current_page_no"], arrival_id, MAX_RECENT_TURNS),
    ).fetchall()
    turns = []
    for row in reversed(rows):
        # A follow-up on this ANSWER is a new question, never the question it answered.
        question = current_parent_question(db, session, arrival_id=arrival_id, before_id=row["id"])
        if question and row["input_text"]:
            reply = row["reply"] if isinstance(row["reply"], str) else ""
            if row["output_text"]:
                reply = reply.removesuffix(row["output_text"])
            turns.append(RecentReadingTurn(question["output_text"], row["input_text"], reply[:160]))
    return tuple(turns)


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
    data = {
        "readingSessionId": row["id"], "storyVersionId": row["version_id"],
        "currentPage": row["current_page_no"], "visitedPageNumbers": json.loads(row["visited_pages_json"]),
        "mode": row["mode"], "status": row["status"], "revision": row["revision"],
        "expiresAt": iso_utc(expiry) if expiry is not None else None,
    }
    source_session_id = row["source_reading_session_id"]
    if source_session_id is not None:
        data["sourceReadingSessionId"] = source_session_id
    if source_session_id is not None or row["carried_prefix_page_count"] > 0:
        data["startPageNumber"] = row["start_page_number"]
        data["carriedPrefixPageCount"] = row["carried_prefix_page_count"]
    return data


# Retired versions remain readable for existing sessions (history, reports and replay),
# while new session creation still rejects retired LIBRARY versions and WITHDRAWN blocks all access.
SESSION_ACCESS = """(v.status = 'PUBLISHED' OR
    (v.status = 'APPROVED_PRIVATE' AND b.owner_guardian_id = ?) OR
    (v.status = 'RETIRED' AND v.reviewed_by_id IS NOT NULL AND v.reviewed_at IS NOT NULL
       AND ((b.source_type = 'ORIGINAL' AND b.owner_guardian_id = ?)
         OR b.source_type = 'LIBRARY')))"""


def _switch_continuation_session(db, request: Request, owner: int, payload: SessionCreate):
    digest = request_digest("/api/v1/reading-sessions", payload.model_dump())
    profile = db.execute("SELECT 1 FROM child_profiles WHERE id = ? AND guardian_id = ?",
                         (payload.childProfileId, owner)).fetchone()
    if not profile:
        return error(request, 404, "RESOURCE_NOT_FOUND", "档案不可用")
    target = db.execute(
        """SELECT v.*, b.source_type AS storybook_source_type FROM story_versions v
           JOIN storybooks b ON b.id = v.storybook_id
           WHERE v.id = ? AND b.owner_guardian_id = ? AND b.source_type = 'ORIGINAL'""",
        (payload.storyVersionId, owner),
    ).fetchone()
    if not target or target["status"] == "WITHDRAWN":
        return error(request, 404, "RESOURCE_NOT_FOUND", "绘本版本不可用")
    src = db.execute(
        """SELECT s.*, v.status AS src_version_status, v.storybook_id AS source_book FROM reading_sessions s
           JOIN child_profiles p ON p.id = s.profile_id
           JOIN story_versions v ON v.id = s.version_id
           WHERE s.id = ? AND p.guardian_id = ?""",
        (payload.sourceReadingSessionId, owner),
    ).fetchone()
    if not src or src["src_version_status"] == "WITHDRAWN":
        return error(request, 404, "RESOURCE_NOT_FOUND", "源阅读会话不可用")
    replay = replay_execution(db, request, "SESSION_CREATE", owner, payload.clientRequestId, digest,
                              source_session_id=src["id"])
    if replay is not None:
        return replay

    def reject_switch(code, message):
        return fail_execution(db, request, owner, "SESSION_CREATE", owner, None, payload.clientRequestId,
                              digest, 409, code, message, source_session_id=src["id"])

    if target["source_session_id"] != src["id"] or target["storybook_id"] != src["source_book"]:
        return reject_switch("SESSION_CONFLICT", "目标版本与源会话或绘本不匹配")
    if src["profile_id"] != payload.childProfileId or src["mode"] != "PARENT_CHILD":
        return reject_switch("SESSION_CONFLICT", "仅支持同一儿童档案的亲子会话续写切换")
    already_switched = db.execute(
        "SELECT 1 FROM reading_sessions WHERE source_reading_session_id = ?", (src["id"],)
    ).fetchone()
    if already_switched or src["end_reason"] == "CONTINUED":
        return reject_switch("CONTINUATION_ALREADY_SWITCHED", "该阅读已切换，请查看续写阅读记录")
    if target["status"] != "APPROVED_PRIVATE":
        return reject_switch("SESSION_CONFLICT", "目标续写版本未批准")
    if (src["status"] != "IN_PROGRESS"
            or src["revision"] != payload.expectedRevision
            or src["version_id"] != target["source_version_id"]
            or src["revision"] != target["source_revision"]
            or src["current_page_no"] != target["source_page_no"]
            or json.loads(src["visited_pages_json"]) != json.loads(target["source_visited_pages_json"])):
        return reject_switch("CONTINUATION_STALE", "源阅读进度已变化，请从当前进度重新生成续写")
    resume_page = target["resume_page_no"]
    prefix = json.loads(target["source_visited_pages_json"])
    if (resume_page != len(prefix) + 1
            or not db.execute("SELECT 1 FROM story_pages WHERE version_id = ? AND page_no = ?",
                              (payload.storyVersionId, resume_page)).fetchone()):
        return reject_switch("SESSION_CONFLICT", "续写继续页不可用")

    # Snapshot and verify opening media (including the continuation resume page) before closing the source session.
    ensure_opening_media(db, target, page_no=resume_page)

    now = int(time.time() * 1000)
    closed = db.execute(
        """UPDATE reading_sessions SET status = 'FINISHED', end_reason = 'CONTINUED',
           ended_at = ?, revision = revision + 1
           WHERE id = ? AND status = 'IN_PROGRESS' AND revision = ?""",
        (now, src["id"], src["revision"]),
    )
    if closed.rowcount != 1:
        return reject_switch("CONTINUATION_STALE", "源阅读进度已变化，请从当前进度重新生成续写")
    visited = session_read_pages(src)
    summary, activities = answer_based_report(db, src, visited) or (fact_summary(visited), [])
    db.execute(
        """INSERT OR IGNORE INTO reading_reports (session_id, status, summary, activities_json, updated_at)
           VALUES (?, 'READY', ?, ?, ?)""",
        (src["id"], summary, json.dumps(activities, ensure_ascii=False), now),
    )
    # Carried prefix pages are renumbered 1..k in the continuation version, resuming at k+1.
    new_visited = [*range(1, len(prefix) + 1), resume_page]
    cursor = db.execute(
        """INSERT INTO reading_sessions
           (profile_id, version_id, current_page_no, mode, status, revision, visited_pages_json,
            source_reading_session_id, start_page_number, carried_prefix_page_count, created_at, started_at)
           VALUES (?, ?, ?, 'PARENT_CHILD', 'IN_PROGRESS', 1, ?, ?, ?, ?, ?, ?)""",
        (payload.childProfileId, payload.storyVersionId, resume_page, json.dumps(new_visited),
         src["id"], resume_page, len(prefix), now, now),
    )
    new_session_id = cursor.lastrowid
    yaya_db.pin_new_session(db, request.app.state.yaya_agent.settings, new_session_id, owner)
    new_row = db.execute(
        "SELECT *, NULL AS grant_expires_at FROM reading_sessions WHERE id = ?", (new_session_id,)
    ).fetchone()
    response = {"data": session_object(new_row), "requestId": request.state.request_id}
    db.execute(
        """INSERT INTO interaction_events
           (session_id, version_id, request_key, request_digest, response_json, page_no, event_type,
            result_status, revision_after, created_at)
           VALUES (?, ?, NULL, ?, ?, ?, 'STARTED', 'SUCCESS', 1, ?)""",
        (new_session_id, payload.storyVersionId, digest,
         json.dumps(response, ensure_ascii=False), resume_page, now),
    )
    record_execution(db, owner, "SESSION_CREATE", owner, None, payload.clientRequestId,
                     digest, now, 201, response, source_session_id=src["id"])
    return JSONResponse(status_code=201, content=response)


@router.post("", status_code=201)
def create_session(payload: SessionCreate, request: Request):
    try:
        with authorized_db(request, write=True, guardian_only=True) as (db, principal, failure):
            if failure:
                return failure
            owner = principal.guardian_id
            if payload.sourceReadingSessionId is not None:
                return _switch_continuation_session(db, request, owner, payload)
            key = payload.clientRequestId
            if key:
                digest = request_digest("/api/v1/reading-sessions", payload.model_dump())
                replay = replay_execution(db, request, "SESSION_CREATE", owner, key, digest, principal)
                if replay is not None:
                    return replay
            profile = db.execute("SELECT 1 FROM child_profiles WHERE id = ? AND guardian_id = ?",
                                 (payload.childProfileId, owner)).fetchone()
            version = db.execute(
                "SELECT v.*, b.source_type AS storybook_source_type FROM story_versions v "
                "JOIN storybooks b ON b.id = v.storybook_id "
                "WHERE v.id = ? AND " + VISIBLE +
                " AND EXISTS (SELECT 1 FROM story_pages p WHERE p.version_id = v.id AND p.page_no = 1)",
                (payload.storyVersionId, owner, True, owner),
            ).fetchone()
            if not profile or not version:
                return error(request, 404, "RESOURCE_NOT_FOUND", "档案或绘本不可用")
            ensure_opening_media(db, version)
            now = int(time.time() * 1000)
            cursor = db.execute(
                """INSERT INTO reading_sessions
                (profile_id, version_id, current_page_no, mode, status, created_at)
                VALUES (?, ?, 1, 'PARENT_CHILD', 'CREATED', ?)""",
                (payload.childProfileId, payload.storyVersionId, now),
            )
            row = db.execute("SELECT *, NULL AS grant_expires_at FROM reading_sessions WHERE id = ?",
                             (cursor.lastrowid,)).fetchone()
            yaya_db.pin_new_session(db, request.app.state.yaya_agent.settings, cursor.lastrowid, owner)
            response = {"data": session_object(row), "requestId": request.state.request_id}
            if key:
                record_execution(db, owner, "SESSION_CREATE", owner, None, key, digest, now, 201, response)
            return response
    except MediaDependencyError:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)
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


def session_event_object(event: sqlite3.Row, *, include_answer: bool) -> dict:
    data = {"interactionEventId": event["id"], "eventType": event["event_type"], "pageNumber": event["page_no"]}
    if include_answer and event["event_type"] == "ANSWER":
        if event["input_text"] is not None:
            data["inputText"] = event["input_text"]
        if event["saved_id"] == event["id"]:
            if event["reply_text"]:
                data["replyText"] = event["reply_text"]
            followup = event["followup_question"] or event["output_text"]
            # Earlier versions stored the follow-up only in output_text and as a reply suffix.
            if (isinstance(event["reply_text"], str) and isinstance(followup, str)
                    and followup and event["reply_text"].endswith(followup)):
                data["followupQuestion"] = followup
    # Only project a finalized guidance response; older rows may contain unverified text.
    if (event["event_type"] in ("QUESTION", "FEEDBACK", "ASK") and event["strategy_type"]
            and event["saved_id"] == event["id"]):
        data.update(outputText=event["output_text"], strategyType=event["strategy_type"], revision=event["revision_after"])
        if event["feedback"] is not None:
            data["feedback"] = event["feedback"]
    if include_answer and event["saved_id"] == event["id"] and event["agent_receipt"]:
        data["agentReceipt"] = json.loads(event["agent_receipt"])
    return data


@router.get("/{session_id}")
def get_session(request: Request, response: Response, session_id: int = Path(ge=1, le=MAX_ID),
                view: Literal["reading", "deletion-status"] = "reading"):
    deletion_check = view == "deletion-status"
    try:
        with authorized_db(
            request, guardian_only=deletion_check, settle_guardian=not deletion_check
        ) as (db, principal, failure):
            if failure:
                return failure
            if deletion_check:
                target = owned_deletion_target(db, session_id, principal.guardian_id)
                return JSONResponse(
                    content={"data": {"exists": target is not None}, "requestId": request.state.request_id},
                    headers={"Cache-Control": "no-store"},
                )
            row = owned_session(db, session_id, principal)
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            event_query = """SELECT id, event_type, page_no, feedback, input_text, output_text, strategy_type, revision_after,
                                    json_extract(response_json, '$.data.replyText') AS reply_text,
                                    json_extract(response_json, '$.data.followupQuestion') AS followup_question,
                                    json_extract(response_json, '$.data.agentReceipt') AS agent_receipt,
                                    json_extract(response_json, '$.data.interactionEventId') AS saved_id
                             FROM interaction_events WHERE session_id = ?"""
            event_params = [session_id]
            if principal.role == "CHILD":
                event_query += " AND page_no = ?"
                event_params.append(row["current_page_no"])
            events = db.execute(event_query + " ORDER BY id", event_params).fetchall()
            report = None
            session = session_object(row)
            if principal.role == "GUARDIAN":
                session.update(childProfileId=row["profile_id"], sourceType=row["source_type"])
                report_row = db.execute("SELECT * FROM reading_reports WHERE session_id = ?", (session_id,)).fetchone()
                report = report_object(session_id, report_row)
            data = {"session": session,
                    "events": [session_event_object(event, include_answer=principal.role == "GUARDIAN") for event in events],
                    "report": report}
            if principal.role == "CHILD" and row["status"] == "IN_PROGRESS":
                data["answerOptions"] = child_answer_options(db, row)
            response.headers["Cache-Control"] = "private, no-store"
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


def owned_deletion_target(db, session_id: int, guardian_id: int):
    return db.execute(
        """SELECT s.id, s.grant_id FROM reading_sessions s
           JOIN child_profiles p ON p.id = s.profile_id
           WHERE s.id = ? AND p.guardian_id = ?""",
        (session_id, guardian_id),
    ).fetchone()


@router.delete("/{session_id}", status_code=204)
def delete_session(request: Request, session_id: int = Path(ge=1, le=MAX_ID)):
    """API-014: delete a guardian-owned reading session, its derived rows, and revoke its active grant."""
    try:
        with authorized_db(request, write=True, guardian_only=True, settle_guardian=False) as (db, principal, failure):
            if failure:
                return failure
            row = owned_deletion_target(db, session_id, principal.guardian_id)
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
            """SELECT s.*, g.expires_at AS grant_expires_at, b.source_type FROM reading_sessions s
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
        """SELECT s.*, g.expires_at AS grant_expires_at, b.source_type FROM reading_sessions s
        LEFT JOIN child_grants g ON g.id = s.grant_id
        JOIN child_profiles p ON p.id = s.profile_id
        JOIN story_versions v ON v.id = s.version_id
        JOIN storybooks b ON b.id = v.storybook_id
        WHERE s.id = ? AND p.guardian_id = ? AND """ + SESSION_ACCESS,
        (session_id, principal.guardian_id, principal.guardian_id, principal.guardian_id),
    ).fetchone()


def replay_execution(db, request: Request, scope_type: str, scope_id: int, client_request_id: str,
                     digest: str, principal: Principal | None = None, *, page_version_id: int | None = None,
                     source_session_id: int | None = None):
    """Shared request_executions lookup for SESSION_EVENT and SESSION_CREATE scopes."""
    previous = db.execute("""SELECT id, request_digest, status, response_status, response_body, deadline_at, started_at,
                                 source_reading_session_id
                          FROM request_executions WHERE scope_type = ?
                          AND scope_id = ? AND client_request_id = ?""",
                          (scope_type, scope_id, client_request_id)).fetchone()
    if not previous:
        return None
    if previous["request_digest"] != digest:
        return error(request, 409, "IDEMPOTENCY_CONFLICT", "请求标识已用于其他操作")
    if source_session_id is not None and previous["source_reading_session_id"] is None:
        # An older failed response has no source ID. A matching replay supplies its verified association.
        db.execute("UPDATE request_executions SET source_reading_session_id = ? WHERE id = ?",
                   (source_session_id, previous["id"]))
    status = previous["status"]
    if status == "RUNNING":
        now = int(time.time() * 1000)
        if previous["deadline_at"] > now:
            return error(request, 409, "REQUEST_IN_PROGRESS", "请求正在处理", retryable=True, headers={"Retry-After": "1"})
        if scope_type == "SESSION_EVENT" and previous["response_status"] == 201:
            # API-015 already committed the answer and its rule reply before calling the model.
            db.execute("UPDATE request_executions SET status = 'SUCCEEDED', completed_at = ? WHERE id = ?",
                       (now, previous["id"]))
            status = "SUCCEEDED"
        else:
            failure = error(request, 504, "REQUEST_OUTCOME_UNKNOWN", "请求结果未知，请先查询会话")
            db.execute("""UPDATE request_executions SET status = 'UNKNOWN', completed_at = ?,
                          response_status = 504, response_body = ? WHERE scope_type = ?
                          AND scope_id = ? AND client_request_id = ? AND status = 'RUNNING'
                          AND deadline_at <= ?""",
                       (now, failure.body.decode('utf-8'), scope_type, scope_id, client_request_id, now))
            return failure
    if status == "UNKNOWN":
        return error(request, 504, "REQUEST_OUTCOME_UNKNOWN", "请求结果未知，请先查询会话")
    if (status == "SUCCEEDED" and page_version_id is not None
            and readable_story_version(db, principal, page_version_id) is None):
        return error(request, 404, "RESOURCE_NOT_FOUND", "绘本不可用")
    body = json.loads(previous["response_body"])
    if status == "SUCCEEDED" and scope_type == "SESSION_CREATE" and principal is not None:
        target = body["data"]["readingSessionId"]
        session = owned_session(db, target, principal)
        if session is None or session["created_at"] != previous["started_at"]:
            return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
    if status == "SUCCEEDED" and scope_type == "STORY_GENERATION":
        target = body.get("data", {}).get("storyVersionId")
        if target is None or owned_generation_version(db, scope_id, target) is None:
            return error(request, 404, "RESOURCE_NOT_FOUND", "生成的绘本不可用")
    body["requestId"] = request.state.request_id
    if principal is not None and principal.role == "CHILD" and isinstance(body.get("data"), dict):
        if "report" in body["data"]:
            body["data"]["report"] = None
        body["data"].pop("replyText", None)
        body["data"].pop("followupQuestion", None)
        body["data"].pop("agentReceipt", None)
    elif status == "SUCCEEDED" and scope_type == "SESSION_EVENT" and principal is not None and principal.role == "GUARDIAN":
        data = body.get("data", {})
        reply_text = data.get("replyText")
        if isinstance(reply_text, str) and not data.get("followupQuestion"):
            legacy = db.execute(
                """SELECT output_text FROM interaction_events WHERE id = ? AND session_id = ? AND request_key = ?
                   AND event_type = 'ANSWER' AND result_status = 'SUCCESS'""",
                (data.get("interactionEventId"), scope_id, client_request_id),
            ).fetchone()
            if legacy and legacy["output_text"] and reply_text.endswith(legacy["output_text"]):
                data["followupQuestion"] = legacy["output_text"]
    return JSONResponse(status_code=200 if status == "SUCCEEDED" else previous["response_status"],
                        content=body)


def record_execution(db, owner: int, scope_type: str, scope_id: int, session_id: int | None,
                     client_request_id: str, digest: str, now: int, status_code: int, response: dict, *,
                     status: str = "SUCCEEDED", execution_token: str | None = None,
                     source_session_id: int | None = None) -> str:
    token = execution_token or str(uuid4())
    db.execute("""INSERT INTO request_executions
        (owner_account_id, session_id, scope_type, scope_id, client_request_id, request_digest,
         status, execution_token, started_at, deadline_at, completed_at, response_status, response_body,
         source_reading_session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (owner, session_id, scope_type, scope_id, client_request_id, digest, status, token, now, now + 25000,
         None if status == "RUNNING" else now, status_code, json.dumps(response, ensure_ascii=False), source_session_id))
    return token


def fail_execution(db, request: Request, owner: int, scope_type: str, scope_id: int, session_id: int | None,
                   client_request_id: str, digest: str, status_code: int, code: str, message: str, *,
                   source_session_id: int | None = None):
    failure = error(request, status_code, code, message)
    record_execution(db, owner, scope_type, scope_id, session_id, client_request_id, digest,
                     int(time.time() * 1000), status_code, json.loads(failure.body), status="FAILED",
                     source_session_id=source_session_id)
    return failure


def replay_session_event(db, request: Request, session_id: int, client_event_id: str, digest: str,
                         principal: Principal | None = None, *, page_version_id: int | None = None):
    return replay_execution(db, request, "SESSION_EVENT", session_id, client_event_id, digest, principal,
                            page_version_id=page_version_id)


def record_session_event(db, owner: int, session_id: int, client_event_id: str, digest: str,
                         now: int, status_code: int, response: dict, *,
                         status: str = "SUCCEEDED", execution_token: str | None = None) -> str:
    return record_execution(db, owner, "SESSION_EVENT", session_id, session_id, client_event_id, digest,
                            now, status_code, response, status=status, execution_token=execution_token)


def fail_session_event(db, request: Request, owner: int, session_id: int, client_event_id: str,
                       digest: str, status_code: int, code: str, message: str):
    return fail_execution(db, request, owner, "SESSION_EVENT", session_id, session_id, client_event_id,
                          digest, status_code, code, message)


@router.post("/{session_id}/events", status_code=201)
async def add_reading_event(payload: ReadingEvent, request: Request, session_id: int = Path(ge=1, le=MAX_ID)):
    model = getattr(request.app.state, "model_adapter", None)
    saved = await run_in_threadpool(save_reading_event, request, session_id, payload, model is not None)
    if isinstance(saved, JSONResponse):
        return saved
    response, token, context = saved
    if isinstance(context, yaya.Context):
        try:
            reply = await request.app.state.yaya_agent.turn(
                context, request_id=request.state.request_id, guardian=request.state.yaya_guardian,
                session=session_id, disconnected=request.is_disconnected,
            )
        except asyncio.CancelledError:
            await asyncio.shield(run_in_threadpool(finalize_answer_reply, request, session_id, payload, response,
                                                   token, yaya.fallback("CLIENT_DISCONNECTED")))
            raise
    else:
        reply = await generate_answer_reply(model, **context)
    for _ in range(2):
        try:
            return await run_in_threadpool(finalize_answer_reply, request, session_id, payload, response, token, reply)
        except sqlite3.Error:
            continue
    return error(request, 503, "DEPENDENCY_UNAVAILABLE", "回答已保存，请核对会话进度", retryable=True)


def save_reading_event(request: Request, session_id: int, payload: ReadingEvent, model_available: bool):
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
                if valid:
                    question = (current_parent_question(db, row)
                                if principal.role == "GUARDIAN" and row["mode"] == "PARENT_CHILD"
                                else current_question(db, row))
                    if question is None:
                        return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                                  digest, 409, "PROMPT_REQUIRED", "当前页尚未有引导问题，无法提交反馈")
            if not valid:
                return fail_session_event(db, request, principal.guardian_id, session_id, payload.clientEventId,
                                          digest, 409, "SESSION_CONFLICT", "当前状态或页面不允许此操作")
            context = None
            if payload.eventType == "ANSWER" and principal.role == "GUARDIAN" and row["mode"] == "PARENT_CHILD":
                if yaya_db.pinned(db, session_id):
                    arrival = current_page_arrival_id(db, row)
                    context = request.app.state.yaya_agent.reading_context(db, row, payload.inputText, arrival, parent_visit_questions(db, row, arrival))
                    request.state.yaya_guardian = principal.guardian_id
                else:
                    context = answer_context(db, row, payload.inputText)
            revision = row["revision"] + 1
            db.execute("""UPDATE reading_sessions SET status = 'IN_PROGRESS', current_page_no = ?,
                       started_at = COALESCE(started_at, ?), revision = ?, visited_pages_json = ? WHERE id = ?""",
                       (next_page_no, now, revision, json.dumps(visited), session_id))
            cursor = db.execute("""INSERT INTO interaction_events
                (session_id, version_id, request_key, request_digest, response_json, page_no, event_type,
                 feedback, input_text, result_status, revision_after, created_at)
                VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, 'SUCCESS', ?, ?)""",
                (session_id, row["version_id"], payload.clientEventId, digest, payload.pageNumber,
                 payload.eventType, payload.feedback,
                 None if isinstance(context, yaya.Context) and context.input_projection.redacted else payload.inputText,
                 revision, now))
            response = {"data": {"interactionEventId": cursor.lastrowid, "revision": revision}, "requestId": request.state.request_id}
            if isinstance(context, yaya.Context):
                provisional = yaya.local_turn(context) or yaya.fallback("DEPENDENCY_UNAVAILABLE")
                response["data"].update(replyText=provisional.reply, agentReceipt=provisional.public(
                    input_redacted=context.input_projection.redacted))
            elif context is not None:
                latest = context["recent_turns"][-1] if context["recent_turns"] else None
                question_answered = bool(latest and latest.question == context["question"])
                response["data"]["replyText"] = reply_to_child_answer(
                    context["page_text"], payload.inputText, context["question"],
                    age_band=context["age_band"], question_answered=question_answered,
                )
                if (not model_available or not context["question"] or answer_is_unsafe(payload.inputText)
                        or close_reading_reply(payload.inputText, context["age_band"], question_answered=question_answered)
                        or not any(char.isalnum() for char in payload.inputText)):
                    context = None
            db.execute("UPDATE interaction_events SET response_json = ? WHERE id = ?",
                       (json.dumps(response, ensure_ascii=False), cursor.lastrowid))
            token = record_session_event(db, principal.guardian_id, session_id, payload.clientEventId, digest, now,
                                         201, response, status="RUNNING" if context is not None else "SUCCEEDED")
            if context is not None:
                return response, token, context
            return JSONResponse(status_code=201, content=response)
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")


def answer_context(db, session, answer: str) -> dict:
    page = db.execute(
        """SELECT p.body_text, v.title, v.topics_json, c.age_band FROM story_pages p
           JOIN story_versions v ON v.id = p.version_id JOIN child_profiles c ON c.id = ?
           WHERE p.version_id = ? AND p.page_no = ?""",
        (session["profile_id"], session["version_id"], session["current_page_no"]),
    ).fetchone()
    arrival_id = current_page_arrival_id(db, session)
    question = current_parent_question(db, session, arrival_id=arrival_id)
    followups = db.execute(
        """SELECT TRIM(output_text) AS question FROM interaction_events WHERE session_id = ? AND version_id = ? AND page_no = ?
           AND event_type = 'ANSWER' AND result_status = 'SUCCESS' AND id > ?
           AND NULLIF(TRIM(output_text), '') IS NOT NULL
           GROUP BY TRIM(output_text) ORDER BY MAX(id) DESC LIMIT ?""",
        (session["id"], session["version_id"], session["current_page_no"], arrival_id, MAX_ANSWER_FOLLOWUPS),
    ).fetchall()
    recent_turns = parent_visit_turns(db, session, arrival_id)
    visited = json.loads(session["visited_pages_json"])
    earlier = [number for number in visited[:-1] if number != session["current_page_no"]][-3:]
    texts = {}
    if earlier:
        texts = dict(db.execute(
            "SELECT page_no, body_text FROM story_pages WHERE version_id = ? AND page_no IN (%s)"
            % ",".join("?" * len(earlier)), (session["version_id"], *earlier),
        ))
    return dict(
        page_text=page["body_text"] if page else "", question=question["output_text"] if question else "", answer=answer,
        title=page["title"] if page else "", page_number=session["current_page_no"],
        age_band=page["age_band"] if page else "", topics=json.loads(page["topics_json"]) if page else [],
        earlier_pages=[texts[number][:ANSWER_EARLIER_PAGE_CHARS + ANSWER_PAGE_LOOKAHEAD]
                       for number in earlier if number in texts],
        used_followups=[item["question"] for item in followups],
        seen_questions=parent_visit_questions(db, session, arrival_id),
        prior_reply=recent_turns[-1].reply if recent_turns else "", recent_turns=recent_turns,
    )


def finalize_answer_reply(request: Request, session_id: int, payload: ReadingEvent, response: dict, token: str, reply):
    with authorized_db(request, write=True, guardian_only=True) as (db, principal, failure):
        if failure:
            return failure
        session = owned_session(db, session_id, principal)
        if session is None:
            return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
        execution = db.execute(
            """SELECT id, status, deadline_at, response_body FROM request_executions
               WHERE session_id = ? AND client_request_id = ? AND execution_token = ?""",
            (session_id, payload.clientEventId, token),
        ).fetchone()
        if execution is None:
            return error(request, 409, "SESSION_CONFLICT", "回答状态已变化，请核对进度")
        if execution["status"] != "RUNNING":
            return JSONResponse(status_code=201, content=json.loads(execution["response_body"]))
        now = int(time.time() * 1000)
        finalized = response
        if (reply is not None and now < execution["deadline_at"] and session["status"] == "IN_PROGRESS"
                and session["mode"] == "PARENT_CHILD" and session["current_page_no"] == payload.pageNumber
                and session["revision"] == response["data"]["revision"]):
            if isinstance(reply, yaya.Outcome):
                followup = reply.followup
                reply_text = reply.reply + (f"\n{followup}" if followup else "")
            else:
                reply_text, followup = reply
            finalized = {**response, "data": {**response["data"], "replyText": reply_text}}
            if isinstance(reply, yaya.Outcome):
                finalized["data"]["agentReceipt"] = reply.public(
                    input_redacted=response["data"]["agentReceipt"].get("inputRedacted", False))
            if followup:
                # A fresh question invalidates feedback based on the provisional answer snapshot.
                revision = session["revision"] + 1
                finalized["data"]["followupQuestion"] = followup
                finalized["data"]["revision"] = revision
                db.execute("UPDATE reading_sessions SET revision = ? WHERE id = ?", (revision, session_id))
            db.execute("UPDATE interaction_events SET response_json = ?, output_text = ?, revision_after = ? WHERE id = ?",
                       (json.dumps(finalized, ensure_ascii=False), followup or None,
                        finalized["data"]["revision"], response["data"]["interactionEventId"]))
        db.execute("UPDATE request_executions SET status = 'SUCCEEDED', completed_at = ?, response_body = ? WHERE id = ?",
                   (now, json.dumps(finalized, ensure_ascii=False), execution["id"]))
        return JSONResponse(status_code=201, content=finalized)


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
            next_page_data = page_object(db, next_page)
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
                    "nextPage": next_page_data,
                    "visitedPageNumbers": visited,
                },
                "requestId": request.state.request_id,
            }
            db.execute("UPDATE interaction_events SET response_json = ? WHERE id = ?",
                       (json.dumps(response, ensure_ascii=False), cursor.lastrowid))
            record_session_event(db, principal.guardian_id, session_id, payload.clientEventId, digest, now, 201, response)
            return JSONResponse(status_code=201, content=response)
    except MediaDependencyError:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)
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
                replay.headers["Cache-Control"] = "private, no-store"
                return replay
            now = int(time.time() * 1000)
            generation = None
            generation_token = None
            answered_report = None
            prompts = []
            if row["status"] == "FINISHED":
                # New key after finish: existing terminal state and current report, no model call.
                report = db.execute("SELECT * FROM reading_reports WHERE session_id = ?", (session_id,)).fetchone()
            elif row["status"] == "IN_PROGRESS" and row["revision"] == payload.expectedRevision:
                visited = session_read_pages(row)
                db.execute("""UPDATE reading_sessions SET status = 'FINISHED', end_reason = 'USER_FINISH',
                           ended_at = ?, revision = revision + 1 WHERE id = ? AND revision = ?""",
                           (now, session_id, row["revision"]))
                answered_report = answer_based_report(db, row, visited)
                facts = fact_summary(visited)
                summary, activities = answered_report or (facts, [])
                generation_token = str(uuid4())
                pages = list(dict.fromkeys(visited))
                page_texts = dict(db.execute(
                    "SELECT page_no, body_text FROM story_pages WHERE version_id = ? AND page_no IN (%s)"
                    % ",".join("?" * len(pages)), (row["version_id"], *pages))) if pages else {}
                prompts = [event[0][:150] for event in reversed(db.execute(
                    """SELECT output_text FROM interaction_events WHERE session_id = ? AND version_id = ?
                       AND event_type IN ('QUESTION', 'FEEDBACK') AND result_status IN ('SUCCESS', 'FALLBACK')
                       AND trim(output_text) <> '' ORDER BY id DESC LIMIT 4""",
                    (session_id, row["version_id"]),
                ).fetchall())]
                generation = (facts, [page_texts[page] for page in pages if page in page_texts])
                db.execute("""INSERT INTO reading_reports (session_id, status, summary, activities_json, updated_at)
                           VALUES (?, ?, ?, ?, ?)""",
                           (session_id, "PENDING", summary, json.dumps(activities, ensure_ascii=False), now))
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
                            generation_token=generation_token, prompts=prompts, answer_report=answered_report)
    return JSONResponse(status_code=200, content=response, headers={"Cache-Control": "private, no-store"})
