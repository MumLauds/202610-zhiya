"""Tests for API-007 (delete child profile) and API-014 (delete reading session) cleanup."""

import asyncio
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app.api.v1.auth import _login_attempts
from app.main import app
from app.schemas.agent import ModelGenerateResult
from app.services.voice_access import VoicePrincipal, VoiceSnapshot

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema.sql"


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "deletes.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
    _login_attempts.clear()
    with TestClient(app, base_url="https://testserver") as first, TestClient(app, base_url="https://testserver") as second:
        for client, account in ((first, "guardian-one"), (second, "guardian-two")):
            creds = {"account": account, "password": "strong-pass-123"}
            assert client.post("/api/v1/auth/registrations", json=creds).status_code == 201
            assert client.post("/api/v1/auth/sessions", json=creds).status_code == 201
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("INSERT INTO storybooks VALUES (1, NULL, 'LIBRARY', 1)")
            db.execute("""INSERT INTO story_versions
                (id, storybook_id, version_no, title, topics_json, age_min, age_max,
                 license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
                VALUES (11, 1, 1, '测试绘本', '["动物"]', 3, 6, 'ok', 'PUBLISHED', 1, 1, 1, 0)""")
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (11, 2, 'p2', 'none')")
            db.execute("""INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number)
                VALUES (11, 1, 'p1', 'none', 2)""")
            db.execute("""INSERT INTO knowledge_items
                (id, version_no, item_key, title, source_title, source_ref, locator, age_band,
                 topics_json, task_types_json, content, status, reviewed_at, created_at)
                VALUES (100, 1, 'k1', 'k', 'src', 'ref', 'L1', NULL, '[]', '["GUIDANCE"]', 'body', 'APPROVED', 1, 1)""")
        yield first, second, path
    app.state.voice_media.clear()
    _login_attempts.clear()


def _seed_derived_data(path: Path, profile_id: int, session_id: int, grant_id: int, auth_session_id: int,
                       knowledge_version_id: int | None = None):
    now = int(time.time() * 1000)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("""INSERT INTO child_grants
            (id, profile_id, issued_by_session_id, token_digest, issued_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (grant_id, profile_id, auth_session_id, f"digest-{grant_id}", now, now + 600000))
        db.execute("INSERT INTO child_grant_books VALUES (?, 11)", (grant_id,))
        db.execute("UPDATE reading_sessions SET grant_id = ?, mode = 'CHILD_SOLO' WHERE id = ?",
                   (grant_id, session_id))
        event_id = db.execute("SELECT id FROM interaction_events WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()[0]
        db.execute("INSERT INTO event_evidence VALUES (?, 100, 1)", (event_id,))
        if knowledge_version_id is not None:
            db.execute("""INSERT INTO story_versions
                (id, storybook_id, version_no, title, topics_json, age_min, age_max,
                 license_note, status, created_at, source_version_id, source_session_id,
                 source_revision, source_page_no, resume_page_no, source_visited_pages_json, branching)
                VALUES (?, 1, 2, '续写草稿', '[]', 3, 6, 'ok', 'DRAFT', 1, 11, ?, 1, 1, 2, '[1]', 0)""",
                (knowledge_version_id, session_id))


def test_deletion_status_checks_ownership_even_if_the_book_was_withdrawn(env):
    owner, other, path = env
    headers = {"X-CSRF-Token": owner.cookies["zhiya_csrf"]}
    profile = owner.post("/api/v1/child-profiles", headers=headers,
                         json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]
    sessions = "/api/v1/reading-sessions"
    session_id = owner.post(sessions, headers=headers, json={
        "childProfileId": profile["childProfileId"], "storyVersionId": 11, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    resource = f"{sessions}/{session_id}"
    query = {"view": "deletion-status"}
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'WITHDRAWN' WHERE id = 11")
    assert owner.get(sessions).json()["data"]["items"] == []
    assert owner.get(resource).status_code == 404
    status = owner.get(resource, params=query)
    assert status.status_code == 200
    assert status.headers["Cache-Control"] == "no-store"
    assert status.json()["data"] == {"exists": True}
    assert other.get(resource, params=query).json()["data"] == {"exists": False}
    with TestClient(app, base_url="https://testserver") as anonymous:
        assert anonymous.get(resource, params=query).status_code == 401
    assert owner.delete(resource, headers=headers).status_code == 204
    assert owner.get(resource, params=query).json()["data"] == {"exists": False}


def test_delete_reading_session_cleans_rows_grants_media_and_enforces_auth(env, monkeypatch, tmp_path):
    first, second, path = env
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    other_headers = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers,
                         json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]
    sid1 = first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile["childProfileId"], "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    sid2 = first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile["childProfileId"], "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    for sid in (sid1, sid2):
        assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": f"s-{sid}"
        }).status_code == 201
    assert first.post(f"/api/v1/reading-sessions/{sid1}/completions", headers=headers, json={
        "reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "end-1"
    }).status_code == 200
    _seed_derived_data(path, profile["childProfileId"], sid1, grant_id=501, auth_session_id=1, knowledge_version_id=12)

    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.delete(f"/api/v1/reading-sessions/{sid1}").status_code == 401
    app.state.voice_media.put(VoicePrincipal(1), VoiceSnapshot(sid1, 11, 1, 1, "p1"), "v", "NORMAL", "m", b"WAV1")
    app.state.voice_media.put(VoicePrincipal(1), VoiceSnapshot(sid2, 11, 1, 1, "p1"), "v", "NORMAL", "m", b"WAV2")
    assert first.delete(f"/api/v1/reading-sessions/{sid1}").status_code == 403
    assert second.delete(f"/api/v1/reading-sessions/{sid1}", headers=other_headers).status_code == 404
    assert first.delete("/api/v1/reading-sessions/0", headers=headers).status_code == 422

    # Owner can still delete their session even if the storybook version was later withdrawn.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'WITHDRAWN' WHERE id = 11")
    deleted = first.delete(f"/api/v1/reading-sessions/{sid1}", headers=headers)
    assert deleted.status_code == 204 and deleted.content == b""
    assert first.delete(f"/api/v1/reading-sessions/{sid1}", headers=headers).status_code == 404

    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT id FROM reading_sessions WHERE id = ?", (sid1,)).fetchone() is None
        assert db.execute("SELECT id FROM reading_sessions WHERE id = ?", (sid2,)).fetchone() is not None
        assert db.execute("SELECT COUNT(*) FROM interaction_events WHERE session_id = ?", (sid1,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM reading_reports WHERE session_id = ?", (sid1,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM request_executions WHERE session_id = ?", (sid1,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0] == 0
        assert db.execute("SELECT revoked_at FROM child_grants WHERE id = 501").fetchone()[0] is not None
        assert db.execute("SELECT source_session_id FROM story_versions WHERE id = 12").fetchone()[0] is None
    assert [v.snapshot.session_id for v in app.state.voice_media.items.values()] == [sid2]

    monkeypatch.setenv("ZHIYA_DB_PATH", str(tmp_path / "missing.sqlite3"))
    err = first.delete(f"/api/v1/reading-sessions/{sid2}", headers=headers)
    assert err.status_code == 503 and err.json()["error"]["retryable"] is False


def test_delete_child_profile_cleans_all_descendants_and_preserves_siblings(env, monkeypatch, tmp_path):
    first, second, path = env
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    other_headers = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}
    p1 = first.post("/api/v1/child-profiles", headers=headers,
                    json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    p2 = first.post("/api/v1/child-profiles", headers=headers,
                    json={"ageBand": "AGE_4_5", "preferredMinutes": 15}).json()["data"]["childProfileId"]
    s1 = first.post("/api/v1/reading-sessions", headers=headers,
                    json={"childProfileId": p1, "storyVersionId": 11, "mode": "PARENT_CHILD"}).json()["data"]["readingSessionId"]
    s2 = first.post("/api/v1/reading-sessions", headers=headers,
                    json={"childProfileId": p2, "storyVersionId": 11, "mode": "PARENT_CHILD"}).json()["data"]["readingSessionId"]
    for sid in (s1, s2):
        assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": f"s-{sid}"
        }).status_code == 201
    assert first.post(f"/api/v1/reading-sessions/{s1}/completions", headers=headers, json={
        "reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "end"
    }).status_code == 200
    _seed_derived_data(path, p1, s1, grant_id=601, auth_session_id=1)

    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.delete(f"/api/v1/child-profiles/{p1}").status_code == 401
    app.state.voice_media.put(VoicePrincipal(1), VoiceSnapshot(s1, 11, 1, 1, "p1"), "v", "NORMAL", "m", b"WAV1")
    app.state.voice_media.put(VoicePrincipal(1), VoiceSnapshot(s2, 11, 1, 1, "p1"), "v", "NORMAL", "m", b"WAV2")
    assert first.delete(f"/api/v1/child-profiles/{p1}").status_code == 403
    assert second.delete(f"/api/v1/child-profiles/{p1}", headers=other_headers).status_code == 404
    assert first.delete("/api/v1/child-profiles/0", headers=headers).status_code == 422

    deleted = first.delete(f"/api/v1/child-profiles/{p1}", headers=headers)
    assert deleted.status_code == 204 and deleted.content == b""
    assert first.delete(f"/api/v1/child-profiles/{p1}", headers=headers).status_code == 404
    assert [item["childProfileId"] for item in first.get("/api/v1/child-profiles").json()["data"]["items"]] == [p2]
    assert [item["readingSessionId"] for item in first.get("/api/v1/reading-sessions").json()["data"]["items"]] == [s2]

    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM child_profiles WHERE id = ?", (p1,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM child_grants WHERE profile_id = ?", (p1,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM child_grant_books WHERE grant_id = 601").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM reading_sessions WHERE profile_id = ?", (p1,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM interaction_events WHERE session_id = ?", (s1,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM reading_reports WHERE session_id = ?", (s1,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM request_executions WHERE session_id = ?", (s1,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM reading_sessions WHERE id = ?", (s2,)).fetchone()[0] == 1
    assert [v.snapshot.session_id for v in app.state.voice_media.items.values()] == [s2]

    monkeypatch.setenv("ZHIYA_DB_PATH", str(tmp_path / "missing.sqlite3"))
    err = first.delete(f"/api/v1/child-profiles/{p2}", headers=headers)
    assert err.status_code == 503 and err.json()["error"]["retryable"] is False
    assert err.json()["requestId"] == err.headers["X-Request-ID"]


def test_concurrent_deletes_and_non_guardian_forbidden(env):
    first, _, path = env
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    p1 = first.post("/api/v1/child-profiles", headers=headers,
                    json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    p2 = first.post("/api/v1/child-profiles", headers=headers,
                    json={"ageBand": "AGE_4_5", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    s_src = first.post("/api/v1/reading-sessions", headers=headers,
                       json={"childProfileId": p1, "storyVersionId": 11, "mode": "PARENT_CHILD"}).json()["data"]["readingSessionId"]
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("""INSERT INTO reading_sessions
            (profile_id, version_id, current_page_no, mode, status, source_reading_session_id, created_at)
            VALUES (?, 11, 1, 'PARENT_CHILD', 'CREATED', ?, 1)""", (p2, s_src))
        derived_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        s_results = list(pool.map(lambda _: first.delete(f"/api/v1/reading-sessions/{s_src}", headers=headers), range(2)))
        p_results = list(pool.map(lambda _: first.delete(f"/api/v1/child-profiles/{p1}", headers=headers), range(2)))
    assert sorted(r.status_code for r in s_results) == [204, 404]
    assert sorted(r.status_code for r in p_results) == [204, 404]
    for r in s_results + p_results:
        if r.status_code == 404:
            assert r.json()["requestId"] == r.headers["X-Request-ID"]
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT source_reading_session_id FROM reading_sessions WHERE id = ?",
                          (derived_id,)).fetchone()[0] is None
        db.execute("UPDATE accounts SET role = 'ADMIN' WHERE id = 1")
    for res in (
        first.delete(f"/api/v1/reading-sessions/{derived_id}", headers=headers),
        first.delete(f"/api/v1/child-profiles/{p2}", headers=headers),
    ):
        assert res.status_code == 403 and res.json()["error"]["code"] == "FORBIDDEN"
        assert res.json()["requestId"] == res.headers["X-Request-ID"]


@pytest.mark.parametrize("delete_target, stale_result, stale_exc", [
    ("session", ModelGenerateResult(success=True, text=json.dumps({"suggestionId": "TALK_ABOUT_STORY"})), None),
    ("profile", None, RuntimeError("stale boom")),
])
def test_stale_report_task_does_not_overwrite_reused_session_id(env, monkeypatch, delete_target, stale_result, stale_exc):
    first, second, path = env
    headers1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    headers2 = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}
    p1 = first.post("/api/v1/child-profiles", headers=headers1,
                    json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    p2 = second.post("/api/v1/child-profiles", headers=headers2,
                     json={"ageBand": "AGE_4_5", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    sid1 = first.post("/api/v1/reading-sessions", headers=headers1, json={
        "childProfileId": p1, "storyVersionId": 11, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    assert first.post(f"/api/v1/reading-sessions/{sid1}/events", headers=headers1, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start-1",
    }).status_code == 201

    g1_started, g1_release = Event(), Event()
    g2_started, g2_release = Event(), Event()

    class PausedAdapter:
        def __init__(self, started: Event, release: Event, result=None, exc=None):
            self.started, self.release, self.result, self.exc = started, release, result, exc

        async def generate(self, request):
            self.started.set()
            assert await asyncio.to_thread(self.release.wait, 10)
            if self.exc is not None:
                raise self.exc
            return self.result

    current_adapter = [PausedAdapter(g1_started, g1_release, result=stale_result, exc=stale_exc)]

    class SwitchingAdapter:
        async def generate(self, request):
            return await current_adapter[0].generate(request)

    monkeypatch.setattr(app.state, "model_adapter", SwitchingAdapter(), raising=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(first.post, f"/api/v1/reading-sessions/{sid1}/completions", headers=headers1,
                         json={"reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "end-1"})
        try:
            assert g1_started.wait(5)
            del_url = f"/api/v1/reading-sessions/{sid1}" if delete_target == "session" else f"/api/v1/child-profiles/{p1}"
            assert first.delete(del_url, headers=headers1).status_code == 204

            sid2 = second.post("/api/v1/reading-sessions", headers=headers2, json={
                "childProfileId": p2, "storyVersionId": 11, "mode": "PARENT_CHILD",
            }).json()["data"]["readingSessionId"]
            assert sid2 == sid1  # Ensure SQLite reused the deleted reading_sessions.id.
            assert second.post(f"/api/v1/reading-sessions/{sid2}/events", headers=headers2, json={
                "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start-2",
            }).status_code == 201

            current_adapter[0] = PausedAdapter(
                g2_started, g2_release,
                result=ModelGenerateResult(success=True, text=json.dumps({"suggestionId": "REVISIT_PAGE"})),
            )
            f2 = pool.submit(second.post, f"/api/v1/reading-sessions/{sid2}/completions", headers=headers2,
                             json={"reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "end-2"})
            try:
                assert g2_started.wait(5)
            finally:
                g1_release.set()
            assert f1.result(timeout=10).status_code == 200

            pending_view = second.get(f"/api/v1/reading-sessions/{sid2}").json()["data"]["report"]
            assert pending_view == {
                "readingSessionId": sid2,
                "summary": "本次共读阅读了第 1 页。",
                "activities": [],
                "status": "PENDING",
            }
        finally:
            g1_release.set()
            g2_release.set()
        assert f2.result(timeout=10).status_code == 200

    final_view = second.get(f"/api/v1/reading-sessions/{sid2}").json()["data"]["report"]
    assert final_view == {
        "readingSessionId": sid2,
        "summary": "本次共读阅读了第 1 页。可以和孩子再看一页已读内容，由孩子自愿选择是否聊一聊。",
        "activities": [],
        "status": "READY",
    }


def test_concurrent_voice_media_expire_and_delete_does_not_fail_get(env):
    first, second, _ = env
    headers1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    headers2 = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}
    p1 = first.post("/api/v1/child-profiles", headers=headers1,
                    json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    p2 = second.post("/api/v1/child-profiles", headers=headers2,
                     json={"ageBand": "AGE_4_5", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    sid_keep = first.post("/api/v1/reading-sessions", headers=headers1, json={
        "childProfileId": p1, "storyVersionId": 11, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    sid_del = second.post("/api/v1/reading-sessions", headers=headers2, json={
        "childProfileId": p2, "storyVersionId": 11, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    for client, hdrs, sid, cid in ((first, headers1, sid_keep, "start-keep"), (second, headers2, sid_del, "start-del")):
        assert client.post(f"/api/v1/reading-sessions/{sid}/events", headers=hdrs, json={
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": cid,
        }).status_code == 201

    keep_key, _ = app.state.voice_media.put(
        VoicePrincipal(1), VoiceSnapshot(sid_keep, 11, 1, 1, "p1"), "v", "NORMAL", "m", b"WAV-KEEP",
    )
    del_key, del_item = app.state.voice_media.put(
        VoicePrincipal(2), VoiceSnapshot(sid_del, 11, 1, 1, "p1"), "v", "NORMAL", "m", b"WAV-DEL",
    )

    expire_checked, purge_done = Event(), Event()

    class _BlockingExpiry:
        def __le__(self, other: float) -> bool:
            expire_checked.set()
            purge_done.wait(0.2)
            return True

    app.state.voice_media.items[del_key] = type(del_item)(
        del_item.principal, del_item.snapshot, del_item.voice_id, del_item.speech_rate,
        del_item.model, del_item.audio, _BlockingExpiry(),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_get = pool.submit(first.get, f"/api/v1/media/{keep_key}")
        assert expire_checked.wait(5)
        f_del = pool.submit(second.delete, f"/api/v1/reading-sessions/{sid_del}", headers=headers2)
        del_resp = f_del.result(timeout=10)
        purge_done.set()
        get_resp = f_get.result(timeout=10)

    assert del_resp.status_code == 204
    assert get_resp.status_code == 200 and get_resp.content == b"WAV-KEEP"
    assert [v.snapshot.session_id for v in app.state.voice_media.items.values()] == [sid_keep]
