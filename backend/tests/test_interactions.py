"""Tests for API-010 recommendations, API-015 ANSWER/FEEDBACK events, API-017 guidance, and API-018 branch selection."""

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import guidance as guidance_api
from app.api.v1.auth import _login_attempts
from app.config import Settings
from app.main import app, create_app
from app.model_gateway import ModelGateway
from app.schemas.agent import ModelGenerateResult
from app.services import reading_agent
from app.speech_config import SpeechSettings

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema.sql"


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "interactions.sqlite3"
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
            for bid, owner, src in ((1, None, "LIBRARY"), (2, 1, "ORIGINAL"), (3, 2, "ORIGINAL"), (4, None, "LIBRARY")):
                db.execute("INSERT INTO storybooks VALUES (?, ?, ?, 1)", (bid, owner, src))
            for vid, bid, vno, topics, age_min, age_max, status in (
                (11, 1, 1, '["互助"]', 3, 5, "PUBLISHED"),
                (21, 2, 1, '["动物"]', 3, 4, "APPROVED_PRIVATE"),
                (31, 3, 1, '["动物"]', 3, 4, "APPROVED_PRIVATE"),
                (41, 4, 1, '["动物"]', 3, 4, "RETIRED"),
                (42, 4, 2, '["动物"]', 3, 4, "WITHDRAWN"),
            ):
                db.execute("""INSERT INTO story_versions
                    (id, storybook_id, version_no, title, topics_json, age_min, age_max,
                     license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'ok', ?, 1, 1, 1, 1)""",
                    (vid, bid, vno, f"story-{vid}", topics, age_min, age_max, status))
                db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (?, 4, '分支第四页', 'none')", (vid,))
                db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (?, 3, '常规第三页', 'none', 4)", (vid,))
                db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (?, 2, '第二页', 'none', 3)", (vid,))
                db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (?, 1, '第一页', 'none', 2)", (vid,))
            db.execute("INSERT INTO story_branches VALUES (501, 11, 2, 4, '走小桥', 'APPROVED')")
            db.execute("INSERT INTO story_branches VALUES (502, 11, 2, 3, '待审分支', 'PENDING')")
            db.execute("INSERT INTO story_branches VALUES (503, 11, 1, 3, '第一页分支', 'APPROVED')")
        yield first, second, path
    _login_attempts.clear()


def test_recommendations_filter_age_interests_and_access(env):
    first, second, _ = env
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    other_headers = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers, json={
        "ageBand": "AGE_3_4", "preferredMinutes": 10, "interests": ["动物"]
    }).json()["data"]
    p_id = profile["childProfileId"]

    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.post("/api/v1/recommendations", json={"childProfileId": p_id}).status_code == 401
    assert first.post("/api/v1/recommendations", json={"childProfileId": p_id}).status_code == 403
    assert second.post("/api/v1/recommendations", headers=other_headers, json={"childProfileId": p_id}).status_code == 404
    assert first.post("/api/v1/recommendations", headers=headers, json={"childProfileId": p_id, "minutes": 31}).status_code == 422

    # Prioritizes matching interest (21) before other accessible age-matched books (11); excludes 31, 41, 42.
    rec = first.post("/api/v1/recommendations", headers=headers, json={"childProfileId": p_id, "minutes": 10})
    assert rec.status_code == 200 and rec.json()["requestId"] == rec.headers["X-Request-ID"]
    items = rec.json()["data"]["items"]
    assert [item["storyVersionId"] for item in items] == [21, 11]
    assert all(item["sources"] == [] and item["reason"] for item in items)

    by_topic = first.post("/api/v1/recommendations", headers=headers, json={"childProfileId": p_id, "topic": " 互助 "})
    assert [item["storyVersionId"] for item in by_topic.json()["data"]["items"]] == [11]
    empty = first.post("/api/v1/recommendations", headers=headers, json={"childProfileId": p_id, "topic": "太空"})
    assert empty.status_code == 200 and empty.json()["data"]["items"] == []


def test_recommendations_rank_all_eligible_versions_before_limiting(env):
    first, _, path = env
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers, json={
        "ageBand": "AGE_3_4", "preferredMinutes": 10, "interests": ["星星", "太空"]
    }).json()["data"]
    # The fixture already has two eligible books. The 51st is the only interest match.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        for book_id in range(100, 149):
            topics = '["太空", "星星"]' if book_id == 148 else '[]'
            db.execute("INSERT INTO storybooks (id, source_type, created_at) VALUES (?, 'LIBRARY', 1)", (book_id,))
            db.execute("""INSERT INTO story_versions
                (id, storybook_id, version_no, title, topics_json, age_min, age_max, license_note,
                 status, reviewed_by_id, reviewed_at, created_at, branching)
                VALUES (?, ?, 1, '绘本', ?, 3, 4, 'ok', 'PUBLISHED', 1, 1, 1, 0)""", (book_id, book_id, topics))
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (?, 1, '第一页', 'none')",
                       (book_id,))

    response = first.post("/api/v1/recommendations", headers=headers, json={"childProfileId": profile["childProfileId"]})
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["storyVersionId"] for item in items] == [148, 11, 21, *range(100, 107)]
    assert items[0]["reason"] == "符合 3～4 岁 与兴趣「太空」"


def test_recommendation_database_failure_is_retryable(env, monkeypatch):
    first, _, path = env
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path.with_name("missing.sqlite3")))
    response = first.post("/api/v1/recommendations", headers=headers, json={"childProfileId": 1})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is True


@pytest.fixture
def started_session(env):
    first, _, path = env
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers, json={
        "ageBand": "AGE_3_4", "preferredMinutes": 10
    }).json()["data"]
    sid = first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile["childProfileId"], "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"
    }).status_code == 201
    return first, headers, path, sid


@pytest.mark.parametrize(("status", "text", "accepted"), [
    ("FAILED", None, False), ("FAILED", "问题生成受阻", False), ("SUCCESS", None, False),
    ("SUCCESS", "", False), ("FALLBACK", " \t\n　", False), ("FALLBACK", "你看到了什么？", True),
])
def test_feedback_requires_a_displayable_question(started_session, status, text, accepted):
    first, headers, path, sid = started_session
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""INSERT INTO interaction_events
            (session_id, version_id, request_key, request_digest, response_json, page_no,
             event_type, output_text, result_status, revision_after, created_at)
            VALUES (?, 11, 'question', 'd', '{}', 1, 'QUESTION', ?, ?, 1, 1)""", (sid, text, status))
    response = first.post(f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
        "eventType": "FEEDBACK", "pageNumber": 1, "expectedRevision": 1, "clientEventId": "feedback", "feedback": "UNDERSTOOD"
    })
    if accepted:
        assert response.status_code == 201 and response.json()["data"]["revision"] == 2
    else:
        assert response.status_code == 409 and response.json()["error"]["code"] == "PROMPT_REQUIRED"
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT revision FROM reading_sessions WHERE id = ?", (sid,)).fetchone()[0] == 1


def test_branch_is_scoped_to_session_version(started_session):
    first, headers, path, sid = started_session
    url = f"/api/v1/reading-sessions/{sid}/branches"
    # Version 21 also belongs to this guardian, but the session is reading version 11.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO story_branches VALUES (504, 21, 1, 3, '另一版本分支', 'APPROVED')")
    outside = first.post(url, headers=headers, json={"branchId": 504, "expectedRevision": 1, "clientEventId": "other-version"})
    missing = first.post(url, headers=headers, json={"branchId": 999, "expectedRevision": 1, "clientEventId": "missing"})
    assert outside.status_code == missing.status_code == 404
    assert outside.json()["error"] == missing.json()["error"]
    assert outside.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_missing_branch_replays_failure(started_session):
    first, headers, path, sid = started_session
    url = f"/api/v1/reading-sessions/{sid}/branches"
    payload = {"branchId": 700, "expectedRevision": 1, "clientEventId": "choice"}
    original = first.post(url, headers=headers, json=payload)
    assert original.status_code == 404
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO story_branches VALUES (700, 11, 1, 4, '后来添加的分支', 'APPROVED')")
    replay = first.post(url, headers=headers, json=payload)
    assert replay.status_code == 404 and replay.json()["error"] == original.json()["error"]
    assert replay.json()["requestId"] == replay.headers["X-Request-ID"] != original.json()["requestId"]
    conflict = first.post(url, headers=headers, json={**payload, "branchId": 503})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    chosen = first.post(url, headers=headers, json={**payload, "clientEventId": "new-choice"})
    assert chosen.status_code == 201 and chosen.json()["data"]["visitedPageNumbers"] == [1, 4]
    assert chosen.json()["data"]["revision"] == 2


def test_branch_replay_respects_retired_library_content_access(started_session):
    first, headers, path, sid = started_session
    session_url = f"/api/v1/reading-sessions/{sid}"
    branch = {"branchId": 503, "expectedRevision": 1, "clientEventId": "branch"}
    chosen = first.post(f"{session_url}/branches", headers=headers, json=branch)
    assert chosen.status_code == 201
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'RETIRED' WHERE id = 11")
    assert first.get("/api/v1/story-versions/11").status_code == 200
    replay = first.post(f"{session_url}/branches", headers=headers, json=branch)
    assert replay.status_code == 200 and replay.json()["data"] == chosen.json()["data"]

    finish = {"reason": "USER_FINISH", "expectedRevision": 2, "clientEventId": "end"}
    assert first.post(f"{session_url}/completions", headers=headers, json=finish).status_code == 200
    assert first.get("/api/v1/story-versions/11").status_code == 404
    replay = first.post(f"{session_url}/branches", headers=headers, json=branch)
    assert replay.status_code == 404 and replay.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    history = first.get(session_url).json()["data"]
    assert history["session"]["status"] == "FINISHED" and history["report"]["readingSessionId"] == sid
    assert first.post(f"{session_url}/completions", headers=headers, json=finish).status_code == 200


def test_answer_feedback_and_branch_choice_lifecycle(env):
    first, second, path = env
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    other = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers,
                         json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]
    sid = first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile["childProfileId"], "storyVersionId": 11, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    events_url = f"/api/v1/reading-sessions/{sid}/events"
    branches_url = f"/api/v1/reading-sessions/{sid}/branches"

    # Branch selection is rejected while CREATED.
    assert first.post(branches_url, headers=headers,
                      json={"branchId": 501, "expectedRevision": 0, "clientEventId": "early-b"}).status_code == 409

    assert first.post(events_url, headers=headers,
                      json={"eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "e1"}).status_code == 201
    assert first.post(events_url, headers=headers,
                      json={"eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "e2"}).status_code == 201

    # Validation for ANSWER and FEEDBACK payloads.
    assert first.post(events_url, headers=headers,
                      json={"eventType": "ANSWER", "pageNumber": 2, "expectedRevision": 2, "clientEventId": "bad-a", "inputText": "   "}).status_code == 422
    assert first.post(events_url, headers=headers,
                      json={"eventType": "FEEDBACK", "pageNumber": 2, "expectedRevision": 2, "clientEventId": "bad-f"}).status_code == 422

    # FEEDBACK requires a prior QUESTION event on the current page.
    no_q = first.post(events_url, headers=headers,
                      json={"eventType": "FEEDBACK", "pageNumber": 2, "expectedRevision": 2, "clientEventId": "f1", "feedback": "UNDERSTOOD"})
    assert no_q.status_code == 409 and no_q.json()["error"]["code"] == "PROMPT_REQUIRED"

    # ANSWER succeeds on current page without changing currentPage.
    ans = first.post(events_url, headers=headers,
                     json={"eventType": "ANSWER", "pageNumber": 2, "expectedRevision": 2, "clientEventId": "a1", "inputText": " 小兔子帮了朋友 "})
    assert ans.status_code == 201 and ans.json()["data"]["revision"] == 3
    ans_replay = first.post(events_url, headers=headers,
                            json={"eventType": "ANSWER", "pageNumber": 2, "expectedRevision": 2, "clientEventId": "a1", "inputText": "小兔子帮了朋友"})
    assert ans_replay.status_code == 200 and ans_replay.json()["data"] == ans.json()["data"]

    # Seed a QUESTION on page 2, then FEEDBACK succeeds.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""INSERT INTO interaction_events
            (session_id, version_id, request_key, request_digest, response_json, page_no,
             event_type, output_text, result_status, revision_after, created_at)
            VALUES (?, 11, 'q1', 'd', '{}', 2, 'QUESTION', '小兔子在做什么？', 'SUCCESS', 3, 1)""", (sid,))
    fb = first.post(events_url, headers=headers,
                    json={"eventType": "FEEDBACK", "pageNumber": 2, "expectedRevision": 3, "clientEventId": "f2", "feedback": "SKIPPED"})
    assert fb.status_code == 201 and fb.json()["data"]["revision"] == 4

    # Branch validation: auth/ownership/missing/unapproved/wrong-page/shared-idempotency.
    assert first.post(branches_url, json={"branchId": 501, "expectedRevision": 4, "clientEventId": "b1"}).status_code == 403
    assert second.post(branches_url, headers=other, json={"branchId": 501, "expectedRevision": 4, "clientEventId": "b1"}).status_code == 404
    assert first.post(branches_url, headers=headers, json={"branchId": 9999, "expectedRevision": 4, "clientEventId": "b-miss"}).status_code == 404
    assert first.post(branches_url, headers=headers, json={"branchId": 502, "expectedRevision": 4, "clientEventId": "b-pend"}).status_code == 409
    assert first.post(branches_url, headers=headers, json={"branchId": 503, "expectedRevision": 4, "clientEventId": "b-page"}).status_code == 409
    assert first.post(branches_url, headers=headers, json={"branchId": 501, "expectedRevision": 4, "clientEventId": "a1"}).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    chosen = first.post(branches_url, headers=headers, json={"branchId": 501, "expectedRevision": 4, "clientEventId": "b1"})
    assert chosen.status_code == 201 and chosen.json()["requestId"] == chosen.headers["X-Request-ID"]
    cdata = chosen.json()["data"]
    assert cdata["currentPage"] == 4 and cdata["revision"] == 5 and cdata["visitedPageNumbers"] == [1, 2, 4]
    assert cdata["nextPage"] == {"pageNumber": 4, "text": "分支第四页", "imageRef": None, "defaultNextPageNumber": None}

    b_replay = first.post(branches_url, headers=headers, json={"branchId": 501, "expectedRevision": 4, "clientEventId": "b1"})
    assert b_replay.status_code == 200 and b_replay.json()["data"] == cdata
    assert b_replay.json()["requestId"] == b_replay.headers["X-Request-ID"] != chosen.json()["requestId"]
    assert first.post(branches_url, headers=headers, json={"branchId": 501, "expectedRevision": 5, "clientEventId": "b1"}).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    with closing(sqlite3.connect(path)) as db:
        row = db.execute("SELECT page_no, branch_id, event_type FROM interaction_events WHERE id = ?",
                         (cdata["interactionEventId"],)).fetchone()
        assert tuple(row) == (2, 501, "BRANCH_CHOSEN")
        ans_row = db.execute("SELECT input_text FROM interaction_events WHERE id = ?",
                             (ans.json()["data"]["interactionEventId"],)).fetchone()
        assert ans_row[0] == "小兔子帮了朋友"


def enter_child_mode(first, version_id):
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers,
                         json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]
    sid = first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile["childProfileId"], "storyVersionId": version_id, "mode": "PARENT_CHILD"
    }).json()["data"]["readingSessionId"]
    assert first.put(f"/api/v1/reading-sessions/{sid}/mode-settings", headers=headers, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": [version_id], "timeLimitSeconds": 600
    }).status_code == 200
    return sid, profile["childProfileId"], {"X-CSRF-Token": first.cookies["zhiya_csrf"]}


def test_child_mode_can_choose_branch_and_cannot_call_recommendations(env):
    first, _, _ = env
    sid, profile_id, child_headers = enter_child_mode(first, 11)
    assert first.post("/api/v1/recommendations", headers=child_headers,
                      json={"childProfileId": profile_id}).status_code == 403
    assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=child_headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "cs"
    }).status_code == 201
    assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=child_headers, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "cp"
    }).status_code == 201
    assert first.get(f"/api/v1/reading-sessions/{sid}").json()["data"]["answerOptions"] == ["走小桥"]
    events_url = f"/api/v1/reading-sessions/{sid}/events"
    answer = {"eventType": "ANSWER", "pageNumber": 2, "expectedRevision": 2}
    for index, label in enumerate(("未经审核的文本", "待审分支", "第一页分支")):
        payload = {**answer, "clientEventId": f"answer-invalid-{index}", "inputText": label}
        refused = first.post(events_url, headers=child_headers, json=payload)
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "VALIDATION_ERROR"
        repeated = first.post(events_url, headers=child_headers, json=payload)
        assert repeated.status_code == 422 and repeated.json()["error"] == refused.json()["error"]
    assert first.get(f"/api/v1/reading-sessions/{sid}").json()["data"]["session"]["revision"] == 2
    accepted = first.post(events_url, headers=child_headers, json={
        **answer, "clientEventId": "answer-valid", "inputText": " 走小桥 "
    })
    assert accepted.status_code == 201 and accepted.json()["data"]["revision"] == 3
    branch = first.post(f"/api/v1/reading-sessions/{sid}/branches", headers=child_headers, json={
        "branchId": 501, "expectedRevision": 3, "clientEventId": "cb"
    })
    assert branch.status_code == 201 and branch.json()["data"]["currentPage"] == 4


def save_question(path, sid, data, *, status="SUCCESS", text="你听到了什么？"):
    # API-017 commits the displayed question and its response together with the new revision.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        version, page, revision = db.execute(
            """UPDATE reading_sessions SET revision = revision + 1 WHERE id = ?
               RETURNING version_id, current_page_no, revision""", (sid,)
        ).fetchone()
        db.execute("""INSERT INTO interaction_events
            (session_id, version_id, request_key, request_digest, response_json, page_no, event_type,
             output_text, result_status, revision_after, created_at)
            VALUES (?, ?, ?, 'd', ?, ?, 'QUESTION', ?, ?, ?, 1)""",
            (sid, version, f"question-{revision}", json.dumps({"data": data}), page, text, status, revision))
    return revision


def test_child_can_answer_a_question_on_a_linear_page(env):
    first, _, path = env
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_versions SET branching = 0 WHERE id = 21")
    sid, _, headers = enter_child_mode(first, 21)
    url = f"/api/v1/reading-sessions/{sid}"
    assert first.post(url + "/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start"
    }).status_code == 201
    save_question(path, sid, {"answerOptions": ["风声"]})
    save_question(path, sid, {"answerOptions": ["滴答滴答", "轰隆轰隆"]}, status="FALLBACK")
    revision = save_question(path, sid, {"answerOptions": ["失效选项"]}, status="FAILED", text=None)
    answer = {"eventType": "ANSWER", "pageNumber": 1, "expectedRevision": revision,
              "clientEventId": "answer", "inputText": "滴答滴答"}
    accepted = first.post(url + "/events", headers=headers, json=answer)
    assert accepted.status_code == 201
    assert accepted.json()["data"]["revision"] == revision + 1
    assert first.get(url).json()["data"]["answerOptions"] == ["滴答滴答", "轰隆轰隆"]

    for index, label in enumerate(("风声", "失效选项", "不是", "别的答案")):
        refused = first.post(url + "/events", headers=headers, json={
            **answer, "expectedRevision": revision + 1, "clientEventId": f"refused-{index}", "inputText": label
        })
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "VALIDATION_ERROR"
    progress = first.get(url).json()["data"]["session"]
    assert (progress["currentPage"], progress["revision"]) == (1, revision + 1)
    replay = first.post(url + "/events", headers=headers, json=answer)
    assert replay.status_code == 200 and replay.json()["data"] == accepted.json()["data"]

    assert first.post(url + "/events", headers=headers, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": revision + 1, "clientEventId": "page"
    }).status_code == 201
    assert first.get(url).json()["data"]["answerOptions"] == []
    revision = save_question(path, sid, {})  # An older question with only a text prompt.
    assert first.get(url).json()["data"]["answerOptions"] == ["是", "不是", "不知道"]
    current = {**answer, "pageNumber": 2, "expectedRevision": revision}
    assert first.post(url + "/events", headers=headers, json={**current, "clientEventId": "old-page"}).status_code == 422
    short_answer = first.post(url + "/events", headers=headers, json={
        **current, "clientEventId": "short", "inputText": " 不知道 "
    })
    assert short_answer.status_code == 201 and short_answer.json()["data"]["revision"] == revision + 1


def guidance(client, headers, session_id, key, *, feedback="INITIAL", page=1, revision=1):
    return client.post(f"/api/v1/reading-sessions/{session_id}/guidance", headers=headers, json={
        "pageNumber": page, "feedback": feedback, "expectedRevision": revision, "clientEventId": key,
    })


def test_story_question_replay_readback_and_answer_audio(started_session, guidance_model, env, monkeypatch):
    client, headers, path, sid = started_session
    _, other, _ = env
    page_text = "小兔打开雨伞，走到小熊身边。"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""UPDATE knowledge_items SET task_types_json='["RETRIEVAL"]' WHERE id=81""")
    url = f"/api/v1/reading-sessions/{sid}"
    payload = {"pageNumber": 1, "expectedRevision": 1, "clientEventId": "ask", "inputText": "谁打开雨伞？"}
    assert client.post(url + "/questions", json=payload).status_code == 403
    assert other.post(url + "/questions", headers={"X-CSRF-Token": other.cookies["zhiya_csrf"]}, json=payload).status_code == 404

    async def model(request):
        assert request.user_input == payload["inputText"] and page_text in request.context_text
        ongoing = await asyncio.to_thread(client.post, url + "/questions", headers=headers, json=payload)
        assert ongoing.status_code == 409 and ongoing.json()["error"]["code"] == "REQUEST_IN_PROGRESS"
        assert "1. " + page_text in request.context_text
        return ModelGenerateResult(success=True, text="1")

    guidance_model.side_effect = model
    response = client.post(url + "/questions", headers=headers, json=payload)
    assert response.status_code == 201
    answer = response.json()["data"]
    assert answer["status"] == "GENERATED" and answer["outputText"] == page_text
    assert answer["sources"] == [{"knowledgeItemId": 81, "sourceTitle": "共读手册"}]
    replay = client.post(url + "/questions", headers=headers, json=payload)
    assert replay.status_code == 200 and replay.json()["data"] == answer
    assert guidance(client, headers, sid, "ask").json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    guidance_model.assert_awaited_once()
    detail = client.get(url).json()["data"]
    assert detail["session"]["revision"] == 2 and len(detail["events"]) == 2
    assert detail["events"][1]["eventType"] == "ASK" and detail["events"][1]["outputText"] == page_text
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT input_text FROM interaction_events WHERE id=?", (answer["interactionEventId"],)).fetchone() == (payload["inputText"],)
        assert db.execute("SELECT knowledge_item_id FROM event_evidence WHERE event_id=?", (answer["interactionEventId"],)).fetchone() == (81,)
    voice = AsyncMock(return_value=b"test-audio-bytes")
    monkeypatch.setattr(app.state, "speech_settings", SpeechSettings(api_key="fixture", tts_api_url="https://example.test/voice"))
    monkeypatch.setattr(app.state.voice_gateway, "synthesize", voice)
    tts = client.post("/api/v1/voice/syntheses", headers=headers, json={"interactionEventId": answer["interactionEventId"]})
    assert tts.status_code == 201 and voice.await_args.kwargs["text"] == page_text
    media = tts.json()["data"]["audioUrl"]
    assert client.get(media).content == b"test-audio-bytes" and other.get(media).status_code == 404


def test_child_questions_only_accept_short_choices_and_never_open_free_text(child_guidance_session, guidance_model):
    client, _, _, sid, headers = child_guidance_session
    url = f"/api/v1/reading-sessions/{sid}/questions"
    payload = {"inputText": "任意自由文本", "pageNumber": 1, "clientEventId": "child", "expectedRevision": 1}
    assert client.post(url, headers=headers, json=payload).status_code == 422
    res = client.post(url, headers=headers, json={**payload, "inputText": "故事里有谁？"})
    assert res.status_code == 201 and res.json()["data"]["strategyType"] == "NO_KNOWLEDGE_FALLBACK"
    guidance_model.assert_not_awaited()


def test_story_question_safety_and_model_failure(started_session, guidance_model):
    client, headers, path, sid = started_session
    url = f"/api/v1/reading-sessions/{sid}/questions"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""UPDATE knowledge_items SET task_types_json='["RETRIEVAL"]' WHERE id=81""")
    def post(key, revision, text):
        return client.post(url, headers=headers, json={"pageNumber": 1, "expectedRevision": revision,
                           "clientEventId": key, "inputText": text})

    blocked = post("private", 1, "我家的电话号码是什么？").json()["data"]
    assert blocked["status"] == "BLOCKED"
    guidance_model.assert_not_awaited()
    assert client.post("/api/v1/voice/syntheses", headers=headers, json={"interactionEventId": blocked["interactionEventId"]}).status_code == 404
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT input_text FROM interaction_events WHERE request_key='private'").fetchone() == (None,)
    guidance_model.return_value = ModelGenerateResult(success=False, error_code="API_ERROR")
    for _ in range(2):
        assert post("unavailable", 2, "雨伞是什么？").status_code == 503
    guidance_model.assert_awaited_once()
    guidance_model.return_value = ModelGenerateResult(success=False, error_code="TIMEOUT")
    assert post("timeout", 2, "雨伞是什么？").json()["data"]["strategyType"] == "TIMEOUT_FALLBACK"
    guidance_model.return_value = ModelGenerateResult(success=True, text="泄露未审核的内容")
    assert post("unsafe-output", 3, "雨伞是什么？").json()["data"]["status"] == "BLOCKED"


@pytest.fixture
def guidance_knowledge(env):
    _, _, path = env
    page = "小兔打开雨伞，走到小熊身边。"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_pages SET body_text=? WHERE version_id=11 AND page_no=1", (page,))
        for item_id, topic, age, content in (
            (81, "互助", "AGE_3_4", "观察雨伞，聊聊怎样关心朋友。"),
            (82, "节奏", "AGE_3_4", page),
            (83, "互助", "AGE_5_6", page),
        ):
            db.execute("""INSERT INTO knowledge_items
                (id, version_no, item_key, title, source_title, source_ref, locator, age_band,
                 topics_json, task_types_json, content, status, reviewed_at, created_at)
                VALUES (?, 1, ?, '观察', '共读手册', 'fixture', 'p1', ?, ?, '["GUIDANCE"]', ?, 'APPROVED', 1, 1)""",
                       (item_id, f"fixture-{item_id}", age, json.dumps([topic], ensure_ascii=False), content))


@pytest.fixture
def guidance_model(guidance_knowledge, monkeypatch):
    model = AsyncMock(return_value=ModelGenerateResult(success=True, text="小兔打开雨伞了吗？"))
    monkeypatch.setattr(app.state.model_adapter, "generate", model)
    return model


def test_guidance_gateway_request_and_replay(started_session, guidance_knowledge, monkeypatch):
    existing, headers, _, sid = started_session
    requests = []

    def provider(request):
        payload = json.loads(request.content)
        requests.append(payload)
        context = payload["messages"][1]["content"]
        first_line = context.split("候选问句：\n", 1)[1].splitlines()[0]
        assert first_line.startswith("1. ")
        return httpx.Response(200, json={"choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": first_line}},
        ]})

    def gateway(settings):
        return ModelGateway(settings, transport=httpx.MockTransport(provider))

    monkeypatch.setattr("app.main.ModelGateway", gateway)
    with TestClient(create_app(Settings(model_api_key="test-only")), base_url="https://testserver") as client:
        client.cookies.update(existing.cookies)
        first = guidance(client, headers, sid, "wire")
        replay = guidance(client, headers, sid, "wire")
        assert first.status_code == 201 and replay.status_code == 200
        data = first.json()["data"]
        assert replay.json()["data"] == data
        assert data["status"] == "GENERATED" and "小兔打开雨伞" in data["outputText"]
        assert data["sources"] == [{"knowledgeItemId": 81, "sourceTitle": "共读手册"}]
        assert len(requests) == 1
        assert requests[0]["thinking"] == {"type": "disabled"}
        prompt = requests[0]["messages"][1]["content"]
        assert "3～4岁" in prompt and "怎样关心朋友" in prompt

        follow = guidance(client, headers, sid, "wire-feedback", feedback="NOT_UNDERSTOOD", revision=2)
        assert follow.status_code == 201 and follow.json()["data"]["strategyType"] == "CONCRETE_RECALL"
        assert len(requests) == 2
        assert "当前页已保存的问题（仅作参考）：" + data["outputText"] in requests[1]["messages"][1]["content"]


def test_guidance_fox_page_feedback_and_refresh(started_session, guidance_model):
    client, headers, path, sid = started_session
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_pages SET body_text=? WHERE version_id=11 AND page_no=1",
                   ("下雨了。小狐狸站在门口，看着外面的雨。",))
    guidance_model.side_effect = [
        ModelGenerateResult(success=True, text="2"),
        ModelGenerateResult(success=True, text="读到“小狐狸站在门口”时，你能指一指画面里的小狐狸吗？"),
    ]
    first = guidance(client, headers, sid, "fox-initial").json()["data"]
    assert first["outputText"] == "读到“下雨了”时，角色在做什么？"
    feedback = guidance(client, headers, sid, "fox-feedback", feedback="NOT_UNDERSTOOD", revision=2).json()["data"]
    assert (feedback["status"], feedback["strategyType"], feedback["outputText"]) == (
        "GENERATED", "CONCRETE_RECALL", "读到“小狐狸站在门口”时，你能指一指画面里的角色吗？")
    for _ in range(2):
        data = client.get(f"/api/v1/reading-sessions/{sid}").json()["data"]
        assert data["session"]["currentPage"] == 1 and data["session"]["revision"] == 3
        events = [e for e in data["events"] if e.get("feedback") == "NOT_UNDERSTOOD"]
        assert len(events) == 1 and events[0]["outputText"] == feedback["outputText"]


def test_guidance_parent_paraphrase_uses_page_wide_question_without_excerpt(started_session, guidance_model):
    client, headers, path, sid = started_session
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_pages SET body_text=? WHERE version_id=11 AND page_no=1", ("嘘！啊！",))
    guidance_model.return_value = ModelGenerateResult(success=True, text="看看这一页，图里有什么？")

    response = guidance(client, headers, sid, "page-wide-paraphrase")
    assert response.status_code == 201
    data = response.json()["data"]
    assert (data["status"], data["strategyType"], data["outputText"]) == (
        "GENERATED", "CONTEXT_QUESTION", "看看这一页，角色在做什么？"
    )


def test_guidance_persistence_feedback_skip_and_replay(started_session, guidance_model, monkeypatch):
    client, headers, path, sid = started_session
    first = guidance(client, headers, sid, "initial")
    assert first.status_code == 201
    question = first.json()["data"]
    assert question["status"] == "GENERATED" and "小兔" in question["outputText"]
    assert question["sources"] == [{"knowledgeItemId": 81, "sourceTitle": "共读手册"}]
    assert "3～4岁" in guidance_model.await_args.args[0].context_text

    other_replies = []
    previous_text = question["outputText"]
    for rev, feedback, strategy in [(2, "UNDERSTOOD", "CONTEXT_QUESTION"), (3, "NOT_UNDERSTOOD", "CONCRETE_RECALL")]:
        reply = guidance(client, headers, sid, feedback, feedback=feedback, revision=rev)
        assert reply.status_code == 201 and reply.json()["data"]["strategyType"] == strategy
        context = guidance_model.await_args.args[0].context_text
        assert "当前页已保存的问题（仅作参考）：" + previous_text in context.splitlines()
        previous_text = reply.json()["data"]["outputText"]
        other_replies.append(reply)
    assert other_replies[0].json()["data"]["outputText"] != other_replies[1].json()["data"]["outputText"]

    def unexpected_lookup(*args, **kwargs):
        pytest.fail("SKIPPED or a replay tried to retrieve knowledge")

    monkeypatch.setattr(reading_agent, "retrieve_knowledge", unexpected_lookup)
    skipped = guidance(client, headers, sid, "skip", feedback="SKIPPED", revision=4)
    assert skipped.status_code == 201
    assert skipped.json()["data"]["outputText"] == "" and skipped.json()["data"]["sources"] == []
    refreshed = client.get(f"/api/v1/reading-sessions/{sid}").json()["data"]
    by_id = {event["interactionEventId"]: event for event in refreshed["events"]}
    assert by_id[question["interactionEventId"]]["outputText"] == question["outputText"]
    saved_skip = by_id[skipped.json()["data"]["interactionEventId"]]
    assert (saved_skip["strategyType"], saved_skip["feedback"], saved_skip["outputText"]) == ("SKIP", "SKIPPED", "")
    assert client.post(f"/api/v1/reading-sessions/{sid}/completions", headers=headers, json={
        "reason": "USER_FINISH", "expectedRevision": 5, "clientEventId": "finish",
    }).status_code == 200
    # Finishing can generate a separate report. Replaying guidance adds no model calls.
    calls = guidance_model.await_count
    replay = guidance(client, headers, sid, "initial")
    assert replay.status_code == 200 and replay.json()["data"] == question
    changed = guidance(client, headers, sid, "initial", revision=6)
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    finished = guidance(client, headers, sid, "after-finish", revision=6)
    assert finished.status_code == 409 and finished.json()["error"]["code"] == "SESSION_CONFLICT"
    assert guidance_model.await_count == calls

    with closing(sqlite3.connect(path)) as db:
        events = db.execute("""SELECT id, feedback, output_text, strategy_type, result_status, response_json
                               FROM interaction_events WHERE event_type IN ('QUESTION', 'FEEDBACK') ORDER BY id""").fetchall()
        assert len(events) == 4
        for row, reply, feedback in zip(events, [first, *other_replies, skipped], [None, "UNDERSTOOD", "NOT_UNDERSTOOD", "SKIPPED"]):
            data = reply.json()["data"]
            assert row[:4] == (data["interactionEventId"], feedback, data["outputText"], data["strategyType"])
            assert row[4] == ("FALLBACK" if feedback == "SKIPPED" else "SUCCESS")
            assert json.loads(row[5]) == reply.json()
            evidence = list(db.execute("SELECT knowledge_item_id, rank_no FROM event_evidence WHERE event_id=?", (row[0],)))
            assert evidence == ([] if feedback == "SKIPPED" else [(81, 1)])


@pytest.fixture
def child_guidance_session(env):
    client, other_guardian, path = env
    sid, _, headers = enter_child_mode(client, 11)
    url = f"/api/v1/reading-sessions/{sid}"
    assert client.post(url + "/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start",
    }).status_code == 201
    return client, other_guardian, path, sid, headers


@pytest.mark.parametrize("bodies", [
    ("图里有你认识的东西吗？", "你觉得下一页还会看到刚才的东西吗？", "你在画里找到了刚才读到的东西吗？"),
    ("图里也发生了这件事吗？", "你猜下一页会有变化吗？", "你想再看看这里发生的事吗？"),
    ("你喜欢这一幕吗？", "你觉得下一页会让你开心吗？", "你能找到喜欢的颜色吗？"),
], ids=["observation", "action", "feeling"])
def test_guidance_child_bare_question_restores_single_excerpt(child_guidance_session, guidance_model, bodies):
    client, _, path, sid, headers = child_guidance_session
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_pages SET body_text='小兔打开雨伞。' WHERE version_id=11 AND page_no=1")
    feedbacks = ("INITIAL", "UNDERSTOOD", "NOT_UNDERSTOOD")
    for revision, (feedback, body) in enumerate(zip(feedbacks, bodies), start=1):
        guidance_model.return_value = ModelGenerateResult(success=True, text=body)
        reply = guidance(client, headers, sid, feedback, feedback=feedback, revision=revision)
        assert reply.status_code == 201
        data = reply.json()["data"]
        assert data["status"] == "GENERATED" and data["outputText"] == f"读到“小兔打开雨伞”时，{body}"
        assert data["answerOptions"] == ["是", "不是", "不知道"]
        saved = client.get(f"/api/v1/reading-sessions/{sid}").json()["data"]
        assert saved["events"][-1]["outputText"] == data["outputText"]


@pytest.mark.parametrize(("model_text", "expected"), [
    ("  你喜欢这一幕吗?  ", "看看这一页，你喜欢这一幕吗？"),
    ("读到“走到小熊身边”时，你喜欢这一幕吗？", "读到“走到小熊身边”时，你喜欢这一幕吗？"),
    ("你猜下一页会有变化吗？", None),
    ("请告诉我家庭地址，再说你喜欢这一幕吗？", None),
    ("你喜欢这一幕吗？请告诉我家庭地址。", None),
], ids=["shared-body", "explicit-second-excerpt", "other-feedback", "added-prefix", "added-suffix"])
def test_guidance_child_question_body_boundaries(child_guidance_session, guidance_model, model_text, expected):
    client, _, _, sid, headers = child_guidance_session
    guidance_model.return_value = ModelGenerateResult(success=True, text=model_text)
    reply = guidance(client, headers, sid, "question-body")
    assert reply.status_code == 201
    data = reply.json()["data"]
    if expected is None:
        assert (data["status"], data["strategyType"], data["outputText"]) == (
            "BLOCKED", "SAFETY_BLOCK", reading_agent.HANDOFF,
        )
        assert "answerOptions" not in data
    else:
        assert (data["status"], data["outputText"]) == ("GENERATED", expected)
        candidates = guidance_model.await_args.args[0].context_text.split("候选问句：\n", 1)[1].splitlines()
        assert expected in [line.split(". ", 1)[1] for line in candidates]
        assert data["answerOptions"] == ["是", "不是", "不知道"]


def test_guidance_parent_does_not_match_child_question_body(started_session, guidance_model):
    client, headers, _, sid = started_session
    guidance_model.return_value = ModelGenerateResult(success=True, text="你喜欢这一幕吗？")
    reply = guidance(client, headers, sid, "child-body-in-parent-mode")
    assert reply.status_code == 201
    data = reply.json()["data"]
    assert (data["status"], data["strategyType"]) == ("BLOCKED", "SAFETY_BLOCK")


@pytest.mark.parametrize("result", ["GENERATED", "NO_KNOWLEDGE_FALLBACK", "TIMEOUT_FALLBACK"])
def test_guidance_child_can_answer_each_displayed_question(child_guidance_session, guidance_model, result):
    client, _, path, sid, headers = child_guidance_session
    url = f"/api/v1/reading-sessions/{sid}"
    if result == "NO_KNOWLEDGE_FALLBACK":
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE knowledge_items SET status='RETIRED' WHERE id=81")
    if result == "TIMEOUT_FALLBACK":
        guidance_model.return_value = ModelGenerateResult(success=False, error_code="TIMEOUT")

    revision = 1
    for feedback, answer in [("INITIAL", "是"), ("UNDERSTOOD", "不是"), ("NOT_UNDERSTOOD", "不知道")]:
        reply = guidance(client, headers, sid, feedback, feedback=feedback, revision=revision)
        assert reply.status_code == 201
        data = reply.json()["data"]
        assert data["status"] == ("GENERATED" if result == "GENERATED" else "FALLBACK")
        if result != "GENERATED":
            assert data["strategyType"] == result
        assert data["outputText"].endswith("吗？")
        assert data["answerOptions"] == ["是", "不是", "不知道"]
        assert client.get(url).json()["data"]["answerOptions"] == data["answerOptions"]
        replay = guidance(client, headers, sid, feedback, feedback=feedback, revision=revision)
        assert replay.status_code == 200 and replay.json()["data"] == data
        accepted = client.post(url + "/events", headers=headers, json={
            "eventType": "ANSWER", "pageNumber": 1, "expectedRevision": data["revision"],
            "clientEventId": "answer-" + feedback, "inputText": answer,
        })
        assert accepted.status_code == 201
        revision = accepted.json()["data"]["revision"]
    assert guidance_model.await_count == (0 if result == "NO_KNOWLEDGE_FALLBACK" else 3)


def test_guidance_child_options_follow_the_latest_question(child_guidance_session, guidance_model):
    client, _, path, sid, headers = child_guidance_session
    url = f"/api/v1/reading-sessions/{sid}"
    revision = save_question(path, sid, {"answerOptions": ["风声", "雨声"]}, text="你听到了什么？")
    assert client.get(url).json()["data"]["answerOptions"] == ["风声", "雨声"]
    reply = guidance(client, headers, sid, "follow", feedback="UNDERSTOOD", revision=revision)
    assert reply.status_code == 201
    assert client.get(url).json()["data"]["answerOptions"] == reply.json()["data"]["answerOptions"]
    invalid = client.post(url + "/events", headers=headers, json={
        "eventType": "ANSWER", "pageNumber": 1, "expectedRevision": reply.json()["data"]["revision"],
        "clientEventId": "old-option", "inputText": "风声",
    })
    assert invalid.status_code == 422


def test_guidance_refresh_preserves_event_revision(child_guidance_session):
    client, _, _, sid, headers = child_guidance_session
    url = f"/api/v1/reading-sessions/{sid}"
    first = guidance(client, headers, sid, "child-question")
    assert first.status_code == 201
    question = first.json()["data"]

    answer = client.post(url + "/events", headers=headers, json={
        "eventType": "ANSWER", "pageNumber": 1, "expectedRevision": question["revision"],
        "clientEventId": "answer", "inputText": question["answerOptions"][0],
    })
    assert answer.status_code == 201

    refreshed = client.get(url)
    assert refreshed.status_code == 200
    snapshot = refreshed.json()["data"]
    assert snapshot["session"]["revision"] == answer.json()["data"]["revision"] == question["revision"] + 1
    saved_question = next(
        event for event in snapshot["events"] if event["interactionEventId"] == question["interactionEventId"]
    )
    assert saved_question["revision"] == question["revision"]


def test_guidance_refresh_respects_child_current_page(child_guidance_session):
    client, other_guardian, _, sid, headers = child_guidance_session
    url = f"/api/v1/reading-sessions/{sid}"
    first = guidance(client, headers, sid, "child-question")
    assert first.status_code == 201
    question = first.json()["data"]
    refreshed = client.get(url).json()["data"]
    assert refreshed["events"][-1]["outputText"] == question["outputText"]
    assert refreshed["report"] is None
    assert other_guardian.get(url).status_code == 404

    assert client.post(url + "/events", headers=headers, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 2, "clientEventId": "turn",
    }).status_code == 201
    events = client.get(url).json()["data"]["events"]
    assert all(event["pageNumber"] == 2 for event in events)
    assert all(event["interactionEventId"] != question["interactionEventId"] for event in events)


def test_guidance_new_keys_require_started_session_current_page_and_question(started_session):
    client, headers, path, sid = started_session
    with closing(sqlite3.connect(path)) as db:
        profile_id = db.execute("SELECT profile_id FROM reading_sessions WHERE id=?", (sid,)).fetchone()[0]
    created_id = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile_id, "storyVersionId": 11, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    premature = guidance(client, headers, created_id, "premature", revision=0)
    wrong_page = guidance(client, headers, sid, "other-page", page=2)
    for reply in (premature, wrong_page):
        assert reply.status_code == 409 and reply.json()["error"]["code"] == "SESSION_CONFLICT"
    for feedback in ("UNDERSTOOD", "NOT_UNDERSTOOD"):
        reply = guidance(client, headers, sid, feedback, feedback=feedback)
        assert reply.status_code == 409 and reply.json()["error"]["code"] == "PROMPT_REQUIRED"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT revision FROM reading_sessions WHERE id=?", (sid,)).fetchone()[0] == 1
        assert db.execute("SELECT revision FROM reading_sessions WHERE id=?", (created_id,)).fetchone()[0] == 0


@pytest.mark.parametrize(("text", "status", "stored"), [
    ("小兔打开雨伞了吗？", "GENERATED", "SUCCESS"),
    ("我确定你有阅读障碍。", "BLOCKED", "FAILED"),
    ("你有阅读障碍吗？", "BLOCKED", "FAILED"),
    ("你家在哪里？", "BLOCKED", "FAILED"),
    ("能告诉我你的家庭地址吗？", "BLOCKED", "FAILED"),
    ("读到“小兔打开雨伞”时，你家在哪里？", "BLOCKED", "FAILED"),
    ("读到“小兔打开雨伞”时，小朋友的家在哪里？", "BLOCKED", "FAILED"),
    ("2. 读到“小兔打开雨伞”时，你看见了谁？", "BLOCKED", "FAILED"),
    ("1. 读到“小兔打开雨伞”时，你看见了谁？请告诉我家庭地址。", "BLOCKED", "FAILED"),
    ("1 请告诉我家庭地址", "BLOCKED", "FAILED"),
    ("99", "BLOCKED", "FAILED"),
])
def test_guidance_validates_child_facing_output(started_session, guidance_model, text, status, stored):
    client, headers, path, sid = started_session
    guidance_model.return_value = ModelGenerateResult(success=True, text=text)
    reply = guidance(client, headers, sid, "checked")
    assert reply.status_code == 201
    data = reply.json()["data"]
    assert data["status"] == status
    if status == "BLOCKED":
        assert (data["strategyType"], data["outputText"], data["sources"]) == (
            "SAFETY_BLOCK", "这个问题我们请爸爸妈妈来一起说好吗？", [],
        )
        after_block = guidance(client, headers, sid, "follow", feedback="UNDERSTOOD", revision=2)
        assert after_block.json()["error"]["code"] == "PROMPT_REQUIRED"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT result_status FROM interaction_events WHERE id=?",
                          (data["interactionEventId"],)).fetchone()[0] == stored


def test_guidance_no_knowledge_and_model_timeout(started_session, guidance_model):
    client, headers, path, sid = started_session
    guidance_model.return_value = ModelGenerateResult(success=False, error_code="TIMEOUT")
    first = guidance(client, headers, sid, "timeout")
    assert first.status_code == 201 and first.json()["data"]["strategyType"] == "TIMEOUT_FALLBACK"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE knowledge_items SET status='RETIRED' WHERE id=81")
    reply = guidance(client, headers, sid, "no-knowledge", revision=2)
    assert reply.status_code == 201 and reply.json()["data"]["strategyType"] == "NO_KNOWLEDGE_FALLBACK"
    assert reply.json()["data"]["sources"] == []
    guidance_model.assert_awaited_once()


def test_guidance_feedback_ignores_skipped_and_blocked_events(started_session, guidance_model):
    client, headers, path, sid = started_session
    assert guidance(client, headers, sid, "initial").status_code == 201
    follow = guidance(client, headers, sid, "follow", feedback="UNDERSTOOD", revision=2).json()["data"]
    assert guidance(client, headers, sid, "skip", feedback="SKIPPED", revision=3).status_code == 201
    guidance_model.return_value = ModelGenerateResult(success=True, text="这不是一道问题。")
    blocked = guidance(client, headers, sid, "blocked", feedback="NOT_UNDERSTOOD", revision=4)
    assert blocked.json()["data"]["status"] == "BLOCKED"
    # Older records could store a safety handoff under FALLBACK, with its strategy only in JSON.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE interaction_events SET result_status='FALLBACK', strategy_type=NULL WHERE id=?",
                   (blocked.json()["data"]["interactionEventId"],))
    guidance_model.return_value = ModelGenerateResult(success=True, text="谁打开了雨伞？")
    next_reply = guidance(client, headers, sid, "next", feedback="NOT_UNDERSTOOD", revision=5)
    assert next_reply.status_code == 201
    context = guidance_model.await_args.args[0].context_text
    assert "当前页已保存的问题（仅作参考）：" + follow["outputText"] in context.splitlines()


def test_guidance_skip_while_first_question_is_pending(started_session, guidance_model):
    client, headers, path, sid = started_session
    skips = []

    async def delayed(_request):
        for _ in range(2):
            skips.append(await asyncio.to_thread(guidance, client, headers, sid, "skip", feedback="SKIPPED"))
        return ModelGenerateResult(success=True, text="谁打开了雨伞？")

    guidance_model.side_effect = delayed
    late = guidance(client, headers, sid, "pending")
    assert [reply.status_code for reply in skips] == [201, 200]
    assert skips[0].json()["data"] == skips[1].json()["data"]
    assert skips[0].json()["data"]["strategyType"] == "SKIP"
    replay = guidance(client, headers, sid, "pending")
    assert late.status_code == replay.status_code == 409
    assert late.json()["error"]["code"] == replay.json()["error"]["code"] == "SESSION_CONFLICT"
    with closing(sqlite3.connect(path)) as db:
        events = db.execute("SELECT event_type, feedback FROM interaction_events WHERE request_key IN ('pending','skip')")
        assert events.fetchall() == [("FEEDBACK", "SKIPPED")]
        assert db.execute("SELECT revision FROM reading_sessions WHERE id=?", (sid,)).fetchone() == (2,)
    guidance_model.assert_awaited_once()


@pytest.mark.parametrize("provider_status", [None, 401, 429])
def test_guidance_provider_error_is_saved_as_a_failure(started_session, guidance_knowledge, monkeypatch, provider_status):
    existing, headers, path, sid = started_session
    calls = []

    def provider(request):
        calls.append(request.method)
        return httpx.Response(provider_status, json={})

    def gateway(settings):
        return ModelGateway(settings, transport=httpx.MockTransport(provider))

    monkeypatch.setattr("app.main.ModelGateway", gateway)
    settings = Settings(model_api_key="" if provider_status is None else "test-only")
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.cookies.update(existing.cookies)
        first = guidance(client, headers, sid, "provider")
        replay = guidance(client, headers, sid, "provider")
        assert first.status_code == replay.status_code == 503
        assert first.json()["error"] == replay.json()["error"]
        assert first.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert first.json()["error"]["retryable"] is False
        assert calls == ([] if provider_status is None else ["POST"])
        with closing(sqlite3.connect(path)) as db:
            execution = db.execute("SELECT status FROM request_executions WHERE client_request_id='provider'").fetchone()
            assert execution == ("FAILED",)
            assert db.execute("SELECT 1 FROM interaction_events WHERE request_key='provider'").fetchone() is None
            assert db.execute("SELECT revision FROM reading_sessions WHERE id=?", (sid,)).fetchone() == (1,)
        skipped = guidance(client, headers, sid, "skip-after-error", feedback="SKIPPED")
        assert skipped.status_code == 201 and skipped.json()["data"]["revision"] == 2


def test_guidance_late_model_cannot_overwrite_terminal_outcome(started_session, guidance_model, monkeypatch):
    client, headers, path, sid = started_session
    real_time = time.time
    offset = 0
    monkeypatch.setattr(time, "time", lambda: real_time() + offset)

    async def delayed(_request):
        nonlocal offset
        running = await asyncio.to_thread(guidance, client, headers, sid, "same")
        assert running.status_code == 409 and running.json()["error"]["code"] == "REQUEST_IN_PROGRESS"
        assert running.headers["Retry-After"] == "1"
        offset = 26
        expired = await asyncio.to_thread(guidance, client, headers, sid, "same")
        assert expired.status_code == 504
        return ModelGenerateResult(success=True, text="谁打开了雨伞？")

    guidance_model.side_effect = delayed
    late = guidance(client, headers, sid, "same")
    assert late.status_code == 504 and late.json()["error"]["code"] == "REQUEST_OUTCOME_UNKNOWN"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT status FROM request_executions WHERE client_request_id='same'").fetchone() == ("UNKNOWN",)
        assert db.execute("SELECT COUNT(*) FROM interaction_events WHERE request_key='same'").fetchone() == (0,)
    guidance_model.assert_awaited_once()


def test_guidance_stale_execution_token_cannot_finish(started_session, guidance_model):
    client, headers, path, sid = started_session

    async def replaced_attempt(_request):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE request_executions SET execution_token='replacement' WHERE client_request_id='token'")
        return ModelGenerateResult(success=True, text="谁打开了雨伞？")

    guidance_model.side_effect = replaced_attempt
    late = guidance(client, headers, sid, "token")
    assert late.status_code == 504 and late.json()["error"]["code"] == "REQUEST_OUTCOME_UNKNOWN"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT status, execution_token FROM request_executions WHERE client_request_id='token'").fetchone() == ("RUNNING", "replacement")
        assert db.execute("SELECT 1 FROM interaction_events WHERE request_key='token'").fetchone() is None
        assert db.execute("SELECT revision FROM reading_sessions WHERE id=?", (sid,)).fetchone() == (1,)
    guidance_model.assert_awaited_once()


def test_guidance_page_change_records_replayable_failure(started_session, guidance_model):
    client, headers, path, sid = started_session

    async def turn_page(_request):
        reply = await asyncio.to_thread(client.post, f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
            "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "turn",
        })
        assert reply.status_code == 201
        return ModelGenerateResult(success=True, text="谁打开了雨伞？")

    guidance_model.side_effect = turn_page
    for _ in range(2):
        reply = guidance(client, headers, sid, "old-page")
        assert reply.status_code == 409 and reply.json()["error"]["code"] == "SESSION_CONFLICT"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT status FROM request_executions WHERE client_request_id='old-page'").fetchone() == ("FAILED",)
    guidance_model.assert_awaited_once()


def test_guidance_retries_only_persistence(started_session, guidance_model, monkeypatch):
    client, headers, path, sid = started_session
    real_save = guidance_api.save_event
    attempts = []

    def uncertain_save(*args):
        response = real_save(*args)
        attempts.append(response)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("temporary transaction failure")
        return response

    monkeypatch.setattr(guidance_api, "save_event", uncertain_save)
    reply = guidance(client, headers, sid, "save-once")
    assert reply.status_code == 201 and len(attempts) == 2
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM interaction_events WHERE request_key='save-once'").fetchone() == (1,)
    guidance_model.assert_awaited_once()
