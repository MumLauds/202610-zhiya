from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.health import is_ready

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health():
    request_id = str(uuid4())
    if is_ready():
        return {"data": {"status": "ok"}, "requestId": request_id}
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "DEPENDENCY_UNAVAILABLE",
                "message": "服务暂不可用",
                "retryable": True,
            },
            "requestId": request_id,
        },
    )
