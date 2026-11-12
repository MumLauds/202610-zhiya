"""Shared health and speech API host; speech authentication is supplied by G07."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.v1.health import router as health_router
from app.api.v1.voice import router as voice_router
from app.adapters.model import ModelAdapter
from app.config import Settings
from app.errors import ServiceError
from app.model_gateway import ModelGateway
from app.speech_config import SpeechSettings
from app.voice_gateway import VoiceGateway
from app.services.voice_access import UnconfiguredVoiceAuthenticator, VoiceAuthenticator
from app.services.voice_media import VoiceMediaStore

logger = logging.getLogger("zhiya.http")


def create_app(settings: Settings | None = None, speech_settings: SpeechSettings | None = None,
               *, voice_auth: VoiceAuthenticator | None = None) -> FastAPI:
    config = settings if settings is not None else Settings.from_env()
    speech_config = speech_settings if speech_settings is not None else SpeechSettings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # HTTPX info/debug logs include provider URLs; keep this bootstrap's logs metadata-only.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with ModelGateway(config) as gateway, VoiceGateway(speech_config) as voice_gateway:
            app.state.model_gateway = gateway
            app.state.model_adapter = ModelAdapter(gateway)
            app.state.voice_gateway = voice_gateway
            async def expire_audio():
                while True:
                    await asyncio.sleep(30)
                    app.state.voice_media.expire()
            expiry_task = asyncio.create_task(expire_audio())
            try:
                yield
            finally:
                expiry_task.cancel()
                with suppress(asyncio.CancelledError):
                    await expiry_task
                app.state.voice_media.items.clear()

    app = FastAPI(title="知芽后端", version="0.1.0", lifespan=lifespan)
    app.state.voice_auth = voice_auth if voice_auth is not None else UnconfiguredVoiceAuthenticator()
    app.state.voice_media = VoiceMediaStore()
    app.state.speech_settings = speech_config

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid4().hex
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            error = ServiceError("INTERNAL_ERROR", "服务暂时不可用", 500)
            response = JSONResponse(error.as_response(request.state.request_id), status_code=500)
        response.headers["X-Request-ID"] = request.state.request_id
        route = request.scope.get("route")
        logger.info("http_request request_id=%s route=%s status=%d duration_ms=%d",
                    request.state.request_id, getattr(route, "path", "<unmatched>"),
                    response.status_code, int((perf_counter() - started) * 1000))
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return JSONResponse(exc.as_response(request.state.request_id), status_code=exc.status_code)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code, message = {
            404: ("NOT_FOUND", "请求的资源不存在"),
            405: ("METHOD_NOT_ALLOWED", "请求方法不支持"),
        }.get(exc.status_code, ("HTTP_ERROR", "请求未能完成"))
        error = ServiceError(code, message, exc.status_code)
        return JSONResponse(error.as_response(request.state.request_id),
                            status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError):
        error = ServiceError("VALIDATION_ERROR", "请求参数不符合要求", 422)
        return JSONResponse(error.as_response(request.state.request_id), status_code=422)

    app.include_router(health_router)
    app.include_router(voice_router)

    return app


app = create_app()
