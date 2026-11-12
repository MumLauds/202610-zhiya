import asyncio
import io
import sqlite3
import time
import wave
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ServiceError
from app.main import create_app
from app.services.voice_access import VoicePrincipal
from app.services.voice_media import MAX_MEDIA_BYTES, VoiceMediaStore
from app.speech_config import SpeechSettings
from app.voice_gateway import Transcription

PAGE = {"readingSessionId": 10, "pageNumber": 1, "revision": 3}


def wav(rate=16000):
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(b"\x00\x00" * 160)
    return target.getvalue()


class TestAuthenticator:
    __test__ = False

    def __init__(self):
        self.principal = VoicePrincipal(1)
        self.error = None
        self.calls = []

    async def authenticate(self, request, *, write):
        self.calls.append(write)
        if self.error:
            raise self.error
        return self.principal


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    with closing(sqlite3.connect(path)) as db:
        db.executescript((Path(__file__).resolve().parents[2] / "database/schema.sql").read_text(encoding="utf-8"))
        db.executescript("""
            INSERT INTO accounts VALUES (1,'one','hash','GUARDIAN',0),(2,'two','hash','GUARDIAN',0);
            INSERT INTO child_profiles VALUES (1,1,'test','AGE_5_6','[]',10,0,0),(2,2,'test','AGE_5_6','[]',10,0,0);
            INSERT INTO storybooks VALUES (1,NULL,'LIBRARY',0);
            INSERT INTO story_versions(id,storybook_id,version_no,title,topics_json,age_min,age_max,license_note,status,reviewed_by_id,reviewed_at,created_at,branching)
                VALUES (1,1,1,'test','[]',3,6,'test','PUBLISHED',1,1,0,0);
            INSERT INTO story_pages(version_id,page_no,body_text,image_ref) VALUES (1,1,'已审核的页面正文','image'),(1,2,'另一页','image');
            INSERT INTO reading_sessions(id,profile_id,version_id,current_page_no,mode,status,revision,created_at,started_at)
                VALUES (10,1,1,1,'PARENT_CHILD','IN_PROGRESS',3,0,1),(20,2,1,1,'PARENT_CHILD','IN_PROGRESS',3,0,1);
            INSERT INTO interaction_events(id,session_id,version_id,request_key,request_digest,response_json,page_no,event_type,output_text,result_status,revision_after,created_at)
                VALUES (100,10,1,'key','digest','{}',1,'ASK','已保存并核验的回答','SUCCESS',3,1);
        """)
        db.commit()
    auth = TestAuthenticator()
    app = create_app(Settings(), SpeechSettings(api_key="test-key", tts_api_url="https://space.example.test/tts"), voice_auth=auth)
    with TestClient(app) as client:
        app.state.voice_gateway = AsyncMock()
        app.state.voice_gateway.synthesize.return_value = wav(24000)
        app.state.voice_gateway.transcribe.return_value = Transcription("识别文字")
        yield client, app, auth, path


def change(path, sql, parameters=()):
    with closing(sqlite3.connect(path)) as db:
        db.execute(sql, parameters)
        db.commit()


def upload(client, fields=None, audio=None, content_type="audio/wav"):
    return client.post("/api/v1/voice/transcriptions", data=PAGE if fields is None else fields,
                       files={"file": ("untrusted-name.wav", wav() if audio is None else audio, content_type)})


def test_transcription_envelope_and_live_auth_checks(setup):
    client, app, auth, _ = setup
    response = upload(client)
    assert response.status_code == 200
    assert response.json()["data"] == {"text": "识别文字", "status": "RECOGNIZED", "reliable": False}
    assert response.json()["requestId"] == response.headers["X-Request-ID"]
    assert auth.calls == [True, True, True]
    app.state.voice_gateway.transcribe.assert_awaited_once()


def test_synthesis_sources_short_cache_and_authorized_audio(setup):
    client, app, auth, _ = setup
    first = client.post("/api/v1/voice/syntheses", json=PAGE)
    second = client.post("/api/v1/voice/syntheses", json=PAGE)
    assert first.status_code == second.status_code == 201
    assert first.json()["data"] == second.json()["data"]
    app.state.voice_gateway.synthesize.assert_awaited_once_with(text="已审核的页面正文", voice_id="wenhuaiqing_v3.1", speech_rate="NORMAL")
    url = first.json()["data"]["audioUrl"]
    result = client.get(url)
    assert result.content == wav(24000) and result.headers["content-type"] == "audio/wav"
    assert result.headers["cache-control"] == "private, no-store"
    auth.principal = VoicePrincipal(2)
    assert client.get(url).status_code == 404
    auth.principal = VoicePrincipal(1)
    event = client.post("/api/v1/voice/syntheses", json={"interactionEventId": 100})
    assert event.status_code == 201
    assert app.state.voice_gateway.synthesize.await_args.kwargs["text"] == "已保存并核验的回答"


@pytest.mark.parametrize("payload", [{}, {"text":"arbitrary"}, {**PAGE,"text":"arbitrary"},
    {**PAGE,"voiceId":"longniuniu_v3.1"}, {**PAGE,"interactionEventId":100},
    {"interactionEventId":None}, {**PAGE,"revision":True}, {**PAGE,"readingSessionId":"10"},
    {**PAGE,"pageNumber":0}])
def test_synthesis_rejects_ambiguous_or_client_text_requests(setup, payload):
    client, app, _, _ = setup
    assert client.post("/api/v1/voice/syntheses", json=payload).status_code == 422
    app.state.voice_gateway.synthesize.assert_not_awaited()


@pytest.mark.parametrize("code,status", [("AUTH_REQUIRED",401),("CSRF_FAILED",403)])
def test_authentication_and_csrf_fail_before_provider(setup, code, status):
    client, app, auth, _ = setup
    auth.error = ServiceError(code, "拒绝", status)
    assert upload(client).status_code == status
    assert client.post("/api/v1/voice/syntheses", json=PAGE).status_code == status
    app.state.voice_gateway.transcribe.assert_not_awaited()
    app.state.voice_gateway.synthesize.assert_not_awaited()


@pytest.mark.parametrize("fields,status", [({**PAGE,"readingSessionId":20},404),
    ({**PAGE,"revision":2},409), ({**PAGE,"pageNumber":2},409), ({**PAGE,"revision":"-1"},422)])
def test_upload_ownership_and_stale_page_rejected(setup, fields, status):
    client, app, _, _ = setup
    assert upload(client, fields=fields).status_code == status
    app.state.voice_gateway.transcribe.assert_not_awaited()


def test_body_and_audio_limits_are_checked_without_provider(setup):
    client, app, _, _ = setup
    assert upload(client, audio=b"x"*(4*1024*1024+1)).status_code == 413
    assert upload(client, audio=b"x", content_type="text/plain").status_code == 415
    assert client.post("/api/v1/voice/transcriptions", content=b"x"*(4*1024*1024+65537),
                       headers={"Content-Type":"multipart/form-data; boundary=abc"}).status_code == 413
    assert client.post("/api/v1/voice/syntheses", content="x"*4097,
                       headers={"Content-Type":"application/json"}).status_code == 413
    app.state.voice_gateway.transcribe.assert_not_awaited()
    app.state.voice_gateway.synthesize.assert_not_awaited()


def test_duplicate_fields_and_missing_file_rejected(setup):
    client, app, _, _ = setup
    files=[("file",("a.wav",wav(),"audio/wav")),("readingSessionId",(None,"10")),
           ("pageNumber",(None,"1")),("revision",(None,"3")),("revision",(None,"3"))]
    assert client.post("/api/v1/voice/transcriptions", files=files).status_code == 422
    assert client.post("/api/v1/voice/transcriptions", data=PAGE).status_code == 415
    app.state.voice_gateway.transcribe.assert_not_awaited()


def test_uploaded_spooled_files_close_on_validation_and_decode_failures(setup, monkeypatch):
    import starlette.formparsers
    client, app, _, _ = setup
    original = starlette.formparsers.SpooledTemporaryFile
    opened = []
    def track(*args, **kwargs):
        result = original(*args, **kwargs)
        opened.append(result)
        return result
    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", track)
    assert upload(client, fields={**PAGE, "revision": "invalid"}, audio=b"x"*1200000).status_code == 422
    assert upload(client, audio=b"x"*1200000).status_code == 422
    assert len(opened) == 2 and all(item.closed for item in opened)
    app.state.voice_gateway.transcribe.assert_not_awaited()


def test_conversion_then_session_recheck_prevents_stale_paid_asr(setup, monkeypatch):
    client, app, _, path = setup
    async def convert(*args):
        change(path,"UPDATE reading_sessions SET revision=4 WHERE id=10")
        return wav()
    monkeypatch.setattr("app.api.v1.voice.normalize_recording",convert)
    assert upload(client, audio=b"webm", content_type="audio/webm;codecs=opus").status_code == 409
    app.state.voice_gateway.transcribe.assert_not_awaited()


@pytest.mark.parametrize("kind", ["asr","tts"])
def test_withdrawal_during_provider_never_returns_result(setup, kind):
    client, app, _, path = setup
    async def provider(**kwargs):
        change(path,"UPDATE story_versions SET status='WITHDRAWN' WHERE id=1")
        return wav(24000) if kind=="tts" else Transcription("secret")
    if kind=="tts":
        app.state.voice_gateway.synthesize.side_effect=provider
        response=client.post("/api/v1/voice/syntheses",json=PAGE)
    else:
        app.state.voice_gateway.transcribe.side_effect=provider
        response=upload(client)
    assert response.status_code == 404 and "secret" not in response.text
    assert not app.state.voice_media.items and not app.state.voice_media.inflight


def test_auth_revocation_after_provider_and_failed_events(setup):
    client, app, auth, path = setup
    change(path,"UPDATE interaction_events SET result_status='FAILED' WHERE id=100")
    assert client.post("/api/v1/voice/syntheses",json={"interactionEventId":100}).status_code==404
    app.state.voice_gateway.synthesize.assert_not_awaited()
    async def provider(**kwargs):
        auth.error=ServiceError("AUTH_REQUIRED","登录已失效",401)
        return wav(24000)
    app.state.voice_gateway.synthesize.side_effect=provider
    assert client.post("/api/v1/voice/syntheses",json=PAGE).status_code==401
    assert not app.state.voice_media.items


def test_media_expiry_and_withdrawn_content(setup):
    client, app, _, path = setup
    url=client.post("/api/v1/voice/syntheses",json=PAGE).json()["data"]["audioUrl"]
    change(path,"UPDATE story_versions SET status='WITHDRAWN' WHERE id=1")
    assert client.get(url).status_code==404
    change(path,"UPDATE story_versions SET status='PUBLISHED' WHERE id=1")
    from dataclasses import replace
    key=url.rsplit('/',1)[1]
    app.state.voice_media.items[key]=replace(app.state.voice_media.items[key],expires_at=time.time()-1)
    assert client.get(url).status_code==404
    assert not app.state.voice_media.items


def test_child_grant_scope_expiry_and_revocation(setup):
    client, app, auth, path = setup
    now=int(time.time()*1000)
    change(path,"INSERT INTO auth_sessions VALUES (1,1,'token',?,NULL)",(now+600000,))
    change(path,"INSERT INTO child_grants VALUES (5,1,1,'child',?,?,NULL)",(now-1000,now+60000))
    change(path,"INSERT INTO child_grant_books VALUES (5,1)")
    change(path,"UPDATE reading_sessions SET mode='CHILD_SOLO',grant_id=5 WHERE id=10")
    auth.principal=VoicePrincipal(1,5)
    assert upload(client).status_code==200
    auth.principal=VoicePrincipal(1,6)
    assert upload(client).status_code==404
    auth.principal=VoicePrincipal(1,5)
    change(path,"UPDATE child_grants SET revoked_at=? WHERE id=5",(now,))
    assert upload(client).status_code==404
    change(path,"UPDATE child_grants SET revoked_at=NULL,issued_at=?,expires_at=? WHERE id=5",(now-120000,now-60000))
    assert upload(client).status_code==404
    app.state.voice_gateway.transcribe.assert_awaited_once()


def test_private_and_retired_version_rules(setup):
    client, app, _, path=setup
    change(path,"UPDATE story_versions SET status='RETIRED' WHERE id=1")
    assert upload(client).status_code==200
    change(path,"UPDATE storybooks SET source_type='ORIGINAL',owner_guardian_id=2 WHERE id=1")
    assert upload(client).status_code==404
    change(path,"UPDATE storybooks SET owner_guardian_id=1 WHERE id=1")
    assert upload(client).status_code==200
    change(path,"UPDATE story_versions SET reviewed_at=NULL WHERE id=1")
    assert upload(client).status_code==404


def test_provider_failure_releases_claim_without_cached_audio(setup):
    client, app, _, _=setup
    app.state.voice_gateway.synthesize.side_effect=ServiceError("TTS_TIMEOUT","合成超时",504)
    response=client.post("/api/v1/voice/syntheses",json=PAGE)
    assert response.status_code==504 and response.json()["error"]["retryable"] is False
    assert not app.state.voice_media.inflight and not app.state.voice_media.items


def test_admission_and_media_memory_bound():
    store=VoiceMediaStore()
    with store.claim(10):
        with pytest.raises(ServiceError,match="正在处理"):
            with store.claim(10): pass
    assert not store.inflight
    args=(VoicePrincipal(1),None,"voice","NORMAL","model")
    first,_=store.put(*args,b"x"*(MAX_MEDIA_BYTES//2+1))
    second,_=store.put(*args,b"y"*(MAX_MEDIA_BYTES//2+1))
    assert first not in store.items and second in store.items
    assert sum(len(item.audio) for item in store.items.values()) <= MAX_MEDIA_BYTES


def test_disconnect_cancels_inflight_operation():
    from app.api.v1.voice import operation_until_disconnect
    async def scenario():
        request=AsyncMock()
        request.is_disconnected.return_value=True
        cleaned=asyncio.Event()
        async def work():
            try: await asyncio.sleep(100)
            finally: cleaned.set()
        with pytest.raises(asyncio.CancelledError):
            await operation_until_disconnect(request,work())
        assert cleaned.is_set()
    asyncio.run(scenario())
