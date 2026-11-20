"""Administrator authoring, publication and knowledge versioning on an isolated database."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.v1.auth import _login_attempts
from app.main import app
from app.services.story_media import SEED_COVER_SOURCE_REF

ADMIN = "/api/v1/admin"
BOOK = {"title": "湖边的纸船", "ageBand": "AGE_4_5", "topics": ["观察"], "licenseNote": "课程内已授权插图",
        "pages": [{"pageNumber": 1, "text": "湖面有一只绿色纸船。", "imageRef": "page.png"}]}
KNOWLEDGE = {"itemKey": "boat-watch", "title": "观察船", "content": "看看湖边的小船。", "ageBand": None,
             "topicTags": ["观察"], "taskTypes": ["GUIDANCE"], "sourceTitle": "课程资料",
             "sourceReference": "教学手册", "locator": "第2页", "status": "APPROVED"}


@pytest.fixture
def editorial(tmp_path, monkeypatch):
    path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    media = tmp_path / "images"
    media.mkdir()
    Image.new("RGB", (4, 4), "green").save(media / "page.png")
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(media))
    schema = Path(__file__).resolve().parents[2] / "database" / "schema.sql"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(schema.read_text(encoding="utf-8"))
    _login_attempts.clear()
    with TestClient(app, base_url="https://testserver") as admin, TestClient(app, base_url="https://testserver") as parent:
        for client, account in ((admin, "test-editor"), (parent, "test-parent")):
            payload = {"account": account, "password": "test-password-456"}
            assert client.post("/api/v1/auth/registrations", json=payload).status_code == 201
            assert client.post("/api/v1/auth/sessions", json=payload).status_code == 201
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE accounts SET role='ADMIN' WHERE account='test-editor'")
        yield admin, parent, path, media
    _login_attempts.clear()


def auth(client, key=None):
    result = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    if key:
        result["Idempotency-Key"] = key
    return result


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/storybooks", BOOK), ("PATCH", "/story-versions/1", {"status": "PUBLISHED"}),
    ("GET", "/knowledge-items", None), ("POST", "/knowledge-items", KNOWLEDGE),
    ("PATCH", "/knowledge-items/1", {"status": "RETIRED"}),
])
def test_editor_permissions(editorial, method, path, body):
    admin, parent, _, _ = editorial
    with TestClient(app, base_url="https://testserver") as anonymous:
        assert anonymous.request(method, ADMIN + path, json=body).status_code == 401
    assert parent.request(method, ADMIN + path, json=body, headers=auth(parent)).status_code == 403
    if body:
        assert admin.request(method, ADMIN + path, json=body).status_code == 403


def test_library_publish_replace_withdraw_and_replay(editorial):
    admin, parent, path, _ = editorial
    def create(book, key):
        return admin.post(ADMIN + "/storybooks", json=book, headers=auth(admin, key))
    def status(vid, value):
        return admin.patch(f"{ADMIN}/story-versions/{vid}", json={"status": value}, headers=auth(admin))
    reply = create(BOOK, "book")
    assert reply.status_code == 201, reply.text
    draft = reply.json()["data"]
    vid, bid = draft["storyVersionId"], draft["storybookId"]
    assert draft["status"] == "DRAFT" and not parent.get("/api/v1/storybooks").json()["data"]["items"]
    assert create(BOOK, "book").json()["data"] == draft
    assert create({**BOOK, "title": "不同请求"}, "book").status_code == 409
    assert status(vid, "PUBLISHED").status_code == 200
    published = parent.get(f"/api/v1/story-versions/{vid}").json()["data"]
    image = published["pages"][0]["imageRef"]
    assert image and parent.get(f"/api/v1/media/{image}").status_code == 200
    profile = parent.post("/api/v1/child-profiles", headers=auth(parent),
                          json={"ageBand": "AGE_4_5", "preferredMinutes": 10}).json()["data"]
    session = parent.post("/api/v1/reading-sessions", headers=auth(parent), json={
        "childProfileId": profile["childProfileId"], "storyVersionId": vid, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    session_url = f"/api/v1/reading-sessions/{session}"
    with closing(sqlite3.connect(path)) as db:
        editor_id, = db.execute("SELECT id FROM accounts WHERE role='ADMIN'").fetchone()
        assert db.execute("SELECT reviewed_by_id, reviewed_at IS NOT NULL FROM story_versions WHERE id=?", (vid,)).fetchone() == (editor_id, 1)
    version = create({**BOOK, "storybookId": bid, "title": "纸船新版"}, "new-edition").json()["data"]
    assert version["versionNumber"] == 2
    assert status(version["storyVersionId"], "PUBLISHED").status_code == 200
    listed = parent.get("/api/v1/storybooks").json()["data"]["items"]
    assert [item["storyVersionId"] for item in listed] == [version["storyVersionId"]]
    assert parent.get(session_url).status_code == 200
    assert parent.get(f"/api/v1/media/{image}").status_code == 200
    assert status(vid, "WITHDRAWN").status_code == 200
    assert parent.get(session_url).status_code == 404
    assert parent.get(f"/api/v1/media/{image}").status_code == 404
    assert status(vid, "PUBLISHED").status_code == 409


def test_missing_image_can_be_repaired_without_publishing_partial_data(editorial):
    admin, _, path, media = editorial
    (media / "page.png").rename(media / "later.png")
    draft = admin.post(ADMIN + "/storybooks", json=BOOK, headers=auth(admin)).json()["data"]
    url = f"{ADMIN}/story-versions/{draft['storyVersionId']}"
    assert admin.get("/api/v1/media/" + draft["pages"][0]["imageRef"]).status_code == 404
    for expected in (422, 200):
        result = admin.patch(url, json={"status": "PUBLISHED"}, headers=auth(admin, "release"))
        assert result.status_code == expected, result.text
        if expected == 422:
            with closing(sqlite3.connect(path)) as db:
                assert db.execute("SELECT status,reviewed_at,approved_page_media_json FROM story_versions").fetchone() == ("DRAFT", None, None)
            (media / "later.png").rename(media / "page.png")


def test_admin_previews_the_same_page_bytes_that_are_published(editorial):
    admin, parent, _, media = editorial
    pages = admin.post(ADMIN + "/storybooks", json=BOOK, headers=auth(admin)).json()["data"]["pages"]
    listed = admin.get(ADMIN + "/story-versions").json()["data"]["items"][0]
    assert listed["pages"][0]["imageRef"] == pages[0]["imageRef"]
    url = "/api/v1/media/" + pages[0]["imageRef"]
    original = (media / "page.png").read_bytes()
    assert parent.get(url).status_code == 404
    preview = admin.get(url)
    assert preview.status_code == 200 and preview.content == original
    assert "no-store" in preview.headers["Cache-Control"]
    assert parent.get(url).status_code == 404
    Image.new("RGB", (4, 4), "blue").save(media / "page.png")
    release = admin.patch(f"{ADMIN}/story-versions/{listed['storyVersionId']}",
                          json={"status": "PUBLISHED"}, headers=auth(admin))
    assert release.status_code == 200
    assert parent.get(url).content == original


def test_knowledge_revisions_retirement_and_concurrent_replay(editorial):
    admin, _, path, _ = editorial
    url = ADMIN + "/knowledge-items"
    assert admin.post(url, json={k: v for k, v in KNOWLEDGE.items() if k != "ageBand"}, headers=auth(admin)).status_code == 422
    first = admin.post(url, json={**KNOWLEDGE, "ageBand": "AGE_4_5"}, headers=auth(admin)).json()["data"]
    id1 = first["knowledgeItemId"]
    assert admin.post(url, json=KNOWLEDGE, headers=auth(admin)).status_code == 409
    def patch(source, payload, key=None):
        return admin.patch(f"{url}/{source}", json=payload, headers=auth(admin, key))
    draft = patch(id1, {"content": "看看湖边的蓝色纸船。", "status": "DRAFT"}).json()["data"]
    assert draft["versionNumber"] == 2 and draft["ageBand"] == "AGE_4_5"
    assert admin.get(url, params={"status": "APPROVED", "query": "观察"}).json()["data"]["items"] == [first]
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: patch(draft["knowledgeItemId"], {"status": "APPROVED", "ageBand": None}, "approve"), range(2)))
    assert all(r.status_code == 200 for r in replies)
    assert replies[0].json()["data"] == replies[1].json()["data"]
    approved = replies[0].json()["data"]
    assert approved["versionNumber"] == 3 and approved["ageBand"] is None
    assert patch(id1, {"status": "RETIRED"}).status_code == 409
    assert patch(approved["knowledgeItemId"], {"status": "RETIRED", "content": "不可混入"}).status_code == 422
    retired = patch(approved["knowledgeItemId"], {"status": "RETIRED"}).json()["data"]
    assert retired["versionNumber"] == 4
    assert admin.get(url, params={"status": "APPROVED"}).json()["data"]["items"] == []
    assert [r["versionNumber"] for r in admin.get(url, params={"page": 2, "pageSize": 2}).json()["data"]["items"]] == [2, 1]
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT content,status,reviewed_at IS NOT NULL FROM knowledge_items WHERE id=?", (id1,)).fetchone() == (
            KNOWLEDGE["content"], "RETIRED", 1)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_seed_cover_and_reviewed_page_keep_their_published_bytes(editorial, monkeypatch):
    admin, parent, path, media = editorial
    draft = admin.post(ADMIN + "/storybooks", json=BOOK, headers=auth(admin)).json()["data"]
    vid, bid = draft["storyVersionId"], draft["storybookId"]
    original = (media / "page.png").read_bytes()
    mapping = media.parent / "covers.json"
    mapping.write_text(json.dumps([{"storybook_id": bid, "story_version_id": vid, "cover_path": "page.png"}]))
    monkeypatch.setenv("ZHIYA_COVER_MAPPING_PATH", str(mapping))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_versions SET source_ref=? WHERE id=?", (SEED_COVER_SOURCE_REF, vid))
    assert admin.patch(f"{ADMIN}/story-versions/{vid}", json={"status": "PUBLISHED"}, headers=auth(admin)).status_code == 200
    (media / "page.png").unlink()
    data = parent.get(f"/api/v1/story-versions/{vid}").json()["data"]
    for ref in (data["coverRef"], data["pages"][0]["imageRef"]):
        assert ref and parent.get(f"/api/v1/media/{ref}").content == original


def test_rejects_unusable_paths_and_excludes_family_owned_versions(editorial):
    admin, parent, path, _ = editorial
    pages = [{"pageNumber": 1, "text": "纸船", "imageRef": "page.png", "defaultNextPageNumber": 2},
             {"pageNumber": 2, "text": "湖边", "imageRef": "page.png"}]
    assert admin.post(ADMIN + "/storybooks", json={**BOOK, "pages": pages, "branches": [
        {"fromPageNumber": 2, "nextPageNumber": 1, "choiceLabel": "回起点"}]}, headers=auth(admin)).status_code == 422
    with closing(sqlite3.connect(path)) as db, db:
        owner, = db.execute("SELECT id FROM accounts WHERE role='GUARDIAN'").fetchone()
        book = db.execute("INSERT INTO storybooks(owner_guardian_id,source_type,created_at) VALUES(?,'ORIGINAL',1)", (owner,)).lastrowid
        private = db.execute("""INSERT INTO story_versions(storybook_id,version_no,title,topics_json,age_min,age_max,
            license_note,status,created_at,branching) VALUES(?,1,'家庭私稿','[]',3,4,'自用','PENDING_REVIEW',1,0)""", (book,)).lastrowid
        db.execute("INSERT INTO story_pages(version_id,page_no,body_text,image_ref) VALUES(?,1,'小船','page.png')", (private,))
    assert admin.post(ADMIN + "/storybooks", json={**BOOK, "storybookId": book}, headers=auth(admin)).status_code == 404
    assert admin.patch(f"{ADMIN}/story-versions/{private}", json={"status": "PUBLISHED"}, headers=auth(admin)).status_code == 404
    private_image = f"/api/v1/media/asset-{private}-page-1"
    assert parent.get(private_image).status_code == 200
    assert admin.get(private_image).status_code == 404


def test_old_database_gains_receipts_without_losing_existing_knowledge(editorial):
    admin, _, path, _ = editorial
    existing = admin.post(ADMIN + "/knowledge-items", json=KNOWLEDGE, headers=auth(admin)).json()["data"]
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE admin_write_receipts")
    with TestClient(app, base_url="https://testserver"):
        reply = admin.patch(f"{ADMIN}/knowledge-items/{existing['knowledgeItemId']}",
                            json={"status": "DRAFT"}, headers=auth(admin, "after-upgrade"))
        assert reply.status_code == 200 and reply.json()["data"]["versionNumber"] == 2
