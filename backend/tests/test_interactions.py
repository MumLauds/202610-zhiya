"""Tests for API-010 recommendations, API-015 ANSWER/FEEDBACK events, and API-018 branch selection."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1.auth import _login_attempts
from app.main import app

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
