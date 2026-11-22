"""API-016/017: claim an event, generate outside the write transaction, then commit its result."""

import json
import logging
import sqlite3
import time
from base64 import b64encode
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.api.v1.auth import authorized_db, error
from app.api.v1.reading_sessions import (
    CHILD_SHORT_ANSWERS, MAX_ID, current_page_arrival_id, current_parent_question, current_question, fail_session_event,
    owned_session, record_session_event, replay_session_event, request_digest,
)
from app.schemas.agent import AgentResult
from app.services.reading_agent import CHILD_QUESTIONS, run_agent, answer_question
from app.services.story_media import MediaDependencyError, delivery_image, read_pinned_asset_bytes

router = APIRouter(prefix="/api/v1/reading-sessions")
logger = logging.getLogger("zhiya.guidance")
MAX_MODEL_IMAGE_BYTES = 512 * 1024


class PageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pageNumber: int = Field(ge=1, le=MAX_ID, strict=True)
    expectedRevision: int = Field(ge=0, le=MAX_ID, strict=True)
    clientEventId: str = Field(min_length=1, max_length=64)


class GuidanceRequest(PageRequest):
    feedback: Literal["INITIAL", "UNDERSTOOD", "NOT_UNDERSTOOD", "SKIPPED"]


class QuestionRequest(PageRequest):
    inputText: str = Field(min_length=1, max_length=200)

    @field_validator("inputText")
    @classmethod
    def confirmed_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请提交已确认的问题")
        return value.strip()


def matches_page(session, payload: PageRequest) -> bool:
    return (session["status"] == "IN_PROGRESS"
            and session["current_page_no"] == payload.pageNumber
            and session["revision"] == payload.expectedRevision)


def save_event(db, request, session, payload, digest, result: AgentResult, now):
    revision = session["revision"] + 1
    ask = isinstance(payload, QuestionRequest)
    feedback = None if ask or payload.feedback == "INITIAL" else payload.feedback
    event_type = "ASK" if ask else "FEEDBACK" if feedback else "QUESTION"
    status = {"GENERATED": "SUCCESS", "FALLBACK": "FALLBACK", "BLOCKED": "FAILED"}[result.status]
    cursor = db.execute(
        """INSERT INTO interaction_events
           (session_id, version_id, request_key, request_digest, response_json, page_no, event_type,
            feedback, input_text, output_text, strategy_type, result_status, revision_after, created_at)
           VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session["id"], session["version_id"], payload.clientEventId, digest, payload.pageNumber,
         event_type, feedback, payload.inputText if ask and result.status != "BLOCKED" else None,
         result.outputText, result.strategyType, status, revision, now),
    )
    event_id = cursor.lastrowid
    response = {"data": {**result.model_dump(), "interactionEventId": event_id, "revision": revision},
                "requestId": request.state.request_id}
    if not ask and session["mode"] == "CHILD_SOLO" and result.status != "BLOCKED" and result.outputText:
        response["data"]["answerOptions"] = list(CHILD_SHORT_ANSWERS)
    db.execute("UPDATE interaction_events SET response_json = ? WHERE id = ?",
               (json.dumps(response, ensure_ascii=False), event_id))
    db.executemany("INSERT INTO event_evidence (event_id, knowledge_item_id, rank_no) VALUES (?, ?, ?)",
                   [(event_id, source.knowledgeItemId, rank) for rank, source in enumerate(result.sources, 1)])
    db.execute("UPDATE reading_sessions SET revision = ? WHERE id = ?", (revision, session["id"]))
    return response


def finish_guidance(request, session_id, payload, digest, token, result: AgentResult | None):
    with authorized_db(request, write=True) as (db, principal, failure):
        session = None if failure else owned_session(db, session_id, principal)
        if not failure:
            if session is None:
                failure = error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            elif not matches_page(session, payload):
                failure = error(request, 409, "SESSION_CONFLICT", "当前状态或页面不允许此操作")
            elif result is None:
                failure = error(request, 503, "DEPENDENCY_UNAVAILABLE", "引导服务暂不可用，可以继续阅读或跳过本页")

        # Only this attempt may finish. Its response, event, evidence and revision commit together.
        now = int(time.time() * 1000)
        execution = db.execute(
            """UPDATE request_executions SET status = ?, completed_at = ?, response_status = ?, response_body = ?
               WHERE scope_type = 'SESSION_EVENT' AND scope_id = ? AND client_request_id = ?
               AND execution_token = ? AND status = 'RUNNING' AND deadline_at > ? RETURNING id""",
            ("FAILED" if failure else "SUCCEEDED", now, failure.status_code if failure else 201,
             failure.body.decode() if failure else "{}", session_id, payload.clientEventId, token, now),
        ).fetchone()
        if execution is None:
            if session is None:
                return failure
            # A deleted session/key can be reused. Never replay a replacement attempt to the old caller.
            same_attempt = db.execute(
                """SELECT 1 FROM request_executions WHERE scope_type = 'SESSION_EVENT'
                   AND scope_id = ? AND client_request_id = ? AND execution_token = ?""",
                (session_id, payload.clientEventId, token),
            ).fetchone()
            if same_attempt:
                return replay_session_event(db, request, session_id, payload.clientEventId, digest, principal)
            return error(request, 504, "REQUEST_OUTCOME_UNKNOWN", "请求结果未知，请先查询会话")
        if failure:
            return failure

        response = save_event(db, request, session, payload, digest, result, now)
        db.execute("UPDATE request_executions SET response_body = ? WHERE id = ?",
                   (json.dumps(response, ensure_ascii=False), execution["id"]))
        return JSONResponse(status_code=201, content=response)


@router.post("/{session_id}/guidance", status_code=201)
async def get_guidance(payload: GuidanceRequest, request: Request, session_id: int = Path(ge=1, le=MAX_ID)):
    return await process_question_or_guidance(payload, request, session_id)


@router.post("/{session_id}/questions", status_code=201)
async def ask_question(payload: QuestionRequest, request: Request, session_id: int = Path(ge=1, le=MAX_ID)):
    return await process_question_or_guidance(payload, request, session_id)


async def process_question_or_guidance(payload: GuidanceRequest | QuestionRequest, request: Request, session_id: int):
    ask = isinstance(payload, QuestionRequest)
    endpoint = "questions" if ask else "guidance"
    digest = request_digest(f"/api/v1/reading-sessions/{session_id}/{endpoint}", payload.model_dump())
    key = payload.clientEventId
    model = getattr(request.app.state, "model_adapter", None)
    page_image = None
    try:
        with authorized_db(request, write=True) as (db, principal, failure):
            if failure:
                return failure
            session = owned_session(db, session_id, principal)
            if session is None:
                return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            if ask and principal.role == "CHILD" and payload.inputText not in CHILD_QUESTIONS:
                return error(request, 422, "VALIDATION_ERROR", "请使用本页提供的短问题")
            replay = replay_session_event(db, request, session_id, key, digest, principal)
            if replay is not None:
                return replay
            owner = principal.guardian_id
            if not matches_page(session, payload):
                return fail_session_event(db, request, owner, session_id, key, digest, 409,
                                          "SESSION_CONFLICT", "当前状态或页面不允许此操作")
            previous_question = confirmed_answer = ""
            if not ask and payload.feedback in ("UNDERSTOOD", "NOT_UNDERSTOOD"):
                parent_reading = principal.role == "GUARDIAN" and session["mode"] == "PARENT_CHILD"
                if parent_reading:
                    arrival_id = current_page_arrival_id(db, session)
                    question = current_parent_question(db, session, arrival_id=arrival_id)
                else:
                    question = current_question(db, session)
                if question is None:
                    return fail_session_event(db, request, owner, session_id, key, digest, 409,
                                              "PROMPT_REQUIRED", "当前页尚未有引导问题")
                previous_question = question["output_text"]
                if parent_reading:
                    if question["event_type"] == "ANSWER":
                        # A generated follow-up and the answer that prompted it share one event.
                        confirmed_answer = question["input_text"] or ""
                    else:
                        after_id = max(question["id"], arrival_id)
                        answer = db.execute(
                            """SELECT input_text FROM interaction_events
                               WHERE session_id = ? AND version_id = ? AND page_no = ? AND id > ?
                                 AND event_type = 'ANSWER' AND result_status = 'SUCCESS'
                               ORDER BY id DESC LIMIT 1""",
                            (session_id, session["version_id"], session["current_page_no"], after_id),
                        ).fetchone()
                        if answer:
                            confirmed_answer = answer["input_text"] or ""

            now = int(time.time() * 1000)
            if not ask and payload.feedback == "SKIPPED":
                skipped = AgentResult(status="FALLBACK", strategyType="SKIP", outputText="")
                response = save_event(db, request, session, payload, digest, skipped, now)
                record_session_event(db, owner, session_id, key, digest, now, 201, response)
                return JSONResponse(status_code=201, content=response)

            context = db.execute(
                """SELECT p.age_band, v.topics_json, page.body_text, page.image_ref FROM reading_sessions s
                   JOIN child_profiles p ON p.id = s.profile_id JOIN story_versions v ON v.id = s.version_id
                   JOIN story_pages page ON page.version_id = v.id AND page.page_no = s.current_page_no
                   WHERE s.id = ?""", (session_id,),
            ).fetchone()
            if context is None:
                return fail_session_event(db, request, owner, session_id, key, digest, 404,
                                          "RESOURCE_NOT_FOUND", "当前页不可用")
            if (principal.role == "GUARDIAN" and session["mode"] == "PARENT_CHILD"
                    and getattr(model, "supports_page_images", False) is True
                    and context["image_ref"] not in (None, "", "none", "/assets/placeholder.png")):
                version = db.execute("SELECT * FROM story_versions WHERE id = ?", (session["version_id"],)).fetchone()
                try:
                    page_image = read_pinned_asset_bytes(db, version, payload.pageNumber)
                except MediaDependencyError:
                    # Guidance can still use the already authorized page text.
                    pass
            earlier_pages = []
            if not ask:
                path = json.loads(session["visited_pages_json"])
                earlier = [number for number in path if number != payload.pageNumber][-3:]
                if earlier:
                    texts = dict(db.execute(
                        "SELECT page_no, body_text FROM story_pages WHERE version_id = ? AND page_no IN (%s)"
                        % ",".join("?" * len(earlier)), (session["version_id"], *earlier),
                    ))
                    earlier_pages = [texts[number][:600] for number in earlier if number in texts]
            token = str(uuid4())
            db.execute(
                """INSERT INTO request_executions
                   (owner_account_id, session_id, scope_type, scope_id, client_request_id, request_digest,
                    status, execution_token, started_at, deadline_at)
                   VALUES (?, ?, 'SESSION_EVENT', ?, ?, ?, 'RUNNING', ?, ?, ?)""",
                (owner, session_id, session_id, key, digest, token, now, now + 25_000),
            )
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)

    try:
        context_args = dict(age_band=context["age_band"], topics=json.loads(context["topics_json"]),
                            page_text=context["body_text"])
        if page_image is not None:
            try:
                data, media_type = await run_in_threadpool(delivery_image, *page_image, "thumbnail")
            except (OSError, ValueError):
                pass
            else:
                if len(data) <= MAX_MODEL_IMAGE_BYTES and media_type in ("image/png", "image/jpeg", "image/webp"):
                    context_args["page_image_url"] = f"data:{media_type};base64,{b64encode(data).decode('ascii')}"
        if ask:
            result = await answer_question(model, input_text=payload.inputText, **context_args)
        else:
            result = await run_agent(model, feedback=payload.feedback,
                                     previous_question=previous_question, child_mode=session["mode"] == "CHILD_SOLO",
                                     confirmed_answer=confirmed_answer, earlier_pages=earlier_pages,
                                     **context_args)
    except Exception as exc:
        # Keep private prompts and provider messages out of logs; record a definite failure for this key.
        logger.warning("guidance generation failed request_id=%s error=%s", request.state.request_id, type(exc).__name__)
        result = None

    # Retry only the short save transaction. The already obtained model result stays in memory.
    for _ in range(2):
        try:
            return finish_guidance(request, session_id, payload, digest, token, result)
        except sqlite3.Error:
            continue
    return error(request, 503, "DEPENDENCY_UNAVAILABLE", "暂时无法保存引导结果，请先查询会话", retryable=True)
