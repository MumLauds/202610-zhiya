"""API-022/023 and audio part of API-024; no client-supplied text or identity."""

import asyncio
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from app.audio_conversion import MAX_UPLOAD_BYTES, normalize_recording
from app.errors import ServiceError
from app.schemas.voice import PageVoiceRequest, SynthesisRequest
from app.services.voice_access import VoicePrincipal, read_voice_source, recheck_source

router = APIRouter(prefix="/api/v1")


def invalid():
    return ServiceError("VALIDATION_ERROR", "请求参数不符合要求", 422)


async def authenticate(request, *, write):
    principal = await request.app.state.voice_auth.authenticate(request, write=write)
    if not isinstance(principal, VoicePrincipal):
        raise ServiceError("VOICE_AUTH_NOT_CONFIGURED", "语音业务认证未正确接入", 503)
    return principal


async def refresh(request, principal, snapshot, *, write):
    if await authenticate(request, write=write) != principal:
        raise ServiceError("RESOURCE_NOT_FOUND", "请求的资源不存在", 404)
    recheck_source(principal, snapshot)


async def bounded_body(request, limit):
    body = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    raise ServiceError("UPLOAD_TOO_LARGE", "上传超过大小限制", 413)
                body.extend(chunk)
    except TimeoutError:
        raise ServiceError("UPLOAD_TIMEOUT", "上传超时，请重新录音", 504) from None
    return bytes(body)


async def operation_until_disconnect(request, operation):
    async def watch():
        while not await request.is_disconnected():
            await asyncio.sleep(0.1)

    work, watcher = asyncio.create_task(operation), asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            raise asyncio.CancelledError
        return await work
    finally:
        for task in (work, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, watcher, return_exceptions=True)


@router.post("/voice/transcriptions", openapi_extra={"requestBody": {"required": True, "content": {
    "multipart/form-data": {"schema": {"type": "object", "required": ["file", "readingSessionId", "pageNumber", "revision"],
        "properties": {"file": {"type": "string", "format": "binary"}, **PageVoiceRequest.model_json_schema()["properties"]}}}}}})
async def transcribe(request: Request):
    principal = await authenticate(request, write=True)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise ServiceError("UNSUPPORTED_MEDIA_TYPE", "请使用录音文件上传格式", 415)
    body = await bounded_body(request, MAX_UPLOAD_BYTES + 65536)

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    # Parsing a bounded buffer prevents multipart headers/parts bypassing the upload limit.
    parsed_request = Request(request.scope, receive)
    try:
        async with parsed_request.form(max_files=1, max_fields=3, max_part_size=128) as form:
            if len(form.multi_items()) != 4 or set(form) != {"file", "readingSessionId", "pageNumber", "revision"}:
                raise invalid()
            upload = form["file"]
            if not isinstance(upload, UploadFile):
                raise invalid()
            values = {}
            for key in ("readingSessionId", "pageNumber", "revision"):
                value = form[key]
                if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,16}", value):
                    raise invalid()
                values[key] = int(value)
            fields = PageVoiceRequest.model_validate(values)
            snapshot = read_voice_source(principal, session_id=fields.reading_session_id,
                                         page_number=fields.page_number, revision=fields.revision)
            audio = await upload.read(MAX_UPLOAD_BYTES + 1)
            media_type = upload.content_type or ""
    except (ValidationError, HTTPException):
        raise invalid() from None
    # The multipart context has closed its spooled files before conversion/provider I/O.
    async def recognize():
        wav = await normalize_recording(audio, media_type)
        await refresh(request, principal, snapshot, write=True)
        result = await request.app.state.voice_gateway.transcribe(audio=wav, media_type="audio/wav")
        await refresh(request, principal, snapshot, write=True)
        return {"data": {"text": result.text, "status": "RECOGNIZED", "reliable": result.reliable},
                "requestId": request.state.request_id}

    with request.app.state.voice_media.claim(snapshot.session_id):
        return await operation_until_disconnect(request, recognize())


@router.post("/voice/syntheses", status_code=201, openapi_extra={"requestBody": {"required": True,
    "content": {"application/json": {"schema": SynthesisRequest.model_json_schema()}}}})
async def synthesize(request: Request):
    principal = await authenticate(request, write=True)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ServiceError("UNSUPPORTED_MEDIA_TYPE", "请使用 JSON 请求格式", 415)
    try:
        fields = SynthesisRequest.model_validate(json.loads(await bounded_body(request, 4096)))
    except (ValidationError, ValueError, RecursionError):
        raise invalid() from None
    snapshot = read_voice_source(principal, session_id=fields.reading_session_id,
                                 page_number=fields.page_number, revision=fields.revision,
                                 event_id=fields.interaction_event_id)
    config = request.app.state.speech_settings
    if not config.tts_configured:
        raise ServiceError("TTS_NOT_CONFIGURED", "语音合成尚未配置", 503)
    store = request.app.state.voice_media
    # Profile preference persistence is not in the current schema; use configured default/NORMAL.
    voice_id, speech_rate = config.tts_voice, "NORMAL"

    async def generate():
        await refresh(request, principal, snapshot, write=True)
        audio = await request.app.state.voice_gateway.synthesize(text=snapshot.text, voice_id=voice_id, speech_rate=speech_rate)
        await refresh(request, principal, snapshot, write=True)
        return store.put(principal, snapshot, voice_id, speech_rate, config.tts_model, audio)

    with store.claim(snapshot.session_id):
        found = store.find(principal, snapshot, voice_id, speech_rate, config.tts_model)
        if found is not None:
            await refresh(request, principal, snapshot, write=True)
        key, media = found if found is not None else await operation_until_disconnect(request, generate())
    return {"data": {"audioUrl": f"/api/v1/media/{key}",
                     "expiresAt": datetime.fromtimestamp(media.expires_at, timezone.utc).isoformat().replace("+00:00", "Z")},
            "requestId": request.state.request_id}


@router.get("/media/{resource_key}", response_class=Response,
            responses={200: {"content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}}}})
async def media(request: Request, resource_key: str):
    try:
        principal = await authenticate(request, write=False)
        store = request.app.state.voice_media
        store.expire()
        value = store.items.get(resource_key) if re.fullmatch(r"audio-[A-Za-z0-9_-]{32}", resource_key) else None
        if value is None or value.principal != principal:
            raise ServiceError("RESOURCE_NOT_FOUND", "请求的资源不存在", 404)
        recheck_source(principal, value.snapshot)
    except ServiceError as exc:
        if exc.status_code == 503 and exc.code == "DEPENDENCY_UNAVAILABLE":
            # Retrying this GET only rechecks access and reads existing audio; it never synthesizes.
            exc.retryable = True
        raise
    return Response(value.audio, media_type="audio/wav", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'inline; filename="speech.wav"',
    })
