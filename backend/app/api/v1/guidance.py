from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from app.api.v1.auth import authorized_db, error
from app.services.reading_agent import run_agent
from app.api.v1.reading_sessions import owned_session

router = APIRouter(prefix="/api/v1/reading-sessions")

class GuidanceRequest(BaseModel):
    pageNumber: int = Field(ge=1)
    feedback: str = Field(pattern="^(INITIAL|UNDERSTOOD|NOT_UNDERSTOOD|SKIPPED)$")
    expectedRevision: int = Field(ge=0)
    clientEventId: str = Field(min_length=1, max_length=64)

@router.post("/{session_id}/guidance", status_code=201)
async def get_guidance(payload: GuidanceRequest, request: Request, session_id: int):
    try:
        with authorized_db(request, write=True) as (db, principal, failure):
            if failure:
                return failure
                
            row = owned_session(db, session_id, principal)
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "会话不可用")
                
            # 从 db 获取当前页的文本
            page = db.execute(
                "SELECT body_text FROM story_pages WHERE version_id = ? AND page_no = ?",
                (row["version_id"], payload.pageNumber)
            ).fetchone()
            page_text = page["body_text"] if page else ""

            # 调用 Agent
            agent_result = await run_agent(
                db=db,
                model_adapter=request.app.state.model_adapter,  # 假设在 main.py 里存了
                page_text=page_text,
                user_input="",  # 如果前端有传，从 payload 取
                feedback_type=payload.feedback,
                age_band="AGE_3_4"
            )
            
            # 依据契约，你还应该把事件存入 interaction_events 表，这里为了先跑通，只返回结果
            return {"data": agent_result, "requestId": request.state.request_id}
    except Exception as e:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")