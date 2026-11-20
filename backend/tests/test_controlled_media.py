"""Tests for DCL-005 controlled cover/page image resolution, full decoding, pinning, and API-024 delivery."""

import base64
import hashlib
import json
import shutil
import sqlite3
import struct
import subprocess
import sys
import zlib
from contextlib import closing
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, WebPImagePlugin

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "database"))

from app.api.v1.auth import _login_attempts  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import app, create_app  # noqa: E402
from app.services import story_media  # noqa: E402
from app.services.story_media import (  # noqa: E402
    DEFAULT_MEDIA_ROOT,
    REPO_ROOT,
    SEED_COVER_SOURCE_REF,
    resolve_cover_asset,
    verify_and_detect_image,
)
from app.speech_config import SpeechSettings  # noqa: E402
from import_seed import SEED_PUBLISHER, import_seed  # noqa: E402

SCHEMA = ROOT / "database" / "schema.sql"


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _make_png(rgb: tuple[int, int, int] = (220, 60, 40), width: int = 2, height: int = 2) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


PNG_RED = _make_png((220, 40, 40))
PNG_BLUE = _make_png((40, 80, 220))
VALID_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMU"
    "FRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQU"
    "FBQUFBT/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAA"
    "F9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNk"
    "ZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6e"
    "rx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJB"
    "UQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eH"
    "l6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oA"
    "DAMBAAIRAxEAPwD8rZ55LmaSaaRpZZGLvI7EszE5JJPUmiiis6fwL0OvF/7xU/xP8z//2Q=="
)
VALID_WEBP = base64.b64decode("UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoBAAEAAQAcJaQAA3AA/v3AgAA=")


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    db_path = tmp_path / "media.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(db_path))
    with closing(sqlite3.connect(db_path)) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
    _login_attempts.clear()
    with TestClient(app, base_url="https://testserver") as first, TestClient(app, base_url="https://testserver") as second:
        for client, account in ((first, "guardian-one"), (second, "guardian-two")):
            creds = {"account": account, "password": "strong-pass-123"}
            assert client.post("/api/v1/auth/registrations", json=creds).status_code == 201
            assert client.post("/api/v1/auth/sessions", json=creds).status_code == 201
        yield first, second, db_path, tmp_path
    _login_attempts.clear()


def _library_book(db_path, version_id, storybook_id, version_no, images, *,
                  status="PUBLISHED", seed_cover=True, branching=0):
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("INSERT OR IGNORE INTO storybooks VALUES (?, NULL, 'LIBRARY', 1)", (storybook_id,))
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             source_ref, license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
            VALUES (?, ?, ?, '封面绘本', '["动物"]', 3, 5, ?, 'ok', ?, 1, 1, 1, ?)""",
                   (version_id, storybook_id, version_no, SEED_COVER_SOURCE_REF if seed_cover else None,
                    status, branching))
        for page_no, image in enumerate(images, start=1):
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (?, ?, 't', ?)",
                       (version_id, page_no, image))


def test_seed_cover_resolution_and_private_media_delivery(media_env):
    first, second, db_path, _ = media_env
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("INSERT INTO storybooks VALUES (2, NULL, 'LIBRARY', 1)")
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             source_ref, license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
            VALUES (2, 2, 1, '小狐狸的雨伞', '["勇气"]', 3, 4, ?, 'ok', 'PUBLISHED', 1, 1, 1, 0)""",
            (SEED_COVER_SOURCE_REF,))
        db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (2, 1, '下雨了', '/assets/placeholder.png')")

    detail = first.get("/api/v1/story-versions/2")
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["coverRef"] == "asset-2-cover"
    assert data["pages"][0]["imageRef"] is None
    shelf = first.get("/api/v1/storybooks").json()["data"]["items"]
    assert len(shelf) == 1 and shelf[0]["coverRef"] == "asset-2-cover" and shelf[0]["pages"][0]["imageRef"] is None

    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.get("/api/v1/media/asset-2-cover").status_code == 401

    resp = second.get("/api/v1/media/asset-2-cover")
    expected_bytes = (REPO_ROOT / "knowledge-base_v2.5" / "图片" / "PIC-B01_小狐狸的雨伞.png").read_bytes()
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "private, no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.content == expected_bytes


def test_page_image_refs_access_control_child_scope_withdrawn_and_traversal(media_env, monkeypatch):
    first, second, db_path, tmp_path = media_env
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "p1.png").write_bytes(PNG_RED)
    (media_root / "p2.jpg").write_bytes(VALID_JPEG)
    (media_root / "note.txt").write_bytes(b"not-image")
    (tmp_path / "secret.png").write_bytes(PNG_RED)
    cover_map = tmp_path / "cover_mapping.json"
    cover_map.write_text(json.dumps([{"book_no": "B-002", "storybook_id": 2, "cover_path": "p1.png"}]), encoding="utf-8")
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("ZHIYA_COVER_MAPPING_PATH", str(cover_map))

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        # id=2 is guardian-one ORIGINAL (must NOT match seed LIBRARY cover_mapping id=2).
        for bid, owner, src in ((2, 1, "ORIGINAL"), (10, None, "LIBRARY"), (11, 1, "ORIGINAL")):
            db.execute("INSERT INTO storybooks VALUES (?, ?, ?, 1)", (bid, owner, src))
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             source_ref, license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
            VALUES (20, 2, 1, '我的原创', '["动物"]', 3, 5, NULL, 'ok', 'APPROVED_PRIVATE', 1, 1, 1, 0)""")
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             source_ref, license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
            VALUES (100, 10, 1, '公开绘本', '["动物"]', 3, 5, NULL, 'ok', 'PUBLISHED', 1, 1, 1, 0)""")
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             source_ref, license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
            VALUES (110, 11, 1, '历史私有', '["动物"]', 3, 5, NULL, 'ok', 'RETIRED', 1, 1, 1, 0)""")

        for pno, ref in (
            (1, "p1.png"),
            (2, "p2.jpg"),
            (3, "/assets/placeholder.png"),
            (4, "小兔子站在屋子中间"),
            (5, "../secret.png"),
            (6, "note.txt"),
            (7, "missing.png"),
            (8, "C:p1.png"),
        ):
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (20, ?, ?, ?)",
                       (pno, f"body-{pno}", ref))
        db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (100, 1, 'pub', 'p1.png')")
        db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (110, 1, 'ret', 'p2.jpg')")

    own_book = first.get("/api/v1/story-versions/20").json()["data"]
    assert own_book["coverRef"] is None
    assert [p["imageRef"] for p in own_book["pages"]] == [
        "asset-20-page-1", "asset-20-page-2", None, None, None, None, None, None,
    ]
    assert first.get("/api/v1/media/asset-20-page-1").content == PNG_RED
    p2_resp = first.get("/api/v1/media/asset-20-page-2")
    assert p2_resp.status_code == 200 and p2_resp.headers["content-type"] == "image/jpeg"
    for blocked in (
        "asset-20-cover", "asset-20-page-3", "asset-20-page-4", "asset-20-page-5",
        "asset-20-page-6", "asset-20-page-7", "asset-20-page-8",
    ):
        assert first.get(f"/api/v1/media/{blocked}").status_code == 404
    assert second.get("/api/v1/media/asset-20-page-1").status_code == 404
    assert first.get("/api/v1/media/asset-110-page-1").content == VALID_JPEG

    # CHILD_SOLO only accesses versions explicitly granted in child_grant_books.
    h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    pid = first.post("/api/v1/child-profiles", headers=h1,
                     json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    sid = first.post("/api/v1/reading-sessions", headers=h1, json={
        "childProfileId": pid, "storyVersionId": 20, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    assert first.put(f"/api/v1/reading-sessions/{sid}/mode-settings", headers=h1, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": [20], "timeLimitSeconds": 300,
    }).status_code == 200
    assert first.get("/api/v1/media/asset-20-page-1").status_code == 200
    assert first.get("/api/v1/media/asset-100-page-1").status_code == 404  # PUBLISHED but ungranted

    # Withdrawing version 20 immediately blocks media access.
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'WITHDRAWN' WHERE id = 20")
    assert first.get("/api/v1/media/asset-20-page-1").status_code == 404

    # RETIRED LIBRARY version media is accessible only during an active reading session.
    assert second.get("/api/v1/media/asset-100-page-1").status_code == 200
    h2 = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}
    p2 = second.post("/api/v1/child-profiles", headers=h2,
                     json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    s2 = second.post("/api/v1/reading-sessions", headers=h2, json={
        "childProfileId": p2, "storyVersionId": 100, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'RETIRED' WHERE id = 100")
    assert second.get("/api/v1/media/asset-100-page-1").status_code == 200
    assert second.delete(f"/api/v1/reading-sessions/{s2}", headers=h2).status_code == 204
    assert second.get("/api/v1/media/asset-100-page-1").status_code == 404


def test_media_dependency_unavailable_returns_retryable_503(media_env, monkeypatch):
    first, _, db_path, tmp_path = media_env
    _library_book(db_path, 2, 2, 1, ["p1.png"])

    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path / "missing-media-dir"))
    for url in ("/api/v1/storybooks", "/api/v1/story-versions/2", "/api/v1/media/asset-2-page-1"):
        res = first.get(url)
        assert res.status_code == 503
        assert res.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert res.json()["error"]["retryable"] is True


def test_missing_default_media_root_fails_closed_for_reads_and_branches(media_env, monkeypatch):
    first, _, db_path, tmp_path = media_env
    _library_book(db_path, 2, 2, 1, ["/assets/placeholder.png", "none", "/assets/placeholder.png"], branching=1)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("INSERT INTO story_branches (id, version_id, from_page_no, next_page_no, choice_label, review_status) VALUES (201, 2, 1, 3, '去公园', 'APPROVED')")
    # Warm read while default media directory exists.
    assert first.get("/api/v1/media/asset-2-cover").status_code == 200

    h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    pid = first.post("/api/v1/child-profiles", headers=h1,
                     json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    sid = first.post("/api/v1/reading-sessions", headers=h1, json={
        "childProfileId": pid, "storyVersionId": 2, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=h1, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "st",
    }).status_code == 201

    # Temporarily remove default media directory (ZHIYA_MEDIA_ROOT unset).
    monkeypatch.delenv("ZHIYA_MEDIA_ROOT", raising=False)
    monkeypatch.setattr(story_media, "DEFAULT_MEDIA_ROOT", tmp_path / "moved-default-media")
    for url in ("/api/v1/storybooks", "/api/v1/story-versions/2", "/api/v1/media/asset-2-cover"):
        res = first.get(url)
        assert res.status_code == 503, url
        assert res.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert res.json()["error"]["retryable"] is True

    branch_payload = {"branchId": 201, "expectedRevision": 1, "clientEventId": "br-retry"}
    failed_branch = first.post(f"/api/v1/reading-sessions/{sid}/branches", headers=h1, json=branch_payload)
    assert failed_branch.status_code == 503
    assert failed_branch.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert failed_branch.json()["error"]["retryable"] is True
    session_state = first.get(f"/api/v1/reading-sessions/{sid}").json()["data"]["session"]
    assert (session_state["currentPage"], session_state["revision"]) == (1, 1)

    # Restore default media directory and retry the exact same branch request.
    monkeypatch.setattr(story_media, "DEFAULT_MEDIA_ROOT", DEFAULT_MEDIA_ROOT)
    retried = first.post(f"/api/v1/reading-sessions/{sid}/branches", headers=h1, json=branch_payload)
    assert retried.status_code == 201
    assert (retried.json()["data"]["currentPage"], retried.json()["data"]["revision"]) == (3, 2)


def test_branch_media_expired_child_grant_and_voice_media_compatibility(tmp_path, monkeypatch):
    db_path = tmp_path / "media-branch.sqlite3"
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "p1.png").write_bytes(PNG_RED)
    (media_root / "p3.webp").write_bytes(VALID_WEBP)
    monkeypatch.setenv("ZHIYA_DB_PATH", str(db_path))
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(media_root))
    with closing(sqlite3.connect(db_path)) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))

    class _FakeVoiceGateway:
        async def synthesize(self, *, text, voice_id, speech_rate):
            return b"RIFFfakewav"

    _login_attempts.clear()
    application = create_app(Settings(), SpeechSettings(api_key="test-key", tts_api_url="https://space.example.test/tts"))
    with TestClient(application, base_url="https://testserver") as first:
        application.state.voice_gateway = _FakeVoiceGateway()
        creds = {"account": "guardian-media", "password": "strong-pass-123"}
        assert first.post("/api/v1/auth/registrations", json=creds).status_code == 201
        assert first.post("/api/v1/auth/sessions", json=creds).status_code == 201
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("INSERT INTO storybooks VALUES (30, NULL, 'LIBRARY', 1)")
            db.execute("""INSERT INTO story_versions
                (id, storybook_id, version_no, title, topics_json, age_min, age_max,
                 source_ref, license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
                VALUES (30, 30, 1, '分支绘本', '["动物"]', 3, 5, '第三方绘本', 'ok', 'PUBLISHED', 1, 1, 1, 1)""")
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (30, 1, '第1页', 'p1.png', 2)")
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (30, 2, '第2页', 'none', NULL)")
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (30, 3, '第3页', 'p3.webp', NULL)")
            db.execute("INSERT INTO story_branches (id, version_id, from_page_no, next_page_no, choice_label, review_status) VALUES (301, 30, 1, 3, '走右边', 'APPROVED')")

        assert first.get("/api/v1/story-versions/30").json()["data"]["coverRef"] is None

        h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
        pid = first.post("/api/v1/child-profiles", headers=h1,
                         json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
        sid = first.post("/api/v1/reading-sessions", headers=h1, json={
            "childProfileId": pid, "storyVersionId": 30, "mode": "PARENT_CHILD",
        }).json()["data"]["readingSessionId"]
        assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=h1, json={
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "st",
        }).status_code == 201

        chosen = first.post(f"/api/v1/reading-sessions/{sid}/branches", headers=h1, json={
            "branchId": 301, "expectedRevision": 1, "clientEventId": "br",
        })
        assert chosen.status_code == 201
        assert chosen.json()["data"]["nextPage"]["imageRef"] == "asset-30-page-3"
        webp = first.get("/api/v1/media/asset-30-page-3")
        assert webp.status_code == 200 and webp.headers["content-type"] == "image/webp" and webp.content == VALID_WEBP

        tts = first.post("/api/v1/voice/syntheses", headers=h1, json={
            "readingSessionId": sid, "pageNumber": 3, "revision": 2,
        })
        assert tts.status_code == 201
        audio = first.get(tts.json()["data"]["audioUrl"])
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav" and audio.content == b"RIFFfakewav"

        sid2 = first.post("/api/v1/reading-sessions", headers=h1, json={
            "childProfileId": pid, "storyVersionId": 30, "mode": "PARENT_CHILD",
        }).json()["data"]["readingSessionId"]
        assert first.put(f"/api/v1/reading-sessions/{sid2}/mode-settings", headers=h1, json={
            "mode": "CHILD_SOLO", "allowedStoryVersionIds": [30], "timeLimitSeconds": 60,
        }).status_code == 200
        child_headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
        assert first.post(f"/api/v1/reading-sessions/{sid2}/events", headers=child_headers, json={
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "cst",
        }).status_code == 201
        assert first.get("/api/v1/media/asset-30-page-1").status_code == 200
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("UPDATE child_grants SET issued_at = 1, expires_at = 60001 WHERE revoked_at IS NULL")
        expired = first.get("/api/v1/media/asset-30-page-1")
        assert expired.status_code == 401 and expired.json()["error"]["code"] == "AUTH_REQUIRED"
        with closing(sqlite3.connect(db_path)) as db:
            settled = db.execute("SELECT status, end_reason FROM reading_sessions WHERE id = ?", (sid2,)).fetchone()
        assert settled == ("FINISHED", "TIME_LIMIT")
    _login_attempts.clear()


def test_docker_image_packages_every_seed_cover():
    mapping = json.loads((ROOT / "data" / "seed" / "cover_mapping.json").read_text(encoding="utf-8"))
    dockerfile = (ROOT / "infra" / "Dockerfile").read_text(encoding="utf-8")
    ignore = (ROOT / "infra" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    assert "COPY knowledge-base_v2.5/图片/ /app/knowledge-base_v2.5/图片/" in dockerfile
    allowed = {line[1:] for line in ignore if line.startswith("!")}
    assert {"knowledge-base_v2.5", "knowledge-base_v2.5/图片"} <= allowed
    patterns = [rule.rsplit("*", 1)[-1] for rule in allowed if rule.startswith("knowledge-base_v2.5/图片/*")]
    assert len(mapping) == 20
    for entry in mapping:
        cover = ROOT / entry["cover_path"]
        assert cover.is_file() and cover.suffix in patterns, entry
        assert verify_and_detect_image(cover.read_bytes()) == "image/png", entry


@pytest.mark.parametrize("explicit_root", [False, True])
def test_repository_mapping_resolves_with_default_and_explicit_media_root(monkeypatch, explicit_root):
    if explicit_root:
        monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(DEFAULT_MEDIA_ROOT))
    else:
        monkeypatch.delenv("ZHIYA_MEDIA_ROOT", raising=False)
    monkeypatch.delenv("ZHIYA_COVER_MAPPING_PATH", raising=False)
    resolved = [resolve_cover_asset(i, 1, "LIBRARY", SEED_COVER_SOURCE_REF) for i in range(1, 100)]
    assert sum(item is not None for item in resolved) == 20


def test_cover_is_pinned_to_story_version_on_filename_reuse_and_mapping_replacement(media_env, monkeypatch):
    first, _, db_path, tmp_path = media_env
    media_root = tmp_path / "media"
    media_root.mkdir()
    # Same-filename overwrite scenario (book 2: version 20 -> version 21 both mapped to shared-cover.png).
    (media_root / "shared-cover.png").write_bytes(PNG_RED)
    # Mapping-replacement scenario (book 3: version 30 mapped to v1.png; updated mapping lists ONLY version 31 -> v2.png).
    (media_root / "v1.png").write_bytes(PNG_RED)
    (media_root / "v2.png").write_bytes(PNG_BLUE)
    mapping = tmp_path / "covers.json"
    mapping.write_text(json.dumps([
        {"storybook_id": 2, "cover_path": "shared-cover.png"},
        {"storybook_id": 3, "cover_path": "v1.png"},
    ]), encoding="utf-8")
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("ZHIYA_COVER_MAPPING_PATH", str(mapping))

    _library_book(db_path, 20, 2, 1, ["none"])
    _library_book(db_path, 30, 3, 1, ["none"])
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    profile = first.post("/api/v1/child-profiles", headers=headers,
                         json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    with monkeypatch.context() as missing_media:
        missing_media.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path / "offline"))
        failed = first.post("/api/v1/reading-sessions", headers=headers, json={
            "childProfileId": profile, "storyVersionId": 20, "mode": "PARENT_CHILD",
        })
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert failed.json()["error"]["retryable"] is True
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT count(*) FROM reading_sessions WHERE version_id = 20").fetchone() == (0,)
    # Bind active sessions to both v20 and v30 (note v30 is bound via POST /reading-sessions without fetching media first).
    assert first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": 20, "mode": "PARENT_CHILD",
    }).status_code == 201
    assert first.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": 30, "mode": "PARENT_CHILD",
    }).status_code == 201

    # Retire old versions, overwrite shared-cover.png in place, and replace mapping to list ONLY version 21 and 31.
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'RETIRED' WHERE id IN (20, 30)")
    (media_root / "shared-cover.png").write_bytes(PNG_BLUE)
    mapping.write_text(json.dumps([
        {"storybook_id": 2, "cover_path": "shared-cover.png"},
        {"storybook_id": 3, "version_no": 2, "cover_path": "v2.png"},
    ]), encoding="utf-8")
    _library_book(db_path, 21, 2, 2, ["none"])
    _library_book(db_path, 31, 3, 2, ["none"])

    assert first.get("/api/v1/media/asset-20-cover").content == PNG_RED
    assert first.get("/api/v1/media/asset-21-cover").content == PNG_BLUE
    assert first.get("/api/v1/story-versions/20").json()["data"]["coverRef"] == "asset-20-cover"
    assert first.get("/api/v1/story-versions/21").json()["data"]["coverRef"] == "asset-21-cover"

    assert first.get("/api/v1/media/asset-30-cover").content == PNG_RED
    assert first.get("/api/v1/media/asset-31-cover").content == PNG_BLUE
    assert first.get("/api/v1/story-versions/30").json()["data"]["coverRef"] == "asset-30-cover"
    assert first.get("/api/v1/story-versions/31").json()["data"]["coverRef"] == "asset-31-cover"


def _create_reading_session(client, version_id):
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    profile = client.post("/api/v1/child-profiles", headers=headers,
                          json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    response = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": version_id, "mode": "PARENT_CHILD",
    })
    assert response.status_code == 201
    return response.json()["data"]["readingSessionId"], headers


def test_missing_page_stays_missing_when_a_new_version_reuses_its_path(media_env, monkeypatch):
    first, _, db_path, root = media_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(root))
    _library_book(db_path, 20, 2, 1, ["later.png"], seed_cover=False)
    _create_reading_session(first, 20)
    assert first.get("/api/v1/media/asset-20-page-1").status_code == 404
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'RETIRED' WHERE id = 20")
    (root / "later.png").write_bytes(PNG_BLUE)
    _library_book(db_path, 21, 2, 2, ["later.png"], seed_cover=False)
    assert first.get("/api/v1/media/asset-21-page-1").content == PNG_BLUE
    assert first.get("/api/v1/story-versions/20").json()["data"]["pages"][0]["imageRef"] is None
    assert first.get("/api/v1/media/asset-20-page-1").status_code == 404


@pytest.mark.parametrize("unavailable", ["cover", "first_page"])
def test_opening_waits_for_bound_images_before_creating_session(media_env, monkeypatch, unavailable):
    client, _, db_path, root = media_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(root))
    mapping = root / "covers.json"
    mapping.write_text(json.dumps([{"storybook_id": 2, "cover_path": "cover.png"}]), encoding="utf-8")
    monkeypatch.setenv("ZHIYA_COVER_MAPPING_PATH", str(mapping))
    (root / "cover.png").write_bytes(PNG_RED)
    (root / "first.png").write_bytes(PNG_BLUE)
    _library_book(db_path, 20, 2, 1, ["first.png"])
    detail = client.get("/api/v1/story-versions/20").json()["data"]
    assert (detail["coverRef"], detail["pages"][0]["imageRef"]) == ("asset-20-cover", "asset-20-page-1")

    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    profile = client.post("/api/v1/child-profiles", headers=headers,
                          json={"ageBand": "AGE_3_4", "preferredMinutes": 10}).json()["data"]["childProfileId"]
    request = {"childProfileId": profile, "storyVersionId": 20, "mode": "PARENT_CHILD"}
    store = root / f".{db_path.name}.media_pins"
    manifest = json.loads((store / "versions" / "20.json").read_bytes())
    entry = manifest["cover"] if unavailable == "cover" else manifest["pages"]["1"]
    blob = store / "blobs" / entry["blob"]
    backup = blob.rename(root / "offline.png")
    with closing(sqlite3.connect(db_path)) as db:
        failed = client.post("/api/v1/reading-sessions", headers=headers, json=request)
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert failed.json()["error"]["retryable"] is True
        assert db.execute("SELECT COUNT(*) FROM reading_sessions").fetchone() == (0,)
        backup.rename(blob)
        assert client.post("/api/v1/reading-sessions", headers=headers, json=request).status_code == 201
        assert db.execute("SELECT COUNT(*) FROM reading_sessions").fetchone() == (1,)


def test_opening_with_placeholders_does_not_require_later_page_image(media_env, monkeypatch):
    client, _, db_path, root = media_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(root))
    (root / "second.png").write_bytes(PNG_BLUE)
    _library_book(db_path, 20, 2, 1, ["/assets/placeholder.png", "second.png"], seed_cover=False)
    detail = client.get("/api/v1/story-versions/20").json()["data"]
    assert detail["coverRef"] is None and detail["pages"][0]["imageRef"] is None
    assert detail["pages"][1]["imageRef"] == "asset-20-page-2"
    (root / f".{db_path.name}.media_pins" / "blobs").rename(root / "offline")
    _create_reading_session(client, 20)


@pytest.mark.parametrize("missing", ["versions", "store", "legacy"])
def test_bound_version_waits_for_original_snapshot(media_env, monkeypatch, missing):
    first, _, db_path, root = media_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(root))
    mapping = root / "covers.json"
    mapping.write_text(json.dumps([{"storybook_id": 2, "cover_path": "cover.png"}]), encoding="utf-8")
    monkeypatch.setenv("ZHIYA_COVER_MAPPING_PATH", str(mapping))
    source = root / "cover.png"
    source.write_bytes(PNG_RED)
    _library_book(db_path, 20, 2, 1, ["none"])
    assert first.get("/api/v1/media/asset-20-cover").content == PNG_RED
    _create_reading_session(first, 20)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'RETIRED' WHERE id = 20")
    source.write_bytes(PNG_BLUE)
    _library_book(db_path, 21, 2, 2, ["none"])
    assert first.get("/api/v1/media/asset-21-cover").content == PNG_BLUE

    store = db_path.parent / f".{db_path.name}.media_pins"
    if missing == "legacy":
        (store.with_name(store.name + ".bindings") / "20.sha256").unlink()
    unavailable = store if missing == "store" else store / "versions"
    backup = unavailable.rename(root / "offline")
    for url in ("/api/v1/media/asset-20-cover", "/api/v1/story-versions/20", "/api/v1/storybooks"):
        response = first.get(url)
        assert response.status_code == 503, url
        assert response.json()["error"]["retryable"] is True
    assert not unavailable.exists()
    backup.rename(unavailable)
    assert first.get("/api/v1/media/asset-20-cover").content == PNG_RED
    assert first.get("/api/v1/media/asset-21-cover").content == PNG_BLUE


def test_offline_pinned_blob_preserves_branch_progress_and_retry(media_env, monkeypatch):
    first, _, db_path, root = media_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(root))
    (root / "page.png").write_bytes(PNG_RED)
    _library_book(db_path, 20, 2, 1, ["none", "none", "page.png"], seed_cover=False, branching=1)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("""INSERT INTO story_branches
            (id, version_id, from_page_no, next_page_no, choice_label, review_status)
            VALUES (90, 20, 1, 3, '去公园', 'APPROVED')""")
    sid, headers = _create_reading_session(first, 20)
    session = f"/api/v1/reading-sessions/{sid}"
    assert first.post(f"{session}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start",
    }).status_code == 201
    image = "/api/v1/media/asset-20-page-3"
    assert first.get(image).content == PNG_RED
    blobs = db_path.parent / f".{db_path.name}.media_pins" / "blobs"
    backup = blobs.rename(root / "offline")
    payload = {"branchId": 90, "expectedRevision": 1, "clientEventId": "choose"}
    branch = first.post(f"{session}/branches", headers=headers, json=payload)
    for response in (branch, first.get(image), first.get("/api/v1/story-versions/20"), first.get("/api/v1/storybooks")):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert response.json()["error"]["retryable"] is True
    state = first.get(session).json()["data"]["session"]
    assert (state["currentPage"], state["revision"]) == (1, 1)
    backup.rename(blobs)
    for status in (201, 200):
        retried = first.post(f"{session}/branches", headers=headers, json=payload)
        assert retried.status_code == status
        assert retried.json()["data"]["nextPage"]["imageRef"] == "asset-20-page-3"
        assert retried.json()["data"]["revision"] == 2


def test_warm_shelf_skips_blob_payloads_and_detects_replacement(media_env, monkeypatch):
    first, _, db_path, root = media_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(root))
    (root / "page.png").write_bytes(PNG_RED)
    _library_book(db_path, 20, 2, 1, ["page.png"], seed_cover=False)
    shelf = "/api/v1/storybooks"
    image = "/api/v1/media/asset-20-page-1"
    assert first.get(shelf).json()["data"]["items"][0]["pages"][0]["imageRef"] == "asset-20-page-1"

    bytes_read = []
    open_path = Path.open

    def count_blob_reads(path, *args, **kwargs):
        stream = open_path(path, *args, **kwargs)
        if path.parent.name == "blobs" and stream.readable():
            read = stream.read

            def counted_read(*read_args):
                data = read(*read_args)
                bytes_read.append(len(data))
                return data

            stream.read = counted_read
        return stream

    monkeypatch.setattr(Path, "open", count_blob_reads)
    assert first.get(shelf).status_code == 200
    assert bytes_read == []
    assert first.get(image).content == PNG_RED
    assert sum(bytes_read) == len(PNG_RED)

    blob, = (db_path.parent / f".{db_path.name}.media_pins" / "blobs").iterdir()
    replacement = root / "replacement.png"
    replacement.write_bytes(PNG_BLUE)
    replacement.replace(blob)
    assert first.get(shelf).status_code == 503
    assert first.get(image).status_code == 503
    blob.write_bytes(PNG_RED)
    assert first.get(image).content == PNG_RED


def test_import_seed_pins_cover_assets_before_mapping_changes(tmp_path, monkeypatch):
    seed_db = tmp_path / "seeded.sqlite3"
    seed_dir = tmp_path / "seed"
    shutil.copytree(ROOT / "data" / "seed", seed_dir)
    monkeypatch.setenv("ZHIYA_DB_PATH", str(seed_db))
    with closing(sqlite3.connect(seed_db)) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
    assert import_seed(seed_db, seed_dir) is True

    # Replace cover_mapping with an empty list after import; imported version 2 still serves its pinned seed cover.
    alt_map = tmp_path / "empty-mapping.json"
    alt_map.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("ZHIYA_COVER_MAPPING_PATH", str(alt_map))

    _login_attempts.clear()
    with TestClient(app, base_url="https://testserver") as client:
        assert client.post("/api/v1/auth/sessions", json={
            "account": SEED_PUBLISHER, "password": "never-a-login-password",
        }).status_code == 401
        creds = {"account": "seed-u1", "password": "strong-pass-123"}
        assert client.post("/api/v1/auth/registrations", json=creds).status_code == 201
        assert client.post("/api/v1/auth/sessions", json=creds).status_code == 201
        items = client.get("/api/v1/storybooks").json()["data"]["items"]
        assert [book["storyVersionId"] for book in items] == list(range(2, 22))
        assert sum(len(book["pages"]) for book in items) == 104
        assert client.get("/api/v1/story-versions/1").status_code == 404
        for book in items:
            keys = [book["coverRef"], *(page["imageRef"] for page in book["pages"])]
            for key in keys:
                response = client.get(f"/api/v1/media/{key}")
                assert response.status_code == 200, key
                assert response.headers["content-type"] == "image/png"
        resp = client.get("/api/v1/media/asset-2-cover")
        expected = (REPO_ROOT / "knowledge-base_v2.5" / "图片" / "PIC-B01_小狐狸的雨伞.png").read_bytes()
        assert resp.status_code == 200 and resp.content == expected
    _login_attempts.clear()


def copy_draft_seed(destination):
    shutil.copytree(ROOT / "data" / "seed", destination)
    versions = destination / "story_versions.json"
    records = json.loads(versions.read_bytes())
    for record in records:
        record["status"] = "DRAFT"
    versions.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("legacy_draft", [False, True], ids=["new-seed", "old-seed"])
def test_seed_draft_picks_up_pages_added_before_publication(media_env, monkeypatch, legacy_draft):
    client, _, db_path, root = media_env
    seed_dir = root / "seed"
    copy_draft_seed(seed_dir)
    pages_file = seed_dir / "story_pages.json"
    pages = json.loads(pages_file.read_bytes())
    draft_pages = [page for page in pages if page["version_id"] == 2]
    assert draft_pages
    for page in draft_pages:
        page["image_ref"] = f"page-{page['page_no']}.png"
    pages_file.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(root))
    mapping = root / "covers.json"
    mapping.write_text(json.dumps([{"storybook_id": 2, "cover_path": "cover.png"}]), encoding="utf-8")
    monkeypatch.setenv("ZHIYA_COVER_MAPPING_PATH", str(mapping))
    (root / "cover.png").write_bytes(PNG_RED)
    assert import_seed(db_path, seed_dir)
    if legacy_draft:
        # Older importers recorded a final null for each missing draft page.
        store = root / f".{db_path.name}.media_pins"
        bindings = store.with_name(store.name + ".bindings")
        old_path = (store / "versions" / "2.draft.json").rename(store / "versions" / "2.json")
        old_manifest = json.loads(old_path.read_bytes())
        old_manifest["pages"] = {str(page["page_no"]): None for page in draft_pages}
        old_path.write_text(json.dumps(old_manifest), encoding="utf-8")
        (bindings / "2.draft.sha256").unlink()
        (bindings / "2.sha256").write_bytes(hashlib.sha256(old_path.read_bytes()).hexdigest().encode("ascii"))

    for page in draft_pages:
        (root / page["image_ref"]).write_bytes(PNG_BLUE)
    (root / "cover.png").write_bytes(PNG_BLUE)
    assert import_seed(db_path, seed_dir) is False
    with closing(sqlite3.connect(db_path)) as db, db:
        assert db.execute("SELECT status FROM story_versions WHERE id=2").fetchone() == ("DRAFT",)
        db.execute("UPDATE story_versions SET status='PUBLISHED', reviewed_by_id=1, reviewed_at=1 WHERE id=2")
    subprocess.run([sys.executable, "-B", str(ROOT / "database" / "import_seed.py"), "--pin-existing-media"],
                   check=True)
    for page in draft_pages:
        (root / page["image_ref"]).write_bytes(PNG_RED)
    detail = client.get("/api/v1/story-versions/2").json()["data"]
    assert client.get("/api/v1/media/" + detail["coverRef"]).content == PNG_RED
    expected = [f"asset-2-page-{page['page_no']}" for page in draft_pages]
    assert [page["imageRef"] for page in detail["pages"]] == expected
    for key in expected:
        assert client.get("/api/v1/media/" + key).content == PNG_BLUE


@pytest.mark.parametrize(("initial_seed_cover", "source", "legacy"), [
    pytest.param(False, "Editorial collection", False, id="complete-source"),
    pytest.param(True, "Editorial collection", False, id="drop-seed-cover"),
    pytest.param(False, SEED_COVER_SOURCE_REF, False, id="reserve-new-seed-cover"),
    pytest.param(True, "Editorial collection", True, id="old-draft-changes-source"),
])
def test_draft_source_change_rechecks_cover_before_publication(media_env, monkeypatch,
                                                               initial_seed_cover, source, legacy):
    client, _, db_path, root = media_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(root))
    mapping = root / "covers.json"
    mapping.write_text(json.dumps([{"storybook_id": 2, "cover_path": "cover.png"}]), encoding="utf-8")
    monkeypatch.setenv("ZHIYA_COVER_MAPPING_PATH", str(mapping))
    cover = root / "cover.png"
    cover.write_bytes(PNG_BLUE)
    _library_book(db_path, 20, 2, 1, ["/assets/placeholder.png"], status="DRAFT", seed_cover=initial_seed_cover)
    with closing(sqlite3.connect(db_path)) as db:
        story_media.pin_database_versions(db, db_path=db_path)
        if legacy:
            store = root / f".{db_path.name}.media_pins"
            (store / "versions" / "20.draft.json").rename(store / "versions" / "20.json")
            bindings = store.with_name(store.name + ".bindings")
            (bindings / "20.draft.sha256").rename(bindings / "20.sha256")
        cover.write_bytes(PNG_RED)
        (root / "page.png").write_bytes(PNG_BLUE)
        with db:
            db.execute("UPDATE story_versions SET source_ref=? WHERE id=20", (source,))
            db.execute("UPDATE story_pages SET image_ref='page.png' WHERE version_id=20")
        story_media.pin_database_versions(db, db_path=db_path)
        cover.write_bytes(PNG_BLUE)
        with db:
            db.execute("UPDATE story_versions SET status='PUBLISHED', reviewed_by_id=1, reviewed_at=2 WHERE id=20")
        story_media.pin_database_versions(db, db_path=db_path)

    book = client.get("/api/v1/story-versions/20").json()["data"]
    assert client.get("/api/v1/storybooks").json()["data"]["items"] == [book]
    assert book["pages"][0]["imageRef"] == "asset-20-page-1"
    assert client.get("/api/v1/media/asset-20-page-1").content == PNG_BLUE
    image = client.get("/api/v1/media/asset-20-cover")
    if source == SEED_COVER_SOURCE_REF:
        assert book["coverRef"] == "asset-20-cover" and image.content == PNG_RED
    else:
        assert book["coverRef"] is None and image.status_code == 404
    _create_reading_session(client, 20)


@pytest.mark.parametrize("release", [False, True], ids=["draft-version-number", "released-source"])
def test_draft_source_edits_preserve_snapshot_identity_checks(media_env, monkeypatch, release):
    _, _, db_path, root = media_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(root))
    mapping = root / "covers.json"
    mapping.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("ZHIYA_COVER_MAPPING_PATH", str(mapping))
    _library_book(db_path, 20, 2, 1, ["none"], status="DRAFT", seed_cover=False)
    with closing(sqlite3.connect(db_path)) as db:
        story_media.pin_database_versions(db, db_path=db_path)
        if release:
            with db:
                db.execute("UPDATE story_versions SET status='PUBLISHED', reviewed_by_id=1, reviewed_at=2 WHERE id=20")
            story_media.pin_database_versions(db, db_path=db_path)
            db.execute("UPDATE story_versions SET source_ref='changed after publication' WHERE id=20")
        else:
            db.execute("UPDATE story_versions SET version_no=2, source_ref='new source' WHERE id=20")
        with pytest.raises(story_media.MediaDependencyError, match="Invalid version media snapshot"):
            story_media.pin_database_versions(db, db_path=db_path)


def test_upgrade_nonseed_library_with_an_existing_session(media_env, monkeypatch):
    first, _, db_path, root = media_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(root))
    (root / "page.png").write_bytes(PNG_RED)
    _library_book(db_path, 20, 2, 1, ["page.png"], seed_cover=False)
    _create_reading_session(first, 20)
    store = db_path.parent / f".{db_path.name}.media_pins"
    (store / "versions" / "20.json").unlink()
    (store.with_name(store.name + ".bindings") / "20.sha256").unlink()
    image = "/api/v1/media/asset-20-page-1"
    assert first.get(image).status_code == 503
    subprocess.run([sys.executable, "-B", str(ROOT / "database" / "import_seed.py"), "--pin-existing-media"],
                   check=True)
    assert first.get(image).content == PNG_RED
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT id FROM story_versions").fetchall() == [(20,)]


def test_seed_upgrade_fills_missing_snapshots_and_preserves_existing_ones(media_env, monkeypatch):
    first, _, db_path, tmp_path = media_env
    cover = tmp_path / "cover.png"
    cover.write_bytes(PNG_RED)
    mapping = tmp_path / "covers.json"
    mapping.write_text(json.dumps([
        {"storybook_id": book_id, "cover_path": cover.name} for book_id in (2, 3)
    ]), encoding="utf-8")
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path))
    monkeypatch.setenv("ZHIYA_COVER_MAPPING_PATH", str(mapping))
    old_seed = tmp_path / "old-seed"
    copy_draft_seed(old_seed)
    assert import_seed(db_path, old_seed) is True

    # Version 2 predates snapshots. Version 3 was already published under the old layout.
    versions = db_path.parent / f".{db_path.name}.media_pins" / "versions"
    (versions / "2.draft.json").unlink()
    bindings = db_path.parent / f".{db_path.name}.media_pins.bindings"
    (bindings / "2.draft.sha256").unlink()
    (bindings / "3.draft.sha256").unlink()
    legacy = (versions / "3.draft.json").rename(versions / "3.json")
    existing = legacy.read_bytes()  # All pages were missing; legacy manifests recorded only positive pages.
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status='PUBLISHED', reviewed_by_id=1, reviewed_at=1 WHERE id=3")
        db.execute("UPDATE story_pages SET image_ref = ? WHERE version_id = 3 AND page_no = 1", (cover.name,))
    cover.write_bytes(PNG_BLUE)
    # Upgrade must wait for the original manifest when it already has a durable binding.
    pinned = (versions / "4.draft.json").rename(tmp_path / "offline-4.json")
    with pytest.raises(story_media.MediaDependencyError):
        import_seed(db_path)
    assert not (versions / "4.draft.json").exists()
    pinned.rename(versions / "4.draft.json")
    assert import_seed(db_path) is False
    assert (versions / "3.json").read_bytes() == existing
    cover.unlink()
    with monkeypatch.context() as offline:
        offline.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path / "offline"))
        assert import_seed(db_path) is False
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'PUBLISHED', reviewed_by_id = 1, reviewed_at = 1 WHERE id = 2")
    assert first.get("/api/v1/media/asset-2-cover").content == PNG_BLUE
    assert first.get("/api/v1/media/asset-3-cover").content == PNG_RED
    assert first.get("/api/v1/story-versions/3").json()["data"]["pages"][0]["imageRef"] is None
    assert first.get("/api/v1/media/asset-3-page-1").status_code == 404


@pytest.mark.parametrize("unwritable", ["versions", "blobs", ".media.sqlite3.media_pins.bindings"])
def test_snapshot_write_failure_returns_503_and_reads_recover(media_env, monkeypatch, unwritable):
    first, _, db_path, tmp_path = media_env
    _library_book(db_path, 40, 40, 1, ["page.png"], seed_cover=False)
    (tmp_path / "page.png").write_bytes(PNG_RED)
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path))
    urls = ("/api/v1/storybooks", "/api/v1/story-versions/40", "/api/v1/media/asset-40-page-1")
    link = story_media.os.link

    def refuse_write(src, dst):
        if Path(dst).parent.name == unwritable:
            raise PermissionError("test snapshot store is read-only")
        return link(src, dst)

    with monkeypatch.context() as offline:
        offline.setattr(story_media.os, "link", refuse_write)
        for url in urls:
            response = first.get(url)
            assert response.status_code == 503, url
            assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
            assert response.json()["error"]["retryable"] is True
    assert not list((db_path.parent / f".{db_path.name}.media_pins").rglob(".tmp-*"))
    assert first.get(urls[0]).status_code == 200
    assert first.get(urls[1]).json()["data"]["pages"][0]["imageRef"] == "asset-40-page-1"
    assert first.get(urls[2]).content == PNG_RED


def test_missing_default_cover_mapping_is_a_retryable_dependency_failure(media_env, monkeypatch):
    first, _, db_path, tmp_path = media_env
    _library_book(db_path, 2, 2, 1, ["none"])
    monkeypatch.delenv("ZHIYA_COVER_MAPPING_PATH", raising=False)
    monkeypatch.setattr(story_media, "DEFAULT_COVER_MAPPING", tmp_path / "missing.json")
    for url in ("/api/v1/storybooks", "/api/v1/story-versions/2", "/api/v1/media/asset-2-cover"):
        res = first.get(url)
        assert res.status_code == 503, url
        assert res.json()["error"] == {**res.json()["error"], "code": "DEPENDENCY_UNAVAILABLE", "retryable": True}
    # A single unmapped book is a confirmed missing image, not a dependency failure.
    monkeypatch.setattr(story_media, "DEFAULT_COVER_MAPPING", ROOT / "data" / "seed" / "cover_mapping.json")
    _library_book(db_path, 40, 40, 1, ["none"])
    assert first.get("/api/v1/story-versions/40").json()["data"]["coverRef"] is None
    assert first.get("/api/v1/media/asset-40-cover").status_code == 404


def test_unreadable_later_frame_blocks_new_and_preexisting_media_keys(media_env, monkeypatch):
    first, _, db_path, tmp_path = media_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path))
    with BytesIO() as stream, Image.new("RGB", (2, 2), "red") as red, Image.new("RGB", (2, 2), "blue") as blue:
        red.save(stream, format="WEBP", save_all=True, append_images=[blue], duration=100, lossless=True)
        animation = stream.getvalue()
    (tmp_path / "story.webp").write_bytes(animation)
    _library_book(db_path, 50, 50, 1, ["story.webp"], seed_cover=False)
    assert first.get("/api/v1/media/asset-50-page-1").content == animation

    # Simulate a fresh process whose decoder cannot read the second frame of a previously pinned image.
    monkeypatch.setattr(story_media, "_VERIFIED_DIGESTS", {})
    load_frame = WebPImagePlugin.WebPImageFile.load

    def fail_on_second_frame(image):
        if image.tell() == 1:
            raise OSError("second frame could not be decoded")
        return load_frame(image)

    monkeypatch.setattr(WebPImagePlugin.WebPImageFile, "load", fail_on_second_frame)
    _library_book(db_path, 51, 51, 1, ["story.webp"], seed_cover=False)
    for version in (50, 51):
        detail = first.get(f"/api/v1/story-versions/{version}")
        assert detail.status_code == 200
        assert detail.json()["data"]["pages"][0]["imageRef"] is None
        assert first.get(f"/api/v1/media/asset-{version}-page-1").status_code == 404


def test_unreadable_images_are_rejected_and_decodable_files_use_their_actual_type(media_env, monkeypatch):
    first, _, db_path, tmp_path = media_env
    media_root = tmp_path / "media"
    media_root.mkdir()
    real_png = (REPO_ROOT / "knowledge-base_v2.5" / "图片" / "PIC-B01_小狐狸的雨伞.png").read_bytes()
    (media_root / "real.png").write_bytes(real_png)
    (media_root / "trunc12.png").write_bytes(real_png[:12])
    (media_root / "trunchalf.png").write_bytes(real_png[: len(real_png) // 2])
    (media_root / "trunc.jpg").write_bytes(VALID_JPEG[:-2])
    (media_root / "trunc.webp").write_bytes(VALID_WEBP[:-1])
    (media_root / "empty.png").write_bytes(b"")
    (media_root / "text.png").write_bytes(b"not an image at all")
    (media_root / "photo.png").write_bytes(VALID_JPEG)
    (media_root / "good.webp").write_bytes(VALID_WEBP)
    (media_root / "photo.txt").write_bytes(VALID_JPEG)
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(media_root))
    _library_book(
        db_path,
        50,
        50,
        1,
        [
            "real.png",
            "trunc12.png",
            "trunchalf.png",
            "trunc.jpg",
            "trunc.webp",
            "empty.png",
            "text.png",
            "photo.png",
            "good.webp",
            "photo.txt",
        ],
        seed_cover=False,
    )

    pages = first.get("/api/v1/story-versions/50").json()["data"]["pages"]
    assert [p["imageRef"] for p in pages] == [
        "asset-50-page-1",
        None,
        None,
        None,
        None,
        None,
        None,
        "asset-50-page-8",
        "asset-50-page-9",
        None,
    ]
    real_resp = first.get("/api/v1/media/asset-50-page-1")
    assert real_resp.status_code == 200 and real_resp.headers["content-type"] == "image/png"
    assert real_resp.content == real_png
    for invalid_page in (2, 3, 4, 5, 6, 7, 10):
        assert first.get(f"/api/v1/media/asset-50-page-{invalid_page}").status_code == 404
    photo = first.get("/api/v1/media/asset-50-page-8")
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg" and photo.content == VALID_JPEG
    webp = first.get("/api/v1/media/asset-50-page-9")
    assert webp.status_code == 200 and webp.headers["content-type"] == "image/webp" and webp.content == VALID_WEBP
