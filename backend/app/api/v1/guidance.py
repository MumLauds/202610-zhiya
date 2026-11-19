from fastapi import APIRouter, Request, Path
from fastapi.responses import JSONResponse
import time
import json
import uuid
from pydantic import BaseModel, Field
from app.api.v1.auth import authorized_db, error
from app.api.v1.reading_sessions import owned_session, replay_session_event, record_session_event, request_digest
from app.services.reading_agent import run_agent
from app.services.knowledge_service import search_knowledge

router = APIRouter(prefix="/api/v1/reading-sessions")

class GuidanceRequest(BaseModel):
    pageNumber: int = Field(ge=1)
    feedback: str = Field(pattern="^(INITIAL|UNDERSTOOD|NOT_UNDERSTOOD|SKIPPED)$")
    expectedRevision: int = Field(ge=0)
    clientEventId: str = Field(min_length=1, max_length=64)

@router.post("/{session_id}/guidance", status_code=201)
async def get_guidance(payload: GuidanceRequest, request: Request, session_id: int = Path(ge=1)):
    digest = request_digest(f"/api/v1/reading-sessions/{session_id}/guidance", payload.model_dump())
    
    # 1. 查库、校验、幂等重放、获取页面文本、查知识
    try:
        with authorized_db(request, write=True) as (db, principal, failure):
            if failure: return failure
            row = owned_session(db, session_id, principal)
            if not row: return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
            if row["status"] not in ("CREATED", "IN_PROGRESS"):
                return error(request, 409, "SESSION_CONFLICT", "会话状态已结束")
                
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

            # 查知识（带入 query_text）
            knowledge = search_knowledge(db, age_band, "GUIDANCE", page_text)

            # 插入 RUNNING 占位，防止并发双击（审查员 P2 要求）
            execution_token = str(uuid.uuid4())
            now = int(time.time() * 1000)
            db.execute("""INSERT INTO request_executions
                (owner_account_id, session_id, scope_type, scope_id, client_request_id, request_digest,
                 status, execution_token, started_at, deadline_at)
                VALUES (?, ?, 'SESSION_EVENT', ?, ?, ?, 'RUNNING', ?, ?, ?)""",
                (principal.guardian_id, session_id, session_id, payload.clientEventId, digest, execution_token, now, now + 25000))
    except Exception as e:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", f"服务暂不可用: {str(e)}")

    # 2. 事务外：构造指令、调模型（或者处理跳过）
    if payload.feedback == "SKIPPED":
        agent_result = {
            "status": "FALLBACK", "outputText": "", "strategyType": "SKIP", "sources": []
        }
    else:
        if payload.feedback == "INITIAL": user_input = "请针对这一页向孩子提一个问题。"
        elif payload.feedback == "UNDERSTOOD": user_input = "孩子说理解了，请进一步引导。" 
        elif payload.feedback == "NOT_UNDERSTOOD": user_input = "孩子没听懂，请简化问题。"
        
        agent_result = await run_agent(
            model_adapter=request.app.state.model_adapter,
            page_text=page_text,
            user_input=user_input,
            feedback_type=payload.feedback,
            knowledge=knowledge
        )

    # 3. 短事务：保存互动事件、引用、更新 revision、记录幂等响应
    try:
        with authorized_db(request, write=True) as (db, principal, failure):
            if failure: return failure
            row = owned_session(db, session_id, principal)
            if row["revision"] != payload.expectedRevision:
                return error(request, 409, "SESSION_CONFLICT", "会话状态已变化")
            
            new_rev = row["revision"] + 1
            event_type = "QUESTION" if payload.feedback == "INITIAL" else "FEEDBACK"
            result_status = 'SUCCESS' if agent_result['status']=='GENERATED' else 'FALLBACK'
            now = int(time.time()*1000)
            
            # 构建完整的 response_json
            response_data = {"data": {**agent_result, "revision": new_rev}, "requestId": request.state.request_id}

            # 插入互动事件（补全 output_text, feedback）
            cursor = db.execute("""INSERT INTO interaction_events 
                (session_id, version_id, request_key, request_digest, response_json, page_no, event_type, result_status, revision_after, created_at, output_text, feedback) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, row["version_id"], payload.clientEventId, digest, json.dumps(response_data), payload.pageNumber, 
                 event_type, result_status, new_rev, now, agent_result.get("outputText"), payload.feedback))
            
            event_id = cursor.lastrowid
            response_data["data"]["interactionEventId"] = event_id

            # 插入知识溯源 event_evidence（审查员要求）
            for idx, src in enumerate(agent_result.get("sources", [])):
                db.execute("INSERT INTO event_evidence (event_id, knowledge_item_id, rank_no) VALUES (?, ?, ?)",
                           (event_id, src["knowledgeItemId"], idx + 1))

            db.execute("UPDATE reading_sessions SET revision = ? WHERE id = ?", (new_rev, session_id))
            
            # 更新请求执行记录为成功
            db.execute("""UPDATE request_executions SET status = 'SUCCEEDED', completed_at = ?, response_status = 201, response_body = ?
                WHERE session_id = ? AND client_request_id = ?""",
                (now, json.dumps(response_data), session_id, payload.clientEventId))

            return JSONResponse(status_code=201, content=response_data)
    except Exception as e:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", f"保存失败: {str(e)}")