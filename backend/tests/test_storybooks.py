"""Permission, filtering and serialization checks for the guardian bookshelf."""

import asyncio
import sqlite3
import hashlib
import json
import time
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app.api.v1.auth import _login_attempts
from app.main import app

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema.sql"


@pytest.fixture
def bookshelf(tmp_path, monkeypatch):
    path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
    _login_attempts.clear()
    with TestClient(app, base_url="https://testserver") as first, TestClient(app, base_url="https://testserver") as second:
        for client, account in ((first, "guardian-one"), (second, "guardian-two")):
            credentials = {"account": account, "password": "strong-pass-123"}
            assert client.post("/api/v1/auth/registrations", json=credentials).status_code == 201
            assert client.post("/api/v1/auth/sessions", json=credentials).status_code == 201
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            for book_id, owner, source in ((1, None, "LIBRARY"), (2, 1, "ORIGINAL"),
                                           (3, 2, "ORIGINAL"), (4, None, "LIBRARY")):
                db.execute("INSERT INTO storybooks VALUES (?, ?, ?, 1)", (book_id, owner, source))
            for version_id, book_id, version_no, status, reviewer in (
                (11, 1, 1, "PUBLISHED", 1), (21, 2, 1, "RETIRED", 1),
                (22, 2, 2, "APPROVED_PRIVATE", 1), (31, 3, 1, "APPROVED_PRIVATE", 1),
                (41, 4, 1, "DRAFT", None), (42, 4, 2, "WITHDRAWN", 1),
                (43, 4, 3, "RETIRED", 1),
            ):
                db.execute("""INSERT INTO story_versions
                    (id, storybook_id, version_no, title, topics_json, age_min, age_max,
                     license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'test', ?, ?, ?, 1, 1)""",
                    (version_id, book_id, version_no, f"story-{version_id}",
                     '["动物"]' if book_id == 1 else '["家庭"]',
                     3 if book_id == 1 else 5, 4 if book_id == 1 else 6,
                     status, reviewer, 1 if reviewer else None))
                db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (?, 1, ?, ?)",
                           (version_id, f"text-{version_id}", "/assets/placeholder.png"))
            db.execute("INSERT INTO story_branches VALUES (1, 11, 1, 1, 'approved', 'APPROVED')")
            db.execute("INSERT INTO story_branches VALUES (2, 11, 1, 1, 'pending', 'PENDING')")
        yield first, second, path
    _login_attempts.clear()


def test_admin_catalogue_lists_only_library_versions(bookshelf):
    admin, guardian, path = bookshelf
    url = "/api/v1/admin/story-versions"
    assert guardian.get(url).status_code == 403
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE accounts SET role = 'ADMIN' WHERE account = 'guardian-one'")
    items = admin.get(url).json()["data"]["items"]
    assert [item["storyVersionId"] for item in items] == [11, 43, 42, 41]
    assert items[-1]["pages"] == [{"pageNumber": 1, "text": "text-41"}]
    assert [branch["reviewStatus"] for branch in items[0]["branches"]] == ["APPROVED", "PENDING"]
    assert admin.get(url, params={"page": 2, "pageSize": 2}).json()["data"]["items"] == items[2:]


def test_guardian_visibility_history_and_private_isolation(bookshelf):
    first, second, _ = bookshelf
    def ids(client, **params):
        response = client.get("/api/v1/storybooks", params=params)
        assert response.status_code == 200
        return [v["storyVersionId"] for v in response.json()["data"]["items"]]
    assert ids(first) == [11, 22]
    assert ids(first, scope="mine") == [11, 22, 21]
    assert ids(second, scope="mine") == [11, 31]
    assert first.get("/api/v1/story-versions/21").status_code == 200
    for version in (31, 41, 42, 43, 999):
        response = first.get(f"/api/v1/story-versions/{version}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_filters_pagination_and_safe_media_projection(bookshelf):
    first, _, _ = bookshelf
    result = first.get("/api/v1/storybooks", params={"ageBand": "AGE_3_4", "topic": "动物"}).json()["data"]
    assert [item["storyVersionId"] for item in result["items"]] == [11]
    assert first.get("/api/v1/storybooks", params={"topic": "无"}).json()["data"]["items"] == []
    assert first.get("/api/v1/storybooks", params={"page": 2, "pageSize": 1}).json()["data"]["items"][0]["storyVersionId"] == 22
    assert first.get("/api/v1/storybooks", params={"pageSize": 51}).status_code == 422
    book = first.get("/api/v1/story-versions/11").json()["data"]
    assert book["coverRef"] is None and book["pages"][0]["imageRef"] is None
    assert book["pages"][0]["text"] == "text-11"
    assert [branch["branchId"] for branch in book["branches"]] == [1]


def test_authentication_and_dependency_failure(bookshelf, monkeypatch, tmp_path):
    first, _, _ = bookshelf
    with TestClient(app, base_url="https://testserver") as anonymous:
        assert anonymous.get("/api/v1/storybooks").status_code == 401
        assert anonymous.get("/api/v1/story-versions/11").status_code == 401
    monkeypatch.setenv("ZHIYA_DB_PATH", str(tmp_path / "missing.sqlite3"))
    response = first.get("/api/v1/storybooks")
    assert response.status_code == 503 and response.json()["error"]["retryable"] is True


def test_reading_session_create_recovery_and_isolation(bookshelf):
    first, second, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    other_headers = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers,
                         json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]
    body = {"childProfileId": profile["childProfileId"], "storyVersionId": 11, "mode": "PARENT_CHILD"}
    assert first.post("/api/v1/reading-sessions", json=body).status_code == 403
    assert second.post("/api/v1/reading-sessions", headers=other_headers, json=body).status_code == 404
    assert first.post("/api/v1/reading-sessions", headers=headers,
                      json={**body, "storyVersionId": 31}).status_code == 404
    assert first.post("/api/v1/reading-sessions", headers=headers,
                      json={**body, "storyVersionId": 41}).status_code == 404
    # Continuation switching is deliberately not accepted by the ordinary create path.
    with TestClient(app, base_url="https://testserver") as anon:
        anon_res = anon.get("/api/v1/reading-sessions")
        assert anon_res.status_code == 401 and anon_res.json()["requestId"] == anon_res.headers["X-Request-ID"]
    invalid_res = first.post("/api/v1/reading-sessions", headers=headers, json={**body, "sourceReadingSessionId": 1})
    assert invalid_res.status_code == 422 and invalid_res.json()["requestId"] == invalid_res.headers["X-Request-ID"]
    response = first.post("/api/v1/reading-sessions", headers=headers, json=body)
    assert response.status_code == 201 and response.json()["requestId"] == response.headers["X-Request-ID"]
    session = response.json()["data"]
    session_id = session["readingSessionId"]
    assert (session["currentPage"], session["visitedPageNumbers"], session["status"], session["revision"]) == (1, [], "CREATED", 0)
    assert first.get(f"/api/v1/reading-sessions/{session_id}").json()["data"] == {
        "session": {**session, "childProfileId": profile["childProfileId"], "sourceType": "LIBRARY"},
        "events": [], "report": None}
    assert second.get(f"/api/v1/reading-sessions/{session_id}").status_code == 404
    assert second.get("/api/v1/reading-sessions").json()["data"]["items"] == []
    assert first.get("/api/v1/reading-sessions").json()["data"]["items"] == [session]
    assert first.get("/api/v1/reading-sessions", params={"pageSize": 51}).status_code == 422
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'WITHDRAWN' WHERE id = 11")
    assert first.get(f"/api/v1/reading-sessions/{session_id}").status_code == 404
    assert first.get("/api/v1/reading-sessions").json()["data"]["items"] == []


def test_reading_session_history_and_report_projection(bookshelf):
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers,
                         json={"ageBand": "AGE_5_6", "preferredMinutes": 10}).json()["data"]
    body = {"childProfileId": profile["childProfileId"], "storyVersionId": 21, "mode": "PARENT_CHILD"}
    result = first.post("/api/v1/reading-sessions", headers=headers, json=body)
    assert result.status_code == 201  # reviewed, owned private history
    session_id = result.json()["data"]["readingSessionId"]
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO reading_reports VALUES (?, 'PENDING', '事实摘要', '[{\"name\":\"hidden\"}]', 1)", (session_id,))
    report = first.get(f"/api/v1/reading-sessions/{session_id}").json()["data"]["report"]
    assert report == {"readingSessionId": session_id, "status": "PENDING", "summary": "事实摘要", "activities": []}
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE reading_reports SET status = 'READY' WHERE session_id = ?", (session_id,))
    assert first.get(f"/api/v1/reading-sessions/{session_id}").json()["data"]["report"]["activities"] == [{"name": "hidden"}]


def test_reading_event_transitions_and_idempotency(bookshelf):
    first, second, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    other = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (11, 2, 'second', 'none')")
        db.execute("UPDATE story_pages SET default_next_page_number = 2 WHERE version_id = 11 AND page_no = 1")
    profile = first.post("/api/v1/child-profiles", headers=headers,
                         json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]
    session = first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile["childProfileId"], "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    url = f"/api/v1/reading-sessions/{session}/events"
    started = {"eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"}
    assert second.post(url, headers=other, json=started).status_code == 404
    csrf_err = first.post(url, json=started)
    assert csrf_err.status_code == 403 and csrf_err.json()["requestId"] == csrf_err.headers["X-Request-ID"]
    initial = first.post(url, headers=headers, json=started)
    assert initial.status_code == 201 and initial.json()["data"]["revision"] == 1
    assert initial.json()["requestId"] == initial.headers["X-Request-ID"]
    replay = first.post(url, headers=headers, json=started)
    assert replay.status_code == 200 and replay.json()["data"] == initial.json()["data"]
    assert replay.json()["requestId"] == replay.headers["X-Request-ID"] != initial.json()["requestId"]
    conflict = first.post(url, headers=headers, json={**started, "pageNumber": 2})
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict.json()["requestId"] == conflict.headers["X-Request-ID"]
    change = {"eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "page"}
    assert first.post(url, headers=headers, json={**change, "pageNumber": 5, "clientEventId": "bad-page"}).status_code == 409
    assert first.post(url, headers=headers, json=change).status_code == 201
    assert first.post(url, headers=headers, json={**change, "clientEventId": "stale"}).status_code == 409
    data = first.get(f"/api/v1/reading-sessions/{session}").json()["data"]
    assert data["session"]["visitedPageNumbers"] == [1, 2]
    assert data["session"]["revision"] == 2 and len(data["events"]) == 2
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""INSERT INTO request_executions
            (owner_account_id, session_id, scope_type, scope_id, client_request_id, request_digest,
             status, execution_token, started_at, deadline_at) VALUES (1, ?, 'SESSION_EVENT', ?,
             'other-endpoint', 'different-digest', 'RUNNING', 'token', 1, 100)""", (session, session))
    collision = first.post(url, headers=headers, json={**change, "clientEventId": "other-endpoint"})
    assert collision.status_code == 409 and collision.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'WITHDRAWN' WHERE id = 11")
    assert first.post(url, headers=headers, json=started).status_code == 404


def test_running_request_expires_to_unknown_without_reexecution(bookshelf):
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers,
                         json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]
    session = first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile["childProfileId"], "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    url = f"/api/v1/reading-sessions/{session}/events"
    body = {"eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "running"}
    canonical = json.dumps({"method": "POST", "path": url, "body": body}, sort_keys=True,
                           separators=(',', ':'), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    now = int(time.time() * 1000)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""INSERT INTO request_executions
            (owner_account_id, session_id, scope_type, scope_id, client_request_id, request_digest,
             status, execution_token, started_at, deadline_at) VALUES (1, ?, 'SESSION_EVENT', ?,
             'running', ?, 'RUNNING', 'token', ?, ?)""", (session, session, digest, now, now + 60000))
    response = first.post(url, headers=headers, json=body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "REQUEST_IN_PROGRESS"
    assert response.json()["requestId"] == response.headers["X-Request-ID"]
    assert response.headers["Retry-After"] == "1"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE request_executions SET started_at = 1, deadline_at = 2 WHERE client_request_id = 'running'")
    timeout = first.post(url, headers=headers, json=body)
    assert timeout.status_code == 504 and timeout.json()["error"]["code"] == "REQUEST_OUTCOME_UNKNOWN"
    assert timeout.json()["requestId"] == timeout.headers["X-Request-ID"]
    unknown = first.post(url, headers=headers, json=body)
    assert unknown.status_code == 504 and unknown.json()["requestId"] == unknown.headers["X-Request-ID"]
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT status FROM request_executions WHERE client_request_id = 'running'").fetchone()[0] == "UNKNOWN"
        assert db.execute("SELECT COUNT(*) FROM interaction_events WHERE session_id = ?", (session,)).fetchone()[0] == 0


def test_concurrent_event_retries_commit_once(bookshelf):
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers,
                         json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]
    session = first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile["childProfileId"], "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    url = f"/api/v1/reading-sessions/{session}/events"
    body = {"eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "same-key"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: first.post(url, headers=headers, json=body), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].json()["data"] == responses[1].json()["data"]
    assert all(r.json()["requestId"] == r.headers["X-Request-ID"] for r in responses)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM interaction_events WHERE session_id = ?", (session,)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM request_executions WHERE session_id = ?", (session,)).fetchone()[0] == 1
        assert db.execute("SELECT revision FROM reading_sessions WHERE id = ?", (session,)).fetchone()[0] == 1


def _started_session(client, headers, path, pages=2):
    with closing(sqlite3.connect(path)) as db, db:
        for page in range(2, pages + 1):
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (11, ?, ?, 'none')",
                       (page, f"text-11-{page}"))
            db.execute("UPDATE story_pages SET default_next_page_number = ? WHERE version_id = 11 AND page_no = ?",
                       (page, page - 1))
    profile = client.post("/api/v1/child-profiles", headers=headers,
                          json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]
    session = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile["childProfileId"], "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    return session


def test_finish_reading_lifecycle_replay_and_conflicts(bookshelf):
    first, second, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    other = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}
    session = _started_session(first, headers, path)
    events = f"/api/v1/reading-sessions/{session}/events"
    url = f"/api/v1/reading-sessions/{session}/completions"
    finish = {"reason": "USER_FINISH", "expectedRevision": 0, "clientEventId": "end"}
    # CREATED sessions cannot be finished by the user.
    created = first.post(url, headers=headers, json={**finish, "clientEventId": "early"})
    assert created.status_code == 409 and created.json()["error"]["code"] == "SESSION_CONFLICT"
    assert first.post(events, headers=headers, json={"eventType": "STARTED", "pageNumber": 1,
                                                     "expectedRevision": 0, "clientEventId": "start"}).status_code == 201
    assert first.post(events, headers=headers, json={"eventType": "PAGE_CHANGED", "pageNumber": 2,
                                                     "expectedRevision": 1, "clientEventId": "page"}).status_code == 201
    assert first.post(url, json={**finish, "expectedRevision": 2}).status_code == 403
    assert second.post(url, headers=other, json={**finish, "expectedRevision": 2}).status_code == 404
    assert first.post(url, headers=headers, json={**finish, "reason": "TIME_LIMIT"}).status_code == 422
    assert first.post(url, headers=headers, json={**finish, "reason": "CONTINUED"}).status_code == 422
    # The SESSION_EVENT key space is shared with reading events.
    shared = first.post(url, headers=headers, json={**finish, "expectedRevision": 2, "clientEventId": "page"})
    assert shared.status_code == 409 and shared.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    stale = first.post(url, headers=headers, json={**finish, "expectedRevision": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "SESSION_CONFLICT"

    done = first.post(url, headers=headers, json={**finish, "clientEventId": "end-2", "expectedRevision": 2})
    assert done.status_code == 200 and done.json()["requestId"] == done.headers["X-Request-ID"]
    data = done.json()["data"]
    assert data["session"]["status"] == "FINISHED" and data["session"]["revision"] == 3
    assert data["session"]["visitedPageNumbers"] == [1, 2]
    assert data["report"] == {"readingSessionId": session, "summary": "本次共读阅读了第 1、2 页。",
                              "activities": [], "status": "PENDING"}
    # Without a configured model the post-commit generation settles to FAILED and keeps the facts.
    detail = first.get(f"/api/v1/reading-sessions/{session}")
    assert detail.status_code == 200 and detail.json()["requestId"] == detail.headers["X-Request-ID"]
    report = detail.json()["data"]["report"]
    assert report == {**data["report"], "status": "FAILED"}
    replay = first.post(url, headers=headers, json={**finish, "clientEventId": "end-2", "expectedRevision": 2})
    assert replay.status_code == 200 and replay.json()["data"] == done.json()["data"]  # original response, still PENDING
    assert replay.json()["requestId"] == replay.headers["X-Request-ID"] != done.json()["requestId"]
    changed = first.post(url, headers=headers, json={**finish, "clientEventId": "end-2", "expectedRevision": 3})
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    repeat = first.post(url, headers=headers, json={**finish, "clientEventId": "end-3", "expectedRevision": 3})
    assert repeat.status_code == 200
    assert repeat.json()["data"]["session"] == {**data["session"]}
    assert repeat.json()["data"]["report"]["status"] == "FAILED"
    late_page = first.post(events, headers=headers, json={"eventType": "PAGE_CHANGED", "pageNumber": 2,
                                                          "expectedRevision": 3, "clientEventId": "late"})
    assert late_page.status_code == 409 and late_page.json()["error"]["code"] == "SESSION_CONFLICT"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT status, end_reason, revision FROM reading_sessions WHERE id = ?",
                          (session,)).fetchone() == ("FINISHED", "USER_FINISH", 3)
        assert db.execute("SELECT COUNT(*) FROM reading_reports WHERE session_id = ?", (session,)).fetchone()[0] == 1


class _FakeAdapter:
    def __init__(self, result=None, exc=None):
        self.calls, self.result, self.exc = [], result, exc

    async def generate(self, request):
        self.calls.append(request)
        if self.exc:
            raise self.exc
        return self.result


@pytest.mark.parametrize("suggestion_id, suggestion, activity_name, materials", [
    ("NONE", "", "一起演一演", ["本次共读的绘本"]),
    ("REVISIT_PAGE", "可以和孩子再看一页已读内容，由孩子自愿选择是否聊一聊。", "一起演一演", ["本次共读的绘本"]),
    ("TALK_ABOUT_STORY", "可以邀请孩子聊聊故事中喜欢的部分，不必要求回答正确。", "一起演一演", ["本次共读的绘本"]),
    ("DRAW_PICTURE", "可以请孩子画一画今天最喜欢的一页，不用要求画得像。", "画出故事中的一幕", ["纸", "彩笔"]),
    ("FIND_IN_HOME", "可以和孩子在家里找一找和故事里一样的东西。", "找找故事里的物品", ["本次共读的绘本", "家中安全的小物品"]),
])
def test_finish_generates_ready_report_once_with_model(bookshelf, monkeypatch, suggestion_id, suggestion,
                                                      activity_name, materials):
    from app.schemas.agent import ModelGenerateResult
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    adapter = _FakeAdapter(ModelGenerateResult(success=True, text=json.dumps({"suggestionId": suggestion_id})))
    monkeypatch.setattr(app.state, "model_adapter", adapter, raising=False)
    session = _started_session(first, headers, path)
    first.post(f"/api/v1/reading-sessions/{session}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"})
    url = f"/api/v1/reading-sessions/{session}/completions"
    body = {"reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "end"}
    assert first.post(url, headers=headers, json=body).json()["data"]["report"]["status"] == "PENDING"
    report = first.get(f"/api/v1/reading-sessions/{session}").json()["data"]["report"]
    assert report["status"] == "READY" and report["summary"] == "本次共读阅读了第 1 页。" + suggestion
    activity, = report["activities"]
    assert set(activity) == {"name", "materials", "steps"}
    assert activity["name"] == activity_name and activity["materials"] == materials
    steps = activity["steps"]
    assert isinstance(steps, list) and len(steps) >= 2 and all(isinstance(step, str) and step.strip() for step in steps)
    assert "第 1 页" in steps[0]
    assert len(adapter.calls) == 1 and adapter.calls[0].user_input == "本次共读阅读了第 1 页。"
    assert "text-11" in adapter.calls[0].context_text
    # Replay and new-key repeats never call the model again.
    first.post(url, headers=headers, json=body)
    repeat = first.post(url, headers=headers, json={**body, "clientEventId": "again", "expectedRevision": 2})
    assert repeat.json()["data"]["report"] == report
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("suggestion_id, expected_status", [("NONE", "READY"), ("DRAW_PICTURE", "FAILED")])
def test_report_without_readable_text_has_no_activity(bookshelf, monkeypatch, suggestion_id, expected_status):
    from app.schemas.agent import ModelGenerateResult
    client, _, path = bookshelf
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    adapter = _FakeAdapter(ModelGenerateResult(success=True, text=json.dumps({"suggestionId": suggestion_id})))
    monkeypatch.setattr(app.state, "model_adapter", adapter, raising=False)
    session = _started_session(client, headers, path, pages=1)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_pages SET body_text = '  ' WHERE version_id = "
                   "(SELECT version_id FROM reading_sessions WHERE id = ?) AND page_no = 1", (session,))
    url = f"/api/v1/reading-sessions/{session}"
    assert client.post(f"{url}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"}).status_code == 201
    assert client.post(f"{url}/completions", headers=headers, json={
        "reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "end"}).status_code == 200
    assert client.get(url).json()["data"]["report"] == {
        "readingSessionId": session, "summary": "本次共读阅读了第 1 页。", "status": expected_status, "activities": [],
    }
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("model_text", [
    "正确回答十道问题，阅读能力低于同龄儿童。",
    "今天一起读完了两页，孩子很投入。",
    "孩子已经掌握了故事中的所有词语。",
    "建议用体罚帮助孩子集中注意力。",
    '{"suggestionId":"TALK_ABOUT_STORY","summary":"阅读能力低于同龄儿童"}',
    '{"suggestionId":"正确回答十道问题"}',
    '{"suggestionId":["TALK_ABOUT_STORY"]}',
    "[]", "null", "", "文" * 501,
])
def test_finish_rejects_unverified_model_content(bookshelf, monkeypatch, model_text):
    from app.schemas.agent import ModelGenerateResult
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    adapter = _FakeAdapter(ModelGenerateResult(success=True, text=model_text))
    monkeypatch.setattr(app.state, "model_adapter", adapter, raising=False)
    session = _started_session(first, headers, path, pages=1)
    base = f"/api/v1/reading-sessions/{session}"
    first.post(f"{base}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"})
    body = {"reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "end"}
    original = first.post(f"{base}/completions", headers=headers, json=body)
    assert original.status_code == 200
    assert original.json()["data"]["report"]["status"] == "PENDING"
    expected = {"readingSessionId": session, "summary": "本次共读阅读了第 1 页。",
                "activities": [], "status": "FAILED"}
    assert first.get(base).json()["data"]["report"] == expected
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT status, summary, activities_json FROM reading_reports WHERE session_id = ?",
                          (session,)).fetchone() == ("FAILED", expected["summary"], "[]")
    replay = first.post(f"{base}/completions", headers=headers, json=body)
    assert replay.json()["data"] == original.json()["data"]
    repeat = first.post(f"{base}/completions", headers=headers,
                        json={**body, "clientEventId": "again", "expectedRevision": 2})
    assert repeat.json()["data"]["report"] == expected
    assert len(adapter.calls) == 1


def test_finish_model_failure_keeps_fact_summary(bookshelf, monkeypatch):
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    monkeypatch.setattr(app.state, "model_adapter", _FakeAdapter(exc=RuntimeError("boom")), raising=False)
    session = _started_session(first, headers, path, pages=1)
    first.post(f"/api/v1/reading-sessions/{session}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"})
    first.post(f"/api/v1/reading-sessions/{session}/completions", headers=headers,
               json={"reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "end"})
    report = first.get(f"/api/v1/reading-sessions/{session}").json()["data"]["report"]
    assert report == {"readingSessionId": session, "summary": "本次共读阅读了第 1 页。",
                      "activities": [], "status": "FAILED"}


def test_startup_recovery_fails_leftover_pending_reports(bookshelf):
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    pending = _started_session(first, headers, path, pages=1)
    profile = first.get("/api/v1/child-profiles").json()["data"]["items"][0]["childProfileId"]
    ready = first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": 11, "mode": "PARENT_CHILD"}).json()["data"]["readingSessionId"]
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO reading_reports VALUES (?, 'PENDING', '事实摘要', '[{\"x\":1}]', 1)", (pending,))
        db.execute("INSERT INTO reading_reports VALUES (?, 'READY', '已生成', '[{\"x\":1}]', 1)", (ready,))
    with TestClient(app, base_url="https://testserver"):
        pass  # lifespan startup runs recovery before serving
    with closing(sqlite3.connect(path)) as db:
        rows = dict((r[0], r[1:]) for r in db.execute(
            "SELECT session_id, status, summary, activities_json FROM reading_reports"))
    assert rows[pending] == ("FAILED", "事实摘要", "[]")
    assert rows[ready] == ("READY", "已生成", '[{"x":1}]')


def test_concurrent_finish_and_page_change_commit_once(bookshelf):
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    session = _started_session(first, headers, path)
    base = f"/api/v1/reading-sessions/{session}"
    first.post(f"{base}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"})
    finish = {"reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "end"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        same_key = list(pool.map(lambda _: first.post(f"{base}/completions", headers=headers, json=finish), range(2)))
    assert [r.status_code for r in same_key] == [200, 200]
    assert same_key[0].json()["data"] == same_key[1].json()["data"]
    assert all(r.json()["requestId"] == r.headers["X-Request-ID"] for r in same_key)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT revision FROM reading_sessions WHERE id = ?", (session,)).fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM request_executions WHERE client_request_id = 'end'").fetchone()[0] == 1
    # Race: page change and finish both target revision 1 on a fresh session; exactly one wins.
    profile = first.get("/api/v1/child-profiles").json()["data"]["items"][0]["childProfileId"]
    race = first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": 11, "mode": "PARENT_CHILD"}).json()["data"]["readingSessionId"]
    rbase = f"/api/v1/reading-sessions/{race}"
    first.post(f"{rbase}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"})
    calls = [lambda: first.post(f"{rbase}/completions", headers=headers, json=finish),
             lambda: first.post(f"{rbase}/events", headers=headers, json={
                 "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "page"})]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda call: call(), calls))
    codes = sorted(r.status_code for r in results)
    assert codes in ([200, 409], [201, 409])
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT revision FROM reading_sessions WHERE id = ?", (race,)).fetchone()[0] == 2


def test_failed_event_and_completion_conflicts_are_recorded_and_replayed(bookshelf):
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    session = _started_session(first, headers, path)
    events = f"/api/v1/reading-sessions/{session}/events"
    completions = f"/api/v1/reading-sessions/{session}/completions"
    early_page = {"eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "early-page"}
    early_end = {"reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "early-end"}

    err1 = first.post(events, headers=headers, json=early_page)
    assert err1.status_code == 409 and err1.json()["error"]["code"] == "SESSION_CONFLICT"
    err2 = first.post(completions, headers=headers, json=early_end)
    assert err2.status_code == 409 and err2.json()["error"]["code"] == "SESSION_CONFLICT"

    # Now advance session to IN_PROGRESS / revision=1 where those payloads would otherwise match.
    assert first.post(events, headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"
    }).status_code == 201

    replay_page = first.post(events, headers=headers, json=early_page)
    assert replay_page.status_code == 409 and replay_page.json()["error"] == err1.json()["error"]
    assert replay_page.json()["requestId"] == replay_page.headers["X-Request-ID"] != err1.json()["requestId"]
    assert first.post(events, headers=headers, json={**early_page, "expectedRevision": 0}).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    replay_end = first.post(completions, headers=headers, json=early_end)
    assert replay_end.status_code == 409 and replay_end.json()["error"] == err2.json()["error"]
    assert replay_end.json()["requestId"] == replay_end.headers["X-Request-ID"] != err2.json()["requestId"]

    view = first.get(f"/api/v1/reading-sessions/{session}").json()["data"]["session"]
    assert view["status"] == "IN_PROGRESS" and view["currentPage"] == 1 and view["revision"] == 1
    with closing(sqlite3.connect(path)) as db:
        statuses = dict(db.execute(
            "SELECT client_request_id, status FROM request_executions WHERE session_id = ?", (session,)
        ).fetchall())
        assert statuses == {"early-page": "FAILED", "early-end": "FAILED", "start": "SUCCEEDED"}


def test_retired_library_version_allows_active_content_and_finished_history_replay(bookshelf):
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    session = _started_session(first, headers, path)
    events = f"/api/v1/reading-sessions/{session}/events"
    completions = f"/api/v1/reading-sessions/{session}/completions"
    assert first.post(events, headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"
    }).status_code == 201

    # Retire the public LIBRARY version while this session is IN_PROGRESS.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'RETIRED', reviewed_by_id = 1, reviewed_at = 1 WHERE id = 11")

    profile_id = first.get("/api/v1/child-profiles").json()["data"]["items"][0]["childProfileId"]
    assert first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile_id, "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).status_code == 404
    assert 11 not in [item["storyVersionId"] for item in first.get("/api/v1/storybooks").json()["data"]["items"]]
    assert 11 not in [item["storyVersionId"] for item in first.get("/api/v1/storybooks", params={"scope": "mine"}).json()["data"]["items"]]
    detail = first.get(f"/api/v1/reading-sessions/{session}").json()["data"]["session"]
    assert detail["sourceType"] == "LIBRARY" and detail["childProfileId"] == profile_id
    assert first.get("/api/v1/story-versions/11").status_code == 200  # active session can still read story pages
    assert first.post(events, headers=headers, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "page2"
    }).status_code == 201

    finish = {"reason": "USER_FINISH", "expectedRevision": 2, "clientEventId": "end"}
    done = first.post(completions, headers=headers, json=finish)
    assert done.status_code == 200 and done.json()["data"]["session"]["status"] == "FINISHED"

    # Once finished: version detail is closed, but session history, report and completion replay stay accessible.
    assert first.get("/api/v1/story-versions/11").status_code == 404
    assert first.get(f"/api/v1/reading-sessions/{session}").json()["data"]["report"]["readingSessionId"] == session
    assert [s["readingSessionId"] for s in first.get("/api/v1/reading-sessions").json()["data"]["items"]] == [session]
    assert first.post(completions, headers=headers, json=finish).status_code == 200
    assert first.post(completions, headers=headers, json={**finish, "clientEventId": "end-2"}).status_code == 200

    # Withdrawing immediately blocks content, history, and replay.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'WITHDRAWN' WHERE id = 11")
    assert first.get("/api/v1/story-versions/11").status_code == 404
    assert first.get(f"/api/v1/reading-sessions/{session}").status_code == 404
    assert first.post(completions, headers=headers, json=finish).status_code == 404


def test_startup_recovery_stays_unready_while_locked_and_recovers_after_release(bookshelf):
    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    pending = _started_session(first, headers, path, pages=1)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO reading_reports VALUES (?, 'PENDING', '事实摘要', '[]', 1)", (pending,))

    # Hold a write lock during app startup so lifespan recovery cannot commit.
    locker = sqlite3.connect(path)
    try:
        locker.execute("BEGIN IMMEDIATE")
        with TestClient(app, base_url="https://testserver") as locked_client:
            unready = locked_client.get("/api/v1/health")
            assert unready.status_code == 503 and unready.json()["error"]["retryable"] is True
            with closing(sqlite3.connect(path)) as reader:
                assert reader.execute(
                    "SELECT status FROM reading_reports WHERE session_id = ?", (pending,)
                ).fetchone()[0] == "PENDING"
            locker.rollback()
            ready = locked_client.get("/api/v1/health")
            assert ready.status_code == 200 and ready.json()["data"]["status"] == "ok"
    finally:
        locker.close()

    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "SELECT status, summary, activities_json FROM reading_reports WHERE session_id = ?", (pending,)
        ).fetchone() == ("FAILED", "事实摘要", "[]")


def test_delayed_recovery_preserves_a_new_report_during_generation(bookshelf, monkeypatch):
    from app.schemas.agent import ModelGenerateResult

    first, _, path = bookshelf
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    leftover = _started_session(first, headers, path, pages=1)
    active = _started_session(first, headers, path, pages=1)
    url = f"/api/v1/reading-sessions/{active}"
    assert first.post(f"{url}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start",
    }).status_code == 201
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO reading_reports VALUES (?, 'PENDING', '遗留事实摘要', '[]', 1)", (leftover,))

    model_started, model_release = Event(), Event()

    class PausedAdapter:
        async def generate(self, request):
            model_started.set()
            assert await asyncio.to_thread(model_release.wait, 10)
            return ModelGenerateResult(success=True, text='{"suggestionId":"NONE"}')

    finish = {"reason": "USER_FINISH", "expectedRevision": 1, "clientEventId": "finish"}
    with closing(sqlite3.connect(path)) as locker:
        locker.execute("BEGIN IMMEDIATE")
        with TestClient(app, base_url="https://testserver") as client:
            client.cookies.update(first.cookies)
            monkeypatch.setattr(app.state, "model_adapter", PausedAdapter())
            assert client.get("/api/v1/health").status_code == 503
            unavailable = client.post(f"{url}/completions", headers=headers, json=finish)
            assert unavailable.status_code == 503 and unavailable.json()["error"]["retryable"] is False
            assert client.get(url).json()["data"]["session"]["status"] == "IN_PROGRESS"
            assert not model_started.is_set()
            locker.rollback()

            # Complete the first business request before probing readiness again.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending_response = pool.submit(client.post, f"{url}/completions", headers=headers, json=finish)
                try:
                    assert model_started.wait(5)
                    assert client.get("/api/v1/health").status_code == 200
                    report = client.get(url).json()["data"]["report"]
                    assert report["status"] == "PENDING"
                finally:
                    model_release.set()
                assert pending_response.result(timeout=10).status_code == 200
            report = client.get(url).json()["data"]["report"]
            assert (report["status"], report["summary"]) == ("READY", "本次共读阅读了第 1 页。")

    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "SELECT status, summary FROM reading_reports WHERE session_id = ?", (leftover,)
        ).fetchone() == ("FAILED", "遗留事实摘要")
