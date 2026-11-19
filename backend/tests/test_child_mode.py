"""Child-mode grant issuance (API-020), scoped access/projection, voice integration and parent unlock (API-021)."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.v1.auth import _login_attempts
from app.config import Settings
from app.main import create_app
from app.schemas.agent import ModelGenerateResult
from app.speech_config import SpeechSettings
from app.voice_gateway import Transcription
from test_voice_api import wav

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema.sql"


def _login(client, account="guardian-one", password="strong-pass-123"):
    creds = {"account": account, "password": password}
    assert client.post("/api/v1/auth/registrations", json=creds).status_code == 201
    assert client.post("/api/v1/auth/sessions", json=creds).status_code == 201
    return {"X-CSRF-Token": client.cookies["zhiya_csrf"]}


@pytest.fixture
def child_env(tmp_path, monkeypatch):
    path = tmp_path / "child-mode.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
    _login_attempts.clear()
    application = create_app(Settings(), SpeechSettings(api_key="test-key", tts_api_url="https://space.example.test/tts"))
    with TestClient(application, base_url="https://testserver") as first, \
            TestClient(application, base_url="https://testserver") as second:
        application.state.voice_gateway = AsyncMock()
        application.state.voice_gateway.synthesize.return_value = wav(24000)
        application.state.voice_gateway.transcribe.return_value = Transcription("儿童朗读识别")
        h1 = _login(first, "guardian-one")
        _login(second, "guardian-two")
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            for bid, owner, src in ((1, None, "LIBRARY"), (2, None, "LIBRARY"), (3, 1, "ORIGINAL"), (4, 2, "ORIGINAL")):
                db.execute("INSERT INTO storybooks VALUES (?, ?, ?, 1)", (bid, owner, src))
            for vid, bid, vno, status in (
                (11, 1, 1, "PUBLISHED"), (12, 2, 1, "PUBLISHED"),
                (13, 2, 2, "RETIRED"), (31, 3, 1, "RETIRED"), (32, 3, 2, "APPROVED_PRIVATE"),
                (41, 4, 1, "APPROVED_PRIVATE"), (14, 1, 2, "WITHDRAWN"),
            ):
                db.execute("""INSERT INTO story_versions
                    (id, storybook_id, version_no, title, topics_json, age_min, age_max,
                     license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
                    VALUES (?, ?, ?, ?, '["动物"]', 3, 6, 'ok', ?, 1, 1, 1, 0)""",
                    (vid, bid, vno, f"book-{vid}", status))
                db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (?, 2, '第二页', 'none')", (vid,))
                db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (?, 1, '第一页', 'none', 2)", (vid,))
        profile_id = first.post("/api/v1/child-profiles", headers=h1,
                                json={"ageBand": "AGE_4_5", "preferredMinutes": 10}).json()["data"]["childProfileId"]
        yield application, first, second, path, profile_id
    _login_attempts.clear()


def test_enter_child_mode_scopes_access_voice_and_parent_unlock(child_env, monkeypatch):
    application, first, second, path, profile_id = child_env
    guardian_headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    old_guardian_token = first.cookies["zhiya_session"]

    s_child = first.post("/api/v1/reading-sessions", headers=guardian_headers, json={
        "childProfileId": profile_id, "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    s_other = first.post("/api/v1/reading-sessions", headers=guardian_headers, json={
        "childProfileId": profile_id, "storyVersionId": 12, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]

    mode_url = f"/api/v1/reading-sessions/{s_child}/mode-settings"
    # Validation & versionAccess checks before issuing grant.
    assert first.put(mode_url, headers=guardian_headers, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": [12], "timeLimitSeconds": 600
    }).status_code == 422  # must include session's storyVersionId (11)
    for bad_vid in (13, 14, 41):  # library RETIRED, WITHDRAWN, other guardian's private
        assert first.put(mode_url, headers=guardian_headers, json={
            "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11, bad_vid], "timeLimitSeconds": 600
        }).status_code == 404

    # Grant a published book and both the retired and current versions of one private book.
    granted_versions = [11, 31, 32]
    res = first.put(mode_url, headers=guardian_headers, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": granted_versions, "timeLimitSeconds": 600
    })
    assert res.status_code == 200 and res.json()["requestId"] == res.headers["X-Request-ID"]
    data = res.json()["data"]
    assert data["mode"] == "CHILD_SOLO" and data["allowedStoryVersionIds"] == granted_versions
    assert data["expiresAt"].endswith("Z")
    child_token = first.cookies["zhiya_session"]
    child_headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    assert child_token != old_guardian_token
    credentials = {"account": "guardian-one", "password": "strong-pass-123"}
    assert first.post("/api/v1/auth/sessions", json=credentials).status_code == 403
    assert first.cookies["zhiya_session"] == child_token

    # Old guardian cookie on the same device was revoked in the same transaction.
    stale = TestClient(application, base_url="https://testserver")
    stale.cookies.set("zhiya_session", old_guardian_token)
    assert stale.get("/api/v1/child-profiles").status_code == 401

    # Child cannot access guardian-only endpoints or scope=mine.
    assert first.get("/api/v1/child-profiles").status_code == 403
    assert first.post("/api/v1/child-profiles", headers=child_headers,
                      json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).status_code == 403
    assert first.patch(f"/api/v1/child-profiles/{profile_id}", headers=child_headers,
                       json={"nickname": "乐乐"}).status_code == 403
    assert first.delete(f"/api/v1/child-profiles/{profile_id}", headers=child_headers).status_code == 403
    assert first.get("/api/v1/reading-sessions").status_code == 403
    assert first.get(f"/api/v1/reading-sessions/{s_child}", params={"view": "deletion-status"}).status_code == 403
    assert first.post("/api/v1/reading-sessions", headers=child_headers, json={
        "childProfileId": profile_id, "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).status_code == 403
    assert first.delete(f"/api/v1/reading-sessions/{s_child}", headers=child_headers).status_code == 403
    assert first.put(mode_url, headers=child_headers, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11], "timeLimitSeconds": 300
    }).status_code == 403
    assert first.get("/api/v1/storybooks", params={"scope": "mine"}).status_code == 403

    # The default shelf lists current granted versions. An explicitly granted retired version is readable by ID.
    shelf = first.get("/api/v1/storybooks")
    assert shelf.status_code == 200
    assert [b["storyVersionId"] for b in shelf.json()["data"]["items"]] == [11, 32]
    retired = first.get("/api/v1/story-versions/31")
    assert retired.status_code == 200
    assert retired.json()["data"]["storyVersionId"] == 31
    assert first.get("/api/v1/story-versions/12").status_code == 404  # PUBLISHED but not granted

    # Child can read/advance bound session, cannot read sibling session, and report stays null.
    assert first.get(f"/api/v1/reading-sessions/{s_other}").status_code == 404
    session_view = first.get(f"/api/v1/reading-sessions/{s_child}").json()["data"]
    assert session_view["session"]["mode"] == "CHILD_SOLO" and session_view["session"]["expiresAt"] == data["expiresAt"]
    assert session_view["report"] is None
    assert first.post(f"/api/v1/reading-sessions/{s_child}/events", headers=child_headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "c-start"
    }).status_code == 201

    # Voice synthesis/media/transcription work in child mode via GuardianVoiceAuthenticator -> child_grant_id.
    synth = first.post("/api/v1/voice/syntheses", headers=child_headers, json={
        "readingSessionId": s_child, "pageNumber": 1, "revision": 1
    })
    assert synth.status_code == 201
    assert first.get(synth.json()["data"]["audioUrl"]).content == wav(24000)
    assert first.post("/api/v1/voice/transcriptions", headers=child_headers,
                      data={"readingSessionId": s_child, "pageNumber": 1, "revision": 1},
                      files={"file": ("input.wav", wav(), "audio/wav")}).status_code == 200

    # Child finishes session: report is projected as null on finish and replay, while saved for parent.
    class _FakeAdapter:
        async def generate(self, _req):
            return ModelGenerateResult(success=True, text=json.dumps({"suggestionId": "TALK_ABOUT_STORY"}))

    monkeypatch.setattr(application.state, "model_adapter", _FakeAdapter(), raising=False)
    finish_body = {"reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "c-end"}
    done = first.post(f"/api/v1/reading-sessions/{s_child}/completions", headers=child_headers, json=finish_body)
    assert done.status_code == 200 and done.json()["data"]["report"] is None
    replay = first.post(f"/api/v1/reading-sessions/{s_child}/completions", headers=child_headers, json=finish_body)
    assert replay.status_code == 200 and replay.json()["data"]["report"] is None
    assert replay.json()["requestId"] == replay.headers["X-Request-ID"] != done.json()["requestId"]

    # Wrong credentials or wrong guardian account fail 401 without revoking child grant.
    assert first.post("/api/v1/auth/parent-unlocks", headers=child_headers,
                      json={"account": "guardian-two", "password": "strong-pass-123"}).status_code == 401
    assert first.post("/api/v1/auth/parent-unlocks", headers=child_headers,
                      json={"account": "guardian-one", "password": "wrong-password"}).status_code == 401
    assert first.get(f"/api/v1/reading-sessions/{s_child}").status_code == 200

    # Correct parent unlock revokes child grant, issues a fresh guardian cookie, and shows the report.
    unlock = first.post("/api/v1/auth/parent-unlocks", headers=child_headers,
                        json={"account": "guardian-one", "password": "strong-pass-123"})
    assert unlock.status_code == 200 and unlock.json()["data"]["accountId"] == 1
    assert first.cookies["zhiya_session"] not in (old_guardian_token, child_token)
    assert first.get(f"/api/v1/reading-sessions/{s_child}").json()["data"]["report"]["status"] == "READY"
    guardian_headers_new = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    # Guardian cannot call parent-unlocks once back in guardian mode; same-key replay returns PENDING snapshot while a new event sees READY.
    assert first.post("/api/v1/auth/parent-unlocks", headers=guardian_headers_new,
                      json={"account": "guardian-one", "password": "strong-pass-123"}).status_code == 403
    assert first.post(f"/api/v1/reading-sessions/{s_child}/completions",
                      headers=guardian_headers_new, json=finish_body).json()["data"]["report"]["status"] == "PENDING"
    assert first.post(
        f"/api/v1/reading-sessions/{s_child}/completions",
        headers=guardian_headers_new,
        json={**finish_body, "clientEventId": "c-end-guardian", "expectedRevision": 2},
    ).json()["data"]["report"]["status"] == "READY"
    old_child = TestClient(application, base_url="https://testserver")
    old_child.cookies.set("zhiya_session", child_token)
    old_child.cookies.set("zhiya_csrf", child_headers["X-CSRF-Token"])
    assert old_child.get("/api/v1/storybooks").status_code == 401
    assert old_child.post("/api/v1/voice/syntheses", headers=child_headers, json={
        "readingSessionId": s_child, "pageNumber": 1, "revision": 2
    }).status_code == 401


def test_shorten_grant_after_library_retire_and_cookie_retention_after_expiry(child_env, monkeypatch):
    application, first, _, path, profile_id = child_env
    h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}

    class CountingAdapter:
        def __init__(self):
            self.calls = []

        async def generate(self, req):
            self.calls.append(req)
            return ModelGenerateResult(success=True, text='{"suggestionId":"REVISIT_PAGE"}')

    adapter = CountingAdapter()
    monkeypatch.setattr(application.state, "model_adapter", adapter)

    sid = first.post("/api/v1/reading-sessions", headers=h1, json={
        "childProfileId": profile_id, "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    entered = first.put(f"/api/v1/reading-sessions/{sid}/mode-settings", headers=h1, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11], "timeLimitSeconds": 900
    })
    assert entered.status_code == 200
    # Child cookies must outlive child timeLimitSeconds (900s) so API-021 unlock works after expiry.
    issued_sec = int(time.time())
    assert all(not c.is_expired(now=issued_sec + 960) for c in first.cookies.jar)
    assert any("Max-Age=86400" in h for h in entered.headers.get_list("set-cookie"))

    child_headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=child_headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"
    }).status_code == 201

    # Second logged-in guardian device can shorten expiresAt (even after LIBRARY 11 retires),
    # cannot extend or change allowed versions.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'RETIRED' WHERE id = 11")
    assert first.get("/api/v1/story-versions/11").status_code == 200

    with closing(TestClient(application, base_url="https://testserver")) as second_device:
        assert second_device.post("/api/v1/auth/sessions",
                                  json={"account": "guardian-one", "password": "strong-pass-123"}).status_code == 201
        h2 = {"X-CSRF-Token": second_device.cookies["zhiya_csrf"]}
        mode_url = f"/api/v1/reading-sessions/{sid}/mode-settings"
        assert second_device.put(mode_url, headers=h2, json={
            "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11, 31], "timeLimitSeconds": 300
        }).status_code == 409
        assert second_device.put(mode_url, headers=h2, json={
            "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11], "timeLimitSeconds": 1800
        }).status_code == 409
        shortened = second_device.put(mode_url, headers=h2, json={
            "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11], "timeLimitSeconds": 120
        })
        assert shortened.status_code == 200

    # Once child grant expires, business calls fail 401 and session transitions atomically to FINISHED (TIME_LIMIT)
    # with a single API-019 report generation; surviving cookies allow API-021 parent unlock past child TTL.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE child_grants SET issued_at = 1, expires_at = 60001")
    with closing(TestClient(application, base_url="https://testserver")) as child_past_ttl:
        for cookie in first.cookies.jar:
            if not cookie.is_expired(now=issued_sec + 960):
                child_past_ttl.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        assert child_past_ttl.get(f"/api/v1/reading-sessions/{sid}").status_code == 401
        assert child_past_ttl.get("/api/v1/storybooks").status_code == 401
        assert child_past_ttl.post("/api/v1/voice/syntheses", headers=child_headers, json={
            "readingSessionId": sid, "pageNumber": 1, "revision": 1
        }).status_code == 401
        assert child_past_ttl.post("/api/v1/auth/sessions", json={
            "account": "guardian-one", "password": "strong-pass-123"
        }).status_code == 403
        unlock = child_past_ttl.post("/api/v1/auth/parent-unlocks", headers=child_headers,
                                     json={"account": "guardian-one", "password": "strong-pass-123"})
        assert unlock.status_code == 200
        g_headers = {"X-CSRF-Token": unlock.cookies["zhiya_csrf"]}
        detail = child_past_ttl.get(f"/api/v1/reading-sessions/{sid}")
        assert detail.status_code == 200
        assert detail.json()["data"]["session"]["status"] == "FINISHED"
        assert detail.json()["data"]["report"]["status"] == "READY"
        assert "第 1 页" in detail.json()["data"]["report"]["summary"]
        # Repeated completion checks return terminal FINISHED/READY state without duplicate model generation.
        comp = child_past_ttl.post(f"/api/v1/reading-sessions/{sid}/completions", headers=g_headers, json={
            "reason": "USER_FINISH", "expectedRevision": 2, "clientEventId": "after-timeout"
        })
        assert comp.status_code == 200 and comp.json()["data"]["report"]["status"] == "READY"
    assert len(adapter.calls) == 1
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT status, end_reason, revision FROM reading_sessions WHERE id = ?", (sid,)).fetchone() == (
            "FINISHED", "TIME_LIMIT", 2
        )


def test_guardian_read_after_expiry_settles_time_limit_once(child_env, monkeypatch):
    application, first, _, path, profile_id = child_env
    h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}

    class CountingAdapter:
        def __init__(self):
            self.calls = []

        async def generate(self, req):
            self.calls.append(req)
            return ModelGenerateResult(success=True, text='{"suggestionId":"NONE"}')

    adapter = CountingAdapter()
    monkeypatch.setattr(application.state, "model_adapter", adapter)

    with closing(TestClient(application, base_url="https://testserver")) as second_device:
        assert second_device.post("/api/v1/auth/sessions",
                                  json={"account": "guardian-one", "password": "strong-pass-123"}).status_code == 201
        sid = first.post("/api/v1/reading-sessions", headers=h1, json={
            "childProfileId": profile_id, "storyVersionId": 11, "mode": "PARENT_CHILD"
        }).json()["data"]["readingSessionId"]
        assert first.put(f"/api/v1/reading-sessions/{sid}/mode-settings", headers=h1, json={
            "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11], "timeLimitSeconds": 600
        }).status_code == 200
        ch = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
        assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=ch, json={
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "s1"
        }).status_code == 201

        # Grant expires without any further request from child device; guardian checks twice.
        with closing(sqlite3.connect(path)) as db:
            expires_at = db.execute("SELECT expires_at FROM child_grants").fetchone()[0]
        monkeypatch.setattr(time, "time", lambda: expires_at / 1000 + 7200)
        r1 = second_device.get(f"/api/v1/reading-sessions/{sid}")
        r2 = second_device.get(f"/api/v1/reading-sessions/{sid}")
        assert r1.status_code == 200 and r1.json()["data"]["session"]["status"] == "FINISHED"
        assert r1.json()["data"]["report"]["status"] == "PENDING"
        assert r2.status_code == 200 and r2.json()["data"]["report"]["status"] == "READY"
        assert r2.json()["data"]["report"]["summary"] == "本次共读阅读了第 1 页。"
        assert len(adapter.calls) == 1
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT ended_at FROM reading_sessions WHERE id=?", (sid,)).fetchone() == (expires_at,)
        assert second_device.get("/api/v1/reading-sessions").json()["data"]["items"][0]["status"] == "FINISHED"


def test_child_and_other_guardian_device_logout_revoke_grants(child_env):
    application, first, _, path, profile_id = child_env
    h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    sid = first.post("/api/v1/reading-sessions", headers=h1, json={
        "childProfileId": profile_id, "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    assert first.put(f"/api/v1/reading-sessions/{sid}/mode-settings", headers=h1, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11], "timeLimitSeconds": 600
    }).status_code == 200
    child_token = first.cookies["zhiya_session"]
    child_headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    assert first.delete("/api/v1/auth/sessions/current", headers=child_headers).status_code == 204
    with TestClient(application, base_url="https://testserver") as replay:
        replay.cookies.set("zhiya_session", child_token)
        assert replay.get("/api/v1/storybooks").status_code == 401
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT revoked_at FROM child_grants").fetchone()[0] is not None
        assert db.execute("SELECT status, end_reason FROM reading_sessions WHERE id = ?", (sid,)).fetchone() == (
            "INTERRUPTED", "GRANT_REVOKED"
        )

    # Another logged-in device of the same guardian account logging out also revokes active account child grants.
    with TestClient(application, base_url="https://testserver") as dev_a, \
            TestClient(application, base_url="https://testserver") as dev_b:
        for dev in (dev_a, dev_b):
            assert dev.post("/api/v1/auth/sessions",
                            json={"account": "guardian-one", "password": "strong-pass-123"}).status_code == 201
        ha = {"X-CSRF-Token": dev_a.cookies["zhiya_csrf"]}
        hb = {"X-CSRF-Token": dev_b.cookies["zhiya_csrf"]}
        sid2 = dev_a.post("/api/v1/reading-sessions", headers=ha, json={
            "childProfileId": profile_id, "storyVersionId": 11, "mode": "PARENT_CHILD"
        }).json()["data"]["readingSessionId"]
        assert dev_a.put(f"/api/v1/reading-sessions/{sid2}/mode-settings", headers=ha, json={
            "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11], "timeLimitSeconds": 600
        }).status_code == 200
        assert dev_b.delete("/api/v1/auth/sessions/current", headers=hb).status_code == 204
        assert dev_a.get(f"/api/v1/reading-sessions/{sid2}").status_code == 401
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT status, end_reason FROM reading_sessions WHERE id = ?", (sid2,)).fetchone() == (
                "INTERRUPTED", "GRANT_REVOKED"
            )


def _enter_session(client, profile_id, *, seconds=600):
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    created = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile_id, "storyVersionId": 11, "mode": "PARENT_CHILD",
    })
    assert created.status_code == 201
    sid = created.json()["data"]["readingSessionId"]
    entered = client.put(f"/api/v1/reading-sessions/{sid}/mode-settings", headers=headers, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11], "timeLimitSeconds": seconds,
    })
    assert entered.status_code == 200
    return sid


def _start(client, sid):
    response = client.post(f"/api/v1/reading-sessions/{sid}/events",
                           headers={"X-CSRF-Token": client.cookies["zhiya_csrf"]}, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start",
    })
    assert response.status_code == 201


@pytest.fixture
def timed_session(child_env, monkeypatch):
    application, child, _, path, profile_id = child_env
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"suggestionId":"NONE"}')
    monkeypatch.setattr(application.state, "model_adapter", model)
    sid = _enter_session(child, profile_id, seconds=60)
    with closing(sqlite3.connect(path)) as db:
        expires_at = db.execute("SELECT expires_at FROM child_grants").fetchone()[0]
    with closing(TestClient(application, base_url="https://testserver")) as guardian:
        assert guardian.post("/api/v1/auth/sessions", json={
            "account": "guardian-one", "password": "strong-pass-123",
        }).status_code == 201
        yield guardian, sid, expires_at, model


def test_reads_settle_only_the_authenticated_family_or_child_grant(child_env, timed_session, monkeypatch):
    application, child, other_family, path, profile_id = child_env
    _, sid, expires_at, model = timed_session
    _start(child, sid)
    with closing(TestClient(application, base_url="https://testserver")) as sibling:
        assert sibling.post("/api/v1/auth/sessions", json={
            "account": "guardian-one", "password": "strong-pass-123",
        }).status_code == 201
        other_sid = _enter_session(sibling, profile_id, seconds=1800)
        monkeypatch.setattr(time, "time", lambda: expires_at / 1000 + 1)
        assert other_family.get("/api/v1/storybooks").status_code == 200
        assert sibling.get(f"/api/v1/reading-sessions/{other_sid}").status_code == 200
        with closing(TestClient(application, base_url="https://testserver")) as anonymous:
            assert anonymous.get("/api/v1/child-profiles").status_code == 401
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT status FROM reading_sessions WHERE id=?", (sid,)).fetchone() == ("IN_PROGRESS",)
        model.generate.assert_not_awaited()
        assert child.get("/api/v1/media/missing").status_code == 401
        model.generate.assert_awaited_once()


def test_child_session_detail_returns_current_page_events(child_env, timed_session):
    _, child, _, _, _ = child_env
    guardian, sid, _, _ = timed_session
    _start(child, sid)
    url = f"/api/v1/reading-sessions/{sid}"
    assert child.post(url + "/events", headers={"X-CSRF-Token": child.cookies["zhiya_csrf"]}, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "next",
    }).status_code == 201
    child_view = child.get(url).json()["data"]
    assert child_view["report"] is None
    assert [e["pageNumber"] for e in child_view["events"]] == [2]
    assert [e["pageNumber"] for e in guardian.get(url).json()["data"]["events"]] == [1, 2]


@pytest.mark.parametrize("withdrawn, reason", [(False, "GRANT_REVOKED"), (True, "CONTENT_WITHDRAWN")])
def test_interrupted_grants_never_generate_completion_reports(child_env, timed_session, monkeypatch, withdrawn, reason):
    _, child, _, path, _ = child_env
    guardian, sid, expires_at, model = timed_session
    url = f"/api/v1/reading-sessions/{sid}"
    if withdrawn:
        _start(child, sid)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE story_versions SET status='WITHDRAWN' WHERE id=11")
        headers = {"X-CSRF-Token": guardian.cookies["zhiya_csrf"]}
        assert guardian.put(url + "/mode-settings", headers=headers, json={
            "mode": "CHILD_SOLO", "allowedStoryVersionIds": [11], "timeLimitSeconds": 60,
        }).status_code == 404
    monkeypatch.setattr(time, "time", lambda: expires_at / 1000 + 1)
    assert guardian.get(url).status_code == (404 if withdrawn else 200)
    assert child.post("/api/v1/auth/parent-unlocks", headers={"X-CSRF-Token": child.cookies["zhiya_csrf"]},
                      json={"account": "guardian-one", "password": "strong-pass-123"}).status_code == 200
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT status, end_reason FROM reading_sessions WHERE id=?", (sid,)).fetchone() == (
            "INTERRUPTED", reason,
        )
        assert db.execute("SELECT COUNT(*) FROM reading_reports WHERE session_id=?", (sid,)).fetchone() == (0,)
    model.generate.assert_not_awaited()


def test_rolled_back_logout_does_not_dispatch_report(child_env, timed_session, monkeypatch):
    _, child, _, path, _ = child_env
    guardian, sid, expires_at, model = timed_session
    _start(child, sid)
    monkeypatch.setattr(time, "time", lambda: expires_at / 1000 + 1)
    with closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TRIGGER fail_logout BEFORE UPDATE OF revoked_at ON auth_sessions
                      BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
    assert guardian.delete("/api/v1/auth/sessions/current",
                           headers={"X-CSRF-Token": guardian.cookies["zhiya_csrf"]}).status_code == 503
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT status FROM reading_sessions WHERE id=?", (sid,)).fetchone() == ("IN_PROGRESS",)
        assert db.execute("SELECT COUNT(*) FROM reading_reports WHERE session_id=?", (sid,)).fetchone() == (0,)
    model.generate.assert_not_awaited()
    assert guardian.get(f"/api/v1/reading-sessions/{sid}").status_code == 200
    model.generate.assert_awaited_once()


def test_expiry_read_recovers_after_startup_lock_without_health_probe(child_env, timed_session, monkeypatch):
    _, child, _, path, _ = child_env
    guardian, sid, expires_at, model = timed_session
    _start(child, sid)
    monkeypatch.setattr(time, "time", lambda: expires_at / 1000 + 1)
    restarted = create_app(Settings(), SpeechSettings())
    with closing(sqlite3.connect(path)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        with TestClient(restarted, base_url="https://testserver") as client:
            client.cookies.update(guardian.cookies)
            monkeypatch.setattr(restarted.state, "model_adapter", model)
            lock.rollback()
            result = client.get(f"/api/v1/reading-sessions/{sid}")
            assert result.status_code == 200
            assert result.json()["data"]["session"]["status"] == "FINISHED"
    model.generate.assert_awaited_once()
