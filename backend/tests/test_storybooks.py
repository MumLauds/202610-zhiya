"""Permission, filtering and serialization checks for the guardian bookshelf."""

import sqlite3
from contextlib import closing
from pathlib import Path

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
