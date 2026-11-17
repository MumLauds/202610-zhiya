"""Real G07 login/CSRF with G08 routes; reading sessions and providers are fixtures."""

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.v1.auth import _login_attempts
from app.config import Settings
from app.main import create_app
from app.speech_config import SpeechSettings
from app.voice_gateway import Transcription
from test_voice_api import PAGE, change, wav


def checked(response, status):
    assert response.status_code == status, response.text
    assert response.json()["requestId"] == response.headers["X-Request-ID"]
    return response.json()


def login(client, account="guardian-one"):
    credentials = {"account": account, "password": "integration-test-password"}
    checked(client.post("/api/v1/auth/registrations", json=credentials), 201)
    checked(client.post("/api/v1/auth/sessions", json=credentials), 201)
    return {"X-CSRF-Token": client.cookies["zhiya_csrf"]}


@pytest.fixture
def integrated(tmp_path, monkeypatch):
    path = tmp_path / "integration.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    with closing(sqlite3.connect(path)) as db:
        db.executescript((Path(__file__).resolve().parents[2] / "database/schema.sql").read_text(encoding="utf-8"))
    _login_attempts.clear()
    app = create_app(Settings(), SpeechSettings(api_key="test-key", tts_api_url="https://space.example.test/tts"))
    with TestClient(app, base_url="https://testserver") as client:
        app.state.voice_gateway = AsyncMock()
        app.state.voice_gateway.synthesize.return_value = wav(24000)
        app.state.voice_gateway.transcribe.return_value = Transcription("测试识别")
        headers = login(client)
        profile = checked(client.post("/api/v1/child-profiles", headers=headers,
                                      json={"ageBand": "AGE_4_5", "preferredMinutes": 10}), 201)
        profile_id = profile["data"]["childProfileId"]
        # G07 has no reading-session API yet. Only this business state is seeded.
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript("""
                INSERT INTO storybooks VALUES (1,NULL,'LIBRARY',0);
                INSERT INTO story_versions(id,storybook_id,version_no,title,topics_json,age_min,age_max,license_note,status,reviewed_by_id,reviewed_at,created_at,branching)
                    VALUES (1,1,1,'test','[]',3,6,'test','PUBLISHED',1,1,0,0);
                INSERT INTO story_pages(version_id,page_no,body_text,image_ref) VALUES (1,1,'测试页面正文','test-image');
            """)
            db.execute("""INSERT INTO reading_sessions(id,profile_id,version_id,current_page_no,mode,status,revision,created_at,started_at)
                VALUES (10,?,1,1,'PARENT_CHILD','IN_PROGRESS',3,0,1)""", (profile_id,))
        yield client, app, path, headers
    _login_attempts.clear()


def synthesize(client, headers):
    return client.post("/api/v1/voice/syntheses", json=PAGE, headers=headers)


def transcribe(client, headers):
    return client.post("/api/v1/voice/transcriptions", data=PAGE, headers=headers,
                       files={"file": ("input.wav", wav(), "audio/wav")})


def test_real_login_speech_media_and_logout_rejects_cookie_replay(integrated):
    client, app, _, headers = integrated
    checked(client.get("/api/v1/storybooks"), 200)
    checked(client.get("/api/v1/story-versions/1"), 200)
    checked(transcribe(client, headers), 200)
    url = checked(synthesize(client, headers), 201)["data"]["audioUrl"]
    assert client.get(url).content == wav(24000)
    token = client.cookies["zhiya_session"]
    assert client.delete("/api/v1/auth/sessions/current", headers=headers).status_code == 204
    client.cookies.set("zhiya_session", token)
    assert checked(client.get(url), 401)["error"]["code"] == "AUTH_REQUIRED"
    checked(synthesize(client, headers), 401)
    app.state.voice_gateway.transcribe.assert_awaited_once()
    app.state.voice_gateway.synthesize.assert_awaited_once()


@pytest.mark.parametrize("csrf", [None, "wrong", b"\xff"])
def test_real_csrf_blocks_both_paid_routes(integrated, csrf):
    client, app, _, _ = integrated
    headers = {} if csrf is None else {"X-CSRF-Token": csrf}
    for response in (synthesize(client, headers), transcribe(client, headers)):
        assert checked(response, 403)["error"]["code"] == "CSRF_INVALID"
    app.state.voice_gateway.synthesize.assert_not_awaited()
    app.state.voice_gateway.transcribe.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["anonymous", "forged", "expired", "revoked", "role"])
def test_real_identity_is_rechecked_for_media_and_writes(integrated, invalid):
    client, app, path, headers = integrated
    url = checked(synthesize(client, headers), 201)["data"]["audioUrl"]
    if invalid in ("anonymous", "forged"):
        client.cookies.clear()
        if invalid == "forged":
            client.cookies.set("zhiya_session", "forged")
    else:
        change(path, {"expired": "UPDATE auth_sessions SET expires_at=0",
                      "revoked": "UPDATE auth_sessions SET revoked_at=1",
                      "role": "UPDATE accounts SET role='ADMIN'"}[invalid])
    status = 403 if invalid == "role" else 401
    for response in (client.get(url), synthesize(client, headers), transcribe(client, headers)):
        checked(response, status)
    app.state.voice_gateway.synthesize.assert_awaited_once()
    app.state.voice_gateway.transcribe.assert_not_awaited()


def test_other_real_guardian_cannot_access_session_or_media(integrated):
    client, app, _, headers = integrated
    url = checked(synthesize(client, headers), 201)["data"]["audioUrl"]
    client.cookies.clear()
    other_headers = login(client, "guardian-two")
    for response in (client.get(url), synthesize(client, other_headers), transcribe(client, other_headers)):
        assert checked(response, 404)["error"]["code"] == "RESOURCE_NOT_FOUND"
    app.state.voice_gateway.synthesize.assert_awaited_once()
    app.state.voice_gateway.transcribe.assert_not_awaited()


@pytest.mark.parametrize("operation", ["synthesize", "transcribe"])
def test_revocation_during_paid_operation_does_not_return_or_cache_result(integrated, operation):
    client, app, path, headers = integrated
    async def revoke(**kwargs):
        change(path, "UPDATE auth_sessions SET revoked_at=1")
        return wav(24000) if operation == "synthesize" else Transcription("测试识别")
    provider = getattr(app.state.voice_gateway, operation)
    provider.side_effect = revoke
    response = synthesize(client, headers) if operation == "synthesize" else transcribe(client, headers)
    error = checked(response, 401)["error"]
    assert error["code"] == "AUTH_REQUIRED" and error["retryable"] is False
    assert not app.state.voice_media.items
    provider.assert_awaited_once()


def test_real_auth_dependency_failure_keeps_get_and_post_retry_distinct(integrated, monkeypatch):
    client, app, path, headers = integrated
    url = checked(synthesize(client, headers), 201)["data"]["audioUrl"]
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path.with_name("missing.sqlite3")))
    for response, retryable in ((client.get(url), True), (synthesize(client, headers), False),
                                (transcribe(client, headers), False)):
        error = checked(response, 503)["error"]
        assert error["code"] == "DEPENDENCY_UNAVAILABLE" and error["retryable"] is retryable
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    assert client.get(url).content == wav(24000)
    app.state.voice_gateway.synthesize.assert_awaited_once()
    app.state.voice_gateway.transcribe.assert_not_awaited()
