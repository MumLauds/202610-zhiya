from fastapi import APIRouter, Request, Path
from fastapi.responses import JSONResponse
import time
from pydantic import BaseModel, Field
from app.api.v1.auth import authorized_db, error
from app.api.v1.reading_sessions import owned_session, replay_session_event, record_session_event, request_digest
from app.services.reading_agent import run_agent
from app.services.knowledge_service import search_knowledge  # 引入查库

router = APIRouter(prefix="/api/v1/reading-sessions")

class GuidanceRequest(BaseModel):
    pageNumber: int = Field(ge=1)
    feedback: str = Field(pattern="^(INITIAL|UNDERSTOOD|NOT_UNDERSTOOD|SKIPPED)$")
    expectedRevision: int = Field(ge=0)
    clientEventId: str = Field(min_length=1, max_length=64)

@router.post("/{session_id}/guidance", status_code=201)
async def get_guidance(payload: GuidanceRequest, request: Request, session_id: int = Path(ge=1)):
    digest = request_digest(f"/api/v1/reading-sessions/{session_id}/guidance", payload.model_dump())
    
    # 1. 短事务：查库、校验、查知识、获取页面文本
    try:
        with authorized_db(request, write=True) as (db, principal, failure):
            if failure: return failure
            row = owned_session(db, session_id, principal)
            if not row: return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            
            replay = replay_session_event(db, request, session_id, payload.clientEventId, digest, principal)
            if replay is not None: return replay
            
            if row["revision"] != payload.expectedRevision:
                return error(request, 409, "SESSION_CONFLICT", "会话状态已变化")

            if payload.feedback in ("UNDERSTOOD", "NOT_UNDERSTOOD"):
                has_question = db.execute(
                    "SELECT 1 FROM interaction_events WHERE session_id = ? AND page_no = ? AND event_type = 'QUESTION'", 
                    (session_id, payload.pageNumber)
                ).fetchone()
                if not has_question:
                    return error(request, 409, "PROMPT_REQUIRED", "当前页尚无问题")

            profile = db.execute("SELECT age_band FROM child_profiles WHERE id = ?", (row["profile_id"],)).fetchone()
            age_band = profile["age_band"] if profile else "AGE_3_4"
            
            page = db.execute("SELECT body_text FROM story_pages WHERE version_id = ? AND page_no = ?", (row["version_id"], payload.pageNumber)).fetchone()
            page_text = page["body_text"] if page else ""

            # 提前查好知识，传给 agent
            knowledge = search_knowledge(db, age_band, "GUIDANCE")
            
    except Exception:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")

    # 2. 事务外：调用模型
    if payload.feedback == "INITIAL": user_input = "请针对这一页向孩子提一个问题。"
    elif payload.feedback == "UNDERSTOOD": user_input = "孩子说理解了，请进一步引导。" 
    elif payload.feedback == "NOT_UNDERSTOOD": user_input = "孩子没听懂，请简化问题。"
    else: user_input = "孩子选择跳过。"

    agent_result = await run_agent(
        model_adapter=request.app.state.model_adapter,
        page_text=page_text,
        user_input=user_input,
        feedback_type=payload.feedback,
        knowledge=knowledge # 传入查好的知识
    )

    # 3. 短事务：保存互动事件
    try:
        with authorized_db(request, write=True) as (db, principal, failure):
            if failure: return failure
            row = owned_session(db, session_id, principal)
            if row["revision"] != payload.expectedRevision:
                return error(request, 409, "SESSION_CONFLICT", "会话状态已变化")
            
            new_rev = row["revision"] + 1
            event_type = "QUESTION" if payload.feedback == "INITIAL" else "FEEDBACK"
            result_status = 'SUCCESS' if agent_result['status']=='GENERATED' else 'FALLBACK'

            cursor = db.execute("""INSERT INTO interaction_events 
                (session_id, version_id, request_key, request_digest, response_json, page_no, event_type, result_status, revision_after, created_at) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, row["version_id"], payload.clientEventId, digest, '{}', payload.pageNumber, 
                 event_type, result_status, new_rev, int(time.time()*1000)))
            
            db.execute("UPDATE reading_sessions SET revision = ? WHERE id = ?", (new_rev, session_id))
            
            response_data = {"data": {**agent_result, "interactionEventId": cursor.lastrowid, "revision": new_rev}, "requestId": request.state.request_id}
            record_session_event(db, principal.guardian_id, session_id, payload.clientEventId, digest, int(time.time()*1000), 201, response_data)
            return JSONResponse(status_code=201, content=response_data)
    except Exception:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")