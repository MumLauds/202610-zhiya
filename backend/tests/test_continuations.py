"""Tests for API-026 story version approvals and API-011 atomic continuation switching (TC-FR17-04/05)."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.api.v1.auth import _login_attempts
from app.main import app
from app.schemas.agent import ModelGenerateResult

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema.sql"


@pytest.fixture
def cont_env(tmp_path, monkeypatch):
    db_path = tmp_path / "continuations.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(db_path))
    with closing(sqlite3.connect(db_path)) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
    _login_attempts.clear()
    with TestClient(app, base_url="https://testserver") as first, TestClient(app, base_url="https://testserver") as second:
        for client, account in ((first, "guardian-one"), (second, "guardian-two")):
            creds = {"account": account, "password": "strong-pass-123"}
            assert client.post("/api/v1/auth/registrations", json=creds).status_code == 201
            assert client.post("/api/v1/auth/sessions", json=creds).status_code == 201
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("INSERT INTO storybooks VALUES (201, 1, 'ORIGINAL', 1)")
            db.execute("""INSERT INTO story_versions
                (id, storybook_id, version_no, title, topics_json, age_min, age_max,
                 license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
                VALUES (301, 201, 1, '小狐狸原创', '["互助"]', 3, 5, '原创授权', 'APPROVED_PRIVATE', 1, 100, 1, 0)""")
            for pno, nxt in ((1, 2), (2, 3), (3, 4), (4, None)):
                db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (301, ?, ?, 'none', ?)",
                           (pno, f"v1第{pno}页", nxt))
        h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
        pid = first.post("/api/v1/child-profiles", headers=h1,
                         json={"ageBand": "AGE_4_5", "preferredMinutes": 15}).json()["data"]["childProfileId"]
        yield first, second, db_path, pid
    _login_attempts.clear()


def _deny_image_read(monkeypatch, path):
    read_bytes = Path.read_bytes

    def read(candidate):
        if candidate == path:
            raise PermissionError("Image is temporarily locked")
        return read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", read)


@pytest.mark.parametrize(("advance_source", "locked"), [(False, False), (True, False), (False, True)],
                         ids=["switch", "stale-source", "locked-illustration"])
def test_generation_approval_and_continuation_switch(cont_env, monkeypatch, tmp_path, advance_source, locked):
    from PIL import Image
    from app.services import story_media

    client, other, db_path, profile = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    model_contexts = []

    class StoryModel:
        async def generate(self, request):
            model_contexts.append(json.loads(request.context_text))
            return ModelGenerateResult(success=True, text=json.dumps({
                "title": "森林里的朋友", "pages": ["朋友找到了树叶。", "他们带着树叶回家。"],
            }))

    monkeypatch.setattr(client.app.state, "model_adapter", StoryModel())
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path))
    cover = tmp_path / "first.png"
    Image.new("RGB", (2, 2), "green").save(cover)
    original_image = cover.read_bytes()
    templates = next(story_media._template_candidates("AGE_4_5", "互助", 4))
    third_image = tmp_path.joinpath(*story_media._image_path_parts(templates[2][0]))
    third_image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), "blue").save(third_image)
    preview_bytes = third_image.read_bytes()
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_pages SET image_ref = 'first.png' WHERE version_id = 301 AND page_no = 1")
    created = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": 301, "mode": "PARENT_CHILD",
    })
    assert created.status_code == 201, created.text
    session = created.json()["data"]["readingSessionId"]
    path = f"/api/v1/reading-sessions/{session}"
    for page, event in ((1, "STARTED"), (2, "PAGE_CHANGED")):
        assert client.post(f"{path}/events", headers=headers, json={
            "eventType": event, "pageNumber": page, "expectedRevision": page - 1, "clientEventId": f"read-{page}",
        }).status_code == 201

    payload = {"sourceReadingSessionId": session, "expectedRevision": 2, "topic": "互助",
               "characters": ["小兔子"], "scene": "森林", "branching": False, "clientRequestId": "generate-1"}
    assert other.post("/api/v1/story-generations", headers={"X-CSRF-Token": other.cookies["zhiya_csrf"]},
                      json=payload).status_code == 404
    with monkeypatch.context() as files:
        if locked:
            fourth_image = tmp_path.joinpath(*story_media._image_path_parts(templates[3][0]))
            fourth_image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (2, 2), "orange").save(fourth_image)
            _deny_image_read(files, fourth_image)
        generated = client.post("/api/v1/story-generations", headers=headers, json=payload)
    assert generated.status_code == 201, generated.text
    data = generated.json()["data"]
    version = data["storyVersionId"]
    assert data["status"] == "PENDING_REVIEW"
    assert data["continuation"]["resumePageNumber"] == 3
    assert data["continuation"]["sourceVisitedPageNumbers"] == [1, 2]
    assert data["pages"][0]["imageRef"] == "asset-301-page-1"
    assert [p["imageRef"] for p in data["pages"][2:]] == [f"asset-{version}-page-3", None]
    preview_url = f"/api/v1/media/asset-{version}-page-3"
    Image.new("RGB", (2, 2), "red").save(third_image)
    assert client.get(preview_url).content == preview_bytes
    assert other.get(preview_url).status_code == 404
    assert model_contexts[0]["previousPages"] == ["v1第1页", "v1第2页"]
    assert model_contexts[0]["ageBand"] == "AGE_4_5"
    replay = client.post("/api/v1/story-generations", headers=headers, json=payload)
    assert replay.status_code == 200 and replay.json()["data"] == data
    mismatch = client.post("/api/v1/story-generations", headers=headers, json={**payload, "scene": "海边"})
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(model_contexts) == 1
    assert client.get(path).json()["data"]["session"]["revision"] == 2

    Image.new("RGB", (2, 2), "red").save(cover)
    approved = client.post(f"/api/v1/story-versions/{version}/approvals", headers=headers,
                           json={"versionNumber": data["versionNumber"], "reviewedAllBranches": True})
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["pages"][3]["imageRef"] is None
    assert client.get(f"/api/v1/media/asset-{version}-page-1").content == original_image
    assert client.get(preview_url).content == preview_bytes
    if advance_source:
        assert client.post(f"{path}/events", headers=headers, json={
            "eventType": "PAGE_CHANGED", "pageNumber": 3, "expectedRevision": 2, "clientEventId": "read-3",
        }).status_code == 201
    switched = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": version, "mode": "PARENT_CHILD",
        "sourceReadingSessionId": session, "expectedRevision": 2, "clientRequestId": "switch-1",
    })
    if advance_source:
        assert switched.status_code == 409
        assert switched.json()["error"]["code"] == "CONTINUATION_STALE"
        assert client.post(f"{path}/events", headers=headers, json={
            "eventType": "PAGE_CHANGED", "pageNumber": 4, "expectedRevision": 3, "clientEventId": "read-4",
        }).status_code == 201
    else:
        assert switched.status_code == 201, switched.text
        assert switched.json()["data"]["currentPage"] == 3
        assert client.get(path).json()["data"]["session"]["status"] == "FINISHED"

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'WITHDRAWN' WHERE id = ?", (version,))
    revoked = client.post("/api/v1/story-generations", headers=headers, json=payload)
    assert revoked.status_code == 404 and "data" not in revoked.json()
    assert len(model_contexts) == 1


def test_original_generation_checks_and_persists_only_valid_drafts(cont_env, monkeypatch, tmp_path):
    client, other, db_path, _ = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    payload = {"ageBand": "AGE_4_5", "topic": "互助", "characters": ["小兔子"], "scene": "森林",
               "branching": True, "clientRequestId": "invalid-story"}
    texts = ["兔子到森林散步。", "兔子来到岔路口。", "兔子到树旁绕了一圈。",
             "兔子和朋友汇合。", "兔子和朋友一起回家。"]
    contexts = []

    class StoryModel:
        async def generate(self, request):
            contexts.append(json.loads(request.context_text))
            return ModelGenerateResult(success=True, text=json.dumps({
                "title": "森林里的朋友", "pages": texts if len(contexts) > 1 else [*texts[:-1], "询问家庭地址"],
                "choices": ["去树旁看看", "去找朋友"],
            }))

    monkeypatch.setattr(client.app.state, "model_adapter", StoryModel())
    for _ in range(2):
        rejected = client.post("/api/v1/story-generations", headers=headers, json=payload)
        assert rejected.status_code == 422
    assert len(contexts) == 1
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM storybooks").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM story_versions").fetchone() == (1,)
    valid = {**payload, "clientRequestId": "valid-story"}
    accepted = client.post("/api/v1/story-generations", headers=headers, json=valid)
    assert accepted.status_code == 201, accepted.text
    data = accepted.json()["data"]
    vid = data["storyVersionId"]
    assert [page["text"] for page in data["pages"]] == texts
    assert [page["imageRef"] for page in data["pages"]] == [f"asset-{vid}-page-{p}" for p in range(1, 6)]
    assert [(branch["fromPageNumber"], branch["nextPageNumber"]) for branch in data["branches"]] == [(2, 3), (2, 4)]
    image_url = f"/api/v1/media/asset-{vid}-page-1"
    preview = client.get(image_url)
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert other.get(image_url).status_code == 404
    assert client.get(f"/api/v1/story-versions/{vid}").status_code == 404
    assert client.post("/api/v1/story-generations", headers=headers, json=valid).status_code == 200
    with closing(sqlite3.connect(db_path)) as db:
        stored_pages = db.execute(
            "SELECT page_no, image_ref, image_license_note FROM story_pages WHERE version_id = ? ORDER BY page_no",
            (vid,),
        ).fetchall()
        assert len(stored_pages) == 5
        assert all(ref != "none" and note == "NO-COMMERCIAL" for _, ref, note in stored_pages)
        manifest = db.execute("SELECT approved_page_media_json FROM story_versions WHERE id = ?", (vid,)).fetchone()[0]
    blobs = db_path.parent / f".{db_path.name}.media_pins" / "blobs"
    pinned = blobs / json.loads(manifest)["1"]["blob"]
    approval_url = f"/api/v1/story-versions/{vid}/approvals"
    approval = {"versionNumber": 1, "reviewedAllBranches": True}
    with monkeypatch.context() as missing_templates:
        missing_templates.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path))
        offline = pinned.rename(tmp_path / pinned.name)
        try:
            unavailable = client.post(approval_url, headers=headers, json=approval)
            assert unavailable.status_code == 503, unavailable.text
            assert unavailable.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
            assert unavailable.json()["error"]["retryable"] is True
            with closing(sqlite3.connect(db_path)) as db:
                assert db.execute("SELECT status, approved_page_media_json FROM story_versions WHERE id = ?",
                                  (vid,)).fetchone() == ("PENDING_REVIEW", manifest)
        finally:
            offline.rename(pinned)
        approved = client.post(approval_url, headers=headers, json=approval)
        assert approved.status_code == 200, approved.text
        assert client.get(image_url).content == preview.content
    for page in approved.json()["data"]["pages"]:
        url = f"/api/v1/media/{page['imageRef']}"
        img = client.get(url)
        assert img.status_code == 200 and img.headers["content-type"] == "image/png" and len(img.content) > 0
        assert other.get(url).status_code == 404

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'WITHDRAWN' WHERE id = ?", (vid,))
    revoked = client.post("/api/v1/story-generations", headers=headers, json=valid)
    assert revoked.status_code == 404 and revoked.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert "data" not in revoked.json() and client.get(image_url).status_code == 404
    assert len(contexts) == 2


@pytest.mark.parametrize("locked", [False, True], ids=["missing", "locked"])
def test_legacy_draft_retries_missing_promised_image_before_preview_or_approval(cont_env, monkeypatch, tmp_path, locked):
    from PIL import Image
    from app.services import story_media

    client, _, db_path, _ = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path))
    paths = []
    for (ref, _), color in zip(next(story_media._template_candidates("AGE_3_4", "互助", 4)),
                               ["green", "blue", "orange", "purple"], strict=True):
        path = tmp_path.joinpath(*story_media._image_path_parts(ref))
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2, 2), color).save(path, format="PNG")
        paths.append(path)

    class StoryModel:
        async def generate(self, request):
            return ModelGenerateResult(success=True, text=json.dumps({
                "title": "一起散步", "pages": ["小兔出门。", "小兔遇见小熊。", "他们一起散步。", "小兔回家。"],
                "choices": [],
            }))

    monkeypatch.setattr(client.app.state, "model_adapter", StoryModel())
    created = client.post("/api/v1/story-generations", headers=headers, json={
        "ageBand": "AGE_3_4", "topic": "互助", "characters": ["小兔"], "scene": "森林",
        "branching": False, "clientRequestId": "legacy-promised-images",
    })
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    version = draft["storyVersionId"]
    urls = [f"/api/v1/media/{page['imageRef']}" for page in draft["pages"]]
    # The merged API stored the generation response before it supported draft image snapshots.
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET approved_page_media_json = NULL WHERE id = ?", (version,))
    unavailable_file = paths[-1]
    saved_image = unavailable_file.read_bytes()
    approval_url = f"/api/v1/story-versions/{version}/approvals"
    approval = {"versionNumber": 1, "reviewedAllBranches": True}
    first_png = paths[0].read_bytes()
    with monkeypatch.context() as files:
        if locked:
            _deny_image_read(files, unavailable_file)
        else:
            unavailable_file.unlink()
        assert client.get(urls[0]).content == first_png
        for response in (client.get(urls[-1]), client.post(approval_url, json=approval, headers=headers)):
            assert response.status_code == 503
            assert response.json()["error"]["retryable"] is True
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT status FROM story_versions WHERE id = ?", (version,)).fetchone() == ("PENDING_REVIEW",)

    unavailable_file.write_bytes(saved_image)
    Image.new("RGB", (2, 2), "red").save(paths[0], format="PNG")
    assert client.get(urls[-1]).content == saved_image
    assert client.post(approval_url, json=approval, headers=headers).status_code == 200
    assert client.get(urls[0]).content == first_png
    assert client.get(urls[-1]).content == saved_image


@pytest.mark.parametrize(("band", "topic"), [("AGE_3_4", "时间认知"), ("AGE_4_5", "互助"), ("AGE_4_5", "合作")])
def test_template_selection_prefers_topic_within_requested_age_band(band, topic):
    from app.services import story_media

    low, high = story_media.AGE_BAND_BOUNDS[band]
    eligible = [entry for entry in story_media._template_catalog()
                if entry["age_min"] <= low and entry["age_max"] >= high and len(entry["assets"]) >= 4]
    matching = [entry for entry in eligible if topic in entry["topics"]]
    selected = story_media.select_template_illustrations(band, topic, 4)
    assert selected in [entry["assets"][:4] for entry in matching or eligible]


@pytest.mark.parametrize(("licensed_count", "custom_root"), [(5, True), (2, True), (0, True), (5, False)],
                         ids=["custom-complete", "custom-partial", "custom-empty", "default-complete"])
def test_continuation_uses_readable_licensed_template_for_new_pages(cont_env, monkeypatch, tmp_path,
                                                                   licensed_count, custom_root):
    from unittest.mock import AsyncMock
    from PIL import Image
    from app.services import story_media

    client, _, db_path, profile = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    if custom_root:
        monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path))
    else:
        monkeypatch.delenv("ZHIYA_MEDIA_ROOT", raising=False)
        monkeypatch.setattr(story_media, "DEFAULT_MEDIA_ROOT", tmp_path)
    versions, catalog_pages = [], []
    # The on-topic template has one readable page in the partial case and none in the complete cases.
    for version_id, low, high, licensed, topic in [(1, 4, 5, licensed_count, "观察"), (2, 5, 6, 6, "互助"),
                                                   (3, 4, 5, 5 if licensed_count else 0, "互助")]:
        versions.append({"id": version_id, "age_min": low, "age_max": high, "topics_json": [topic],
                         "source_ref": story_media.SEED_COVER_SOURCE_REF})
        for n in range(1, 7):
            ref = f"{version_id}-{n}.png"
            if version_id != 3 or (licensed_count == 2 and n == 2):
                Image.new("RGB", (2, 2), (version_id, n, 0)).save(tmp_path / ref)
            elif n == 1:
                (tmp_path / ref).write_bytes(b"incomplete PNG")
            catalog_pages.append({"version_id": version_id, "page_no": n, "image_ref": ref,
                                  "image_license_note": story_media.TEMPLATE_PAGE_LICENSE if n <= licensed else None})
    for name, data in [("DEFAULT_TEMPLATE_VERSIONS", versions), ("DEFAULT_TEMPLATE_PAGES", catalog_pages)]:
        catalog = tmp_path / f"{name}.json"
        catalog.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(story_media, name, catalog)

    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text=json.dumps({
        "title": "一起散步", "pages": ["来到岔路口。", "走过树林。", "一起回家。"], "choices": ["走进树林", "去找朋友"],
    }))
    monkeypatch.setattr(client.app.state, "model_adapter", model)
    session = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": 301, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    for n, event in enumerate(("STARTED", "PAGE_CHANGED", "PAGE_CHANGED"), start=1):
        advanced = client.post(f"/api/v1/reading-sessions/{session}/events", headers=headers, json={
            "eventType": event, "pageNumber": n, "expectedRevision": n - 1, "clientEventId": f"read-{n}",
        })
        assert advanced.status_code == 201
    request = {"sourceReadingSessionId": session, "expectedRevision": 3, "topic": "互助",
               "characters": ["小兔"], "scene": "树林", "branching": True, "clientRequestId": "short-templates"}
    created = client.post("/api/v1/story-generations", headers=headers, json=request)
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    assert [p["text"] for p in draft["pages"][:3]] == [f"v1第{n}页" for n in (1, 2, 3)]
    with closing(sqlite3.connect(db_path)) as db:
        new_rows = db.execute("SELECT image_ref, image_license_note FROM story_pages "
                              "WHERE version_id = ? AND page_no > 3 ORDER BY page_no",
                              (draft["storyVersionId"],)).fetchall()
    available = min(licensed_count, 3)
    assert len(new_rows) == 3
    assert len({ref for ref, _ in new_rows[:available]}) == available
    licensed_refs = {f"1-{n}.png" for n in range(1, licensed_count + 1)}
    for page, (ref, note) in zip(draft["pages"][3:3 + available], new_rows[:available], strict=True):
        assert ref in licensed_refs and note == story_media.TEMPLATE_PAGE_LICENSE
        assert client.get(f"/api/v1/media/{page['imageRef']}").content == (tmp_path / ref).read_bytes()
    assert new_rows[available:] == [("none", None)] * (3 - available)
    assert all(p["imageRef"] is None for p in draft["pages"][3 + available:])
    assert client.post("/api/v1/story-generations", headers=headers, json=request).json()["data"] == draft
    model.generate.assert_awaited_once()
    approved = client.post(f"/api/v1/story-versions/{draft['storyVersionId']}/approvals", headers=headers,
                           json={"versionNumber": draft["versionNumber"], "reviewedAllBranches": True})
    assert approved.status_code == 200, approved.text
    approved_refs = [page["imageRef"] for page in approved.json()["data"]["pages"][3:]]
    assert approved_refs == [page["imageRef"] for page in draft["pages"][3:]]


def test_story_generation_validation_limits_and_mode_guards(cont_env, monkeypatch):
    client, _, db_path, profile = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    calls = []

    class StoryModel:
        async def generate(self, request):
            calls.append(json.loads(request.context_text))
            return ModelGenerateResult(success=True, text=json.dumps({
                "title": "三页后续分支",
                "pages": ["来到分岔路口。", "向左走遇见松鼠。", "一起安全回家。"],
                "choices": ["向左走", "直接回家"],
            }))

    # Anonymous and missing CSRF fail closed before reaching generation.
    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.post("/api/v1/story-generations", json={
            "ageBand": "AGE_4_5", "topic": "互助", "characters": ["小兔"], "scene": "森林",
            "branching": False, "clientRequestId": "anon-1",
        }).status_code == 401
    monkeypatch.setattr(client.app.state, "model_adapter", StoryModel())
    assert client.post("/api/v1/story-generations", json={
        "ageBand": "AGE_4_5", "topic": "互助", "characters": ["小兔"], "scene": "森林",
        "branching": False, "clientRequestId": "nocsrf-1",
    }).status_code == 403

    # Mutually exclusive / explicit-null ageBand vs continuation fields -> 422.
    for bad in (
        {"ageBand": None, "topic": "互助", "characters": ["小兔"], "scene": "森林", "branching": False, "clientRequestId": "b1"},
        {"ageBand": "AGE_4_5", "sourceReadingSessionId": 1, "expectedRevision": 1, "topic": "互助", "characters": ["小兔"], "scene": "森林", "branching": False, "clientRequestId": "b2"},
        {"topic": "互助", "characters": ["小兔"], "scene": "森林", "branching": False, "clientRequestId": "b3"},
    ):
        assert client.post("/api/v1/story-generations", headers=headers, json=bad).status_code == 422

    # Sensitive input fails with 422 before calling the model, and same-key mismatch returns IDEMPOTENCY_CONFLICT.
    sensitive_req = {"ageBand": "AGE_4_5", "topic": "互助", "characters": ["小兔"], "scene": "询问家庭地址",
                     "branching": False, "clientRequestId": "sens-1"}
    assert client.post("/api/v1/story-generations", headers=headers, json=sensitive_req).status_code == 422
    assert client.post("/api/v1/story-generations", headers=headers, json=sensitive_req).status_code == 422
    assert client.post("/api/v1/story-generations", headers=headers,
                       json={**sensitive_req, "scene": "森林"}).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    # LIBRARY session cannot be continued (404).
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("INSERT INTO storybooks VALUES (299, NULL, 'LIBRARY', 1)")
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             license_note, status, reviewed_by_id, reviewed_at, created_at, branching)
            VALUES (399, 299, 1, '馆藏绘本', '["互助"]', 3, 5, '馆藏授权', 'PUBLISHED', 1, 100, 1, 0)""")
        for pno, nxt in ((1, 2), (2, 3), (3, 4), (4, None)):
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (399, ?, '馆藏页', 'none', ?)",
                       (pno, nxt))
    lib_sid = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": 399, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    assert client.post(f"/api/v1/reading-sessions/{lib_sid}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "lib-s1",
    }).status_code == 201
    assert client.post("/api/v1/story-generations", headers=headers, json={
        "sourceReadingSessionId": lib_sid, "expectedRevision": 1, "topic": "互助",
        "characters": ["小兔"], "scene": "森林", "branching": False, "clientRequestId": "lib-cont",
    }).status_code == 404

    # Advance an ORIGINAL session to 3 pages -> branching=True succeeds with 6 total pages (decision=4 -> 5, 6).
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_pages SET default_next_page_number = 5 WHERE version_id = 301 AND page_no = 4")
        db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (301, 5, 'v1第5页', 'none', 6)")
        db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (301, 6, 'v1第6页', 'none', NULL)")
    sid = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": 301, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    for page, event in ((1, "STARTED"), (2, "PAGE_CHANGED"), (3, "PAGE_CHANGED")):
        assert client.post(f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
            "eventType": event, "pageNumber": page, "expectedRevision": page - 1, "clientEventId": f"p-{page}",
        }).status_code == 201
    assert len(calls) == 0

    ok_branch = client.post("/api/v1/story-generations", headers=headers, json={
        "sourceReadingSessionId": sid, "expectedRevision": 3, "topic": "互助",
        "characters": ["小兔"], "scene": "森林", "branching": True, "clientRequestId": "branch-at-3",
    })
    assert ok_branch.status_code == 201, ok_branch.text
    assert [(b["fromPageNumber"], b["nextPageNumber"]) for b in ok_branch.json()["data"]["branches"]] == [(4, 5), (4, 6)]
    assert len(calls) == 1

    # Advance to page 4 -> branching=True hits 422 CONTINUATION_LIMIT without calling the model.
    assert client.post(f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 4, "expectedRevision": 3, "clientEventId": "p-4",
    }).status_code == 201
    limit_branch = client.post("/api/v1/story-generations", headers=headers, json={
        "sourceReadingSessionId": sid, "expectedRevision": 4, "topic": "互助",
        "characters": ["小兔"], "scene": "森林", "branching": True, "clientRequestId": "limit-branch-4",
    })
    assert limit_branch.status_code == 422 and limit_branch.json()["error"]["code"] == "CONTINUATION_LIMIT"
    assert len(calls) == 1

    # Advance to page 6 -> branching=False also hits 422 CONTINUATION_LIMIT.
    for page in (5, 6):
        assert client.post(f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
            "eventType": "PAGE_CHANGED", "pageNumber": page, "expectedRevision": page - 1, "clientEventId": f"p-{page}",
        }).status_code == 201
    limit_linear = client.post("/api/v1/story-generations", headers=headers, json={
        "sourceReadingSessionId": sid, "expectedRevision": 6, "topic": "互助",
        "characters": ["小兔"], "scene": "森林", "branching": False, "clientRequestId": "limit-linear-6",
    })
    assert limit_linear.status_code == 422 and limit_linear.json()["error"]["code"] == "CONTINUATION_LIMIT"
    assert len(calls) == 1

    # The same family's child device cannot preview the guardian's pending draft.
    preview_url = f"/api/v1/media/{ok_branch.json()['data']['pages'][-1]['imageRef']}"
    assert client.get(preview_url).status_code == 200
    with TestClient(app, base_url="https://testserver") as child_client:
        creds = {"account": "guardian-one", "password": "strong-pass-123"}
        assert child_client.post("/api/v1/auth/sessions", json=creds).status_code == 201
        ch = {"X-CSRF-Token": child_client.cookies["zhiya_csrf"]}
        csid = child_client.post("/api/v1/reading-sessions", headers=ch, json={
            "childProfileId": profile, "storyVersionId": 399, "mode": "PARENT_CHILD",
        }).json()["data"]["readingSessionId"]
        assert child_client.put(f"/api/v1/reading-sessions/{csid}/mode-settings", headers=ch, json={
            "mode": "CHILD_SOLO", "allowedStoryVersionIds": [399], "timeLimitSeconds": 300,
        }).status_code == 200
        assert child_client.get(preview_url).status_code == 404
        child_csrf = {"X-CSRF-Token": child_client.cookies["zhiya_csrf"]}
        forbidden = child_client.post("/api/v1/story-generations", headers=child_csrf, json={
            "ageBand": "AGE_4_5", "topic": "互助", "characters": ["小兔"], "scene": "森林",
            "branching": False, "clientRequestId": "child-attempt",
        })
        assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "FORBIDDEN"


def test_story_generation_execution_lifecycle_fenced_json_and_race_guards(cont_env, monkeypatch, tmp_path):
    from PIL import Image

    client, _, db_path, profile = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    media_root = tmp_path / "media"
    media_root.mkdir()
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(media_root))
    cover = media_root / "branch3.png"
    Image.new("RGB", (4, 4), "blue").save(cover)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET branching = 1 WHERE id = 301")
        db.execute("INSERT INTO story_branches VALUES (801, 301, 1, 3, '走近路', 'APPROVED')")
        db.execute("UPDATE story_pages SET image_ref = 'branch3.png' WHERE version_id = 301 AND page_no = 3")

    sid = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": 301, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    assert client.post(f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "r1",
    }).status_code == 201
    assert client.post(f"/api/v1/reading-sessions/{sid}/branches", headers=headers, json={
        "branchId": 801, "expectedRevision": 1, "clientEventId": "r2",
    }).status_code == 201

    mode = {"value": "timeout"}
    calls = 0

    class ScriptedModel:
        async def generate(self, request):
            nonlocal calls
            calls += 1
            if mode["value"] == "timeout":
                return ModelGenerateResult(success=False, error_code="TIMEOUT")
            if mode["value"] == "api_error":
                return ModelGenerateResult(success=False, error_code="API_ERROR")
            if mode["value"] == "expire_during_call":
                with closing(sqlite3.connect(db_path)) as db, db:
                    db.execute(
                        "UPDATE request_executions SET started_at = 1, deadline_at = 2 WHERE client_request_id = 'exp-1'",
                    )
                return ModelGenerateResult(success=True, text=json.dumps({
                    "title": "超时落地", "pages": ["新第3页", "新第4页"],
                }))
            if mode["value"] == "advance_during_call":
                with closing(sqlite3.connect(db_path)) as db, db:
                    db.execute(
                        "UPDATE reading_sessions SET current_page_no = 4, revision = 3, visited_pages_json = '[1,3,4]' WHERE id = ?",
                        (sid,),
                    )
                return ModelGenerateResult(success=True, text=json.dumps({
                    "title": "中途过期的续写", "pages": ["新第3页", "新第4页"],
                }))
            return ModelGenerateResult(
                success=True,
                text='```json\n{"title": "小路新发现", "pages": ["来到小木桥。", "一起过小桥。"]}\n```',
            )

    monkeypatch.setattr(client.app.state, "model_adapter", ScriptedModel())
    base_req = {
        "sourceReadingSessionId": sid, "expectedRevision": 2, "topic": "互助",
        "characters": ["小狐狸"], "scene": "森林", "branching": False,
    }

    # 1. Timeout records UNKNOWN and replays 504 without calling the model again.
    timed_out = client.post("/api/v1/story-generations", headers=headers, json={**base_req, "clientRequestId": "to-1"})
    assert timed_out.status_code == 504 and timed_out.json()["error"]["code"] == "REQUEST_OUTCOME_UNKNOWN"
    assert client.post("/api/v1/story-generations", headers=headers, json={**base_req, "clientRequestId": "to-1"}).status_code == 504
    assert calls == 1

    # 2. API_ERROR records FAILED 503 and replays 503 without calling the model again.
    mode["value"] = "api_error"
    dep_err = client.post("/api/v1/story-generations", headers=headers, json={**base_req, "clientRequestId": "err-1"})
    assert dep_err.status_code == 503 and dep_err.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert client.post("/api/v1/story-generations", headers=headers, json={**base_req, "clientRequestId": "err-1"}).status_code == 503
    assert calls == 2

    # 3. Missing media root fails closed before calling model (retryable=True) and recovers once restored.
    offline_root = tmp_path / "media-offline"
    media_root.rename(offline_root)
    media_down = client.post("/api/v1/story-generations", headers=headers, json={**base_req, "clientRequestId": "fenced-1"})
    assert media_down.status_code == 503 and media_down.json()["error"]["retryable"] is True
    assert calls == 2
    offline_root.rename(media_root)

    # 4. Markdown-fenced JSON succeeds on retry with the same key and preserves branched carried media.
    mode["value"] = "fenced"
    ok = client.post("/api/v1/story-generations", headers=headers, json={**base_req, "clientRequestId": "fenced-1"})
    assert ok.status_code == 201, ok.text
    assert calls == 3
    odata = ok.json()["data"]
    assert odata["continuation"]["sourceVisitedPageNumbers"] == [1, 3]
    assert odata["continuation"]["resumePageNumber"] == 3
    assert odata["pages"][1]["imageRef"] == "asset-301-page-3"

    # 5. Exceeding the execution deadline during model generation marks the execution UNKNOWN (504).
    mode["value"] = "expire_during_call"
    expired = client.post("/api/v1/story-generations", headers=headers, json={**base_req, "clientRequestId": "exp-1"})
    assert expired.status_code == 504 and expired.json()["error"]["code"] == "REQUEST_OUTCOME_UNKNOWN"
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute(
            "SELECT status FROM request_executions WHERE client_request_id = 'exp-1'",
        ).fetchone()[0] == "UNKNOWN"

    # 6. If source advances while the model is running, finish_generation returns 409 CONTINUATION_STALE without saving a draft.
    mode["value"] = "advance_during_call"
    raced = client.post("/api/v1/story-generations", headers=headers, json={**base_req, "clientRequestId": "race-1"})
    assert raced.status_code == 409 and raced.json()["error"]["code"] == "CONTINUATION_STALE"
    assert client.post("/api/v1/story-generations", headers=headers, json={**base_req, "clientRequestId": "race-1"}).status_code == 409
    assert client.post("/api/v1/story-generations", headers=headers,
                       json={**base_req, "expectedRevision": 3, "clientRequestId": "race-1"}).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    with closing(sqlite3.connect(db_path)) as db:
        # Only version 301 and the valid draft from step 4 exist.
        assert db.execute("SELECT COUNT(*) FROM story_versions").fetchone()[0] == 2

    # 7. Deleting source session cleans up associated STORY_GENERATION request_executions rows.
    assert client.delete(f"/api/v1/reading-sessions/{sid}", headers=headers).status_code == 204
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM request_executions WHERE scope_type = 'STORY_GENERATION'").fetchone()[0] == 0


def test_draft_reaching_deadline_during_save_is_rolled_back(cont_env, monkeypatch):
    from types import SimpleNamespace
    from app.api.v1 import story_generations

    client, _, db_path, _ = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    payload = {"ageBand": "AGE_4_5", "topic": "互助", "characters": ["小兔"], "scene": "森林",
               "branching": False, "clientRequestId": "slow-draft"}
    calls = 0
    now = 1000

    class StoryModel:
        async def generate(self, request):
            nonlocal calls
            calls += 1
            return ModelGenerateResult(success=True, text=json.dumps({
                "title": "森林散步", "pages": ["小兔出发了。", "小兔走进森林。", "朋友来帮忙。", "大家回家了。"],
            }))

    save_draft = story_generations.save_draft

    def slow_save(*args):
        nonlocal now
        response = save_draft(*args)
        assert response.status_code == 201
        now += 65
        return response

    monkeypatch.setattr(client.app.state, "model_adapter", StoryModel())
    monkeypatch.setattr(story_generations, "time", SimpleNamespace(time=lambda: now))
    monkeypatch.setattr(story_generations, "save_draft", slow_save)
    for _ in range(2):
        response = client.post("/api/v1/story-generations", headers=headers, json=payload)
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "REQUEST_OUTCOME_UNKNOWN"
    assert calls == 1
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM storybooks").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM story_versions").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM story_pages").fetchone() == (4,)
        assert db.execute(
            "SELECT status, completed_at = deadline_at FROM request_executions WHERE client_request_id = 'slow-draft'"
        ).fetchone() == ("UNKNOWN", 1)


def test_story_generation_gateway_60s_budget_and_template_illustrations_4_pages(cont_env, monkeypatch):
    import asyncio
    import httpx
    from app.adapters.model import ModelAdapter
    from app.config import Settings
    from app.errors import ServiceError
    from app.model_gateway import ModelGateway
    from app.schemas.agent import ModelGenerateRequest

    client, _, db_path, _ = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    provider_calls = 0
    transport_budgets = []

    async def slow_handler(request):
        nonlocal provider_calls
        provider_calls += 1
        transport_budgets.append(set(request.extensions["timeout"].values()))
        await asyncio.sleep(0.08)
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "title": "小熊去散步",
                        "pages": ["第1页故事。", "第2页故事。", "第3页故事。", "第4页故事。"],
                    }, ensure_ascii=False),
                },
            }],
        })

    settings = Settings(
        model_api_url="https://model.example.test/v1/chat/completions",
        model_api_key="fake-key",
        model_name="test-model",
        model_timeout_seconds=0.04,
    )
    gateway = ModelGateway(settings, transport=httpx.MockTransport(slow_handler))
    adapter = ModelAdapter(gateway)
    monkeypatch.setattr(client.app.state, "model_adapter", adapter)
    try:
        # Normal text request keeps the shorter gateway timeout (0.04s < 0.08s -> TIMEOUT).
        normal_res = client.portal.call(
            adapter.generate,
            ModelGenerateRequest(system_prompt="规则", context_text="上下文", user_input="正常问题"),
        )
        assert normal_res.success is False and normal_res.error_code == "TIMEOUT"
        assert provider_calls == 1

        # Invalid explicit timeout_seconds is rejected without calling provider.
        try:
            client.portal.call(
                lambda: gateway.generate(input_text="hi", system_prompt="sys", timeout_seconds=61.0),
            )
            raise AssertionError("Expected ServiceError for timeout > 60")
        except ServiceError as exc:
            assert exc.code == "INVALID_INPUT"

        # API-025 uses the 60s budget so the 0.08s delayed response succeeds and assigns licensed templates.
        resp = client.post("/api/v1/story-generations", headers=headers, json={
            "ageBand": "AGE_3_4", "topic": "勇气", "characters": ["小熊"], "scene": "树林",
            "branching": False, "clientRequestId": "budget-60s-4pages",
        })
        assert resp.status_code == 201, resp.text
        assert provider_calls == 2
        assert transport_budgets == [{0.04}, {60.0}]
        data = resp.json()["data"]
        vid = data["storyVersionId"]
        assert data["checkResults"]["licensePassed"] is True
        assert [p["imageRef"] for p in data["pages"]] == [f"asset-{vid}-page-{n}" for n in range(1, 5)]

        with closing(sqlite3.connect(db_path)) as db:
            rows = db.execute(
                "SELECT image_ref, image_license_note FROM story_pages WHERE version_id = ? ORDER BY page_no",
                (vid,),
            ).fetchall()
            assert len(rows) == 4
            assert all(ref != "none" and note == "NO-COMMERCIAL" for ref, note in rows)

        approved = client.post(f"/api/v1/story-versions/{vid}/approvals", headers=headers,
                               json={"versionNumber": 1, "reviewedAllBranches": True})
        assert approved.status_code == 200, approved.text
        detail = client.get(f"/api/v1/story-versions/{vid}")
        assert detail.status_code == 200
        for p in detail.json()["data"]["pages"]:
            img = client.get(f"/api/v1/media/{p['imageRef']}")
            assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    finally:
        client.portal.call(gateway.__aexit__)


@pytest.mark.parametrize("catalog_error", ["unlicensed", "invalid_version_id"])
def test_original_generation_rejects_invalid_catalog_before_model_call(cont_env, monkeypatch, tmp_path, catalog_error):
    from unittest.mock import AsyncMock
    from app.services import story_media

    client, _, db_path, _ = cont_env
    source_pages = json.loads(story_media.DEFAULT_TEMPLATE_PAGES.read_text(encoding="utf-8"))
    if catalog_error == "invalid_version_id":
        source_pages[0]["version_id"] = "invalid"
    else:
        for page in source_pages:
            if page.get("image_license_note") == "NO-COMMERCIAL":
                page["image_license_note"] = "禁止使用"
    catalog = tmp_path / "template-pages.json"
    catalog.write_text(json.dumps(source_pages), encoding="utf-8")
    monkeypatch.setattr(story_media, "DEFAULT_TEMPLATE_PAGES", catalog)
    model = AsyncMock()
    monkeypatch.setattr(client.app.state, "model_adapter", model)

    response = client.post("/api/v1/story-generations", headers={"X-CSRF-Token": client.cookies["zhiya_csrf"]},
                           json={"ageBand": "AGE_3_4", "topic": "互助", "characters": ["小兔"], "scene": "森林",
                                 "branching": False, "clientRequestId": "restricted-template"})
    assert response.status_code == 503 and response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is True
    model.generate.assert_not_awaited()
    with closing(sqlite3.connect(db_path)) as db:
        assert not db.execute("SELECT 1 FROM request_executions WHERE client_request_id = 'restricted-template'").fetchone()
        assert db.execute("SELECT COUNT(*) FROM story_versions").fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["original-pin-store", "continuation-templates"])
def test_required_new_images_recover_with_same_generation_key(cont_env, monkeypatch, tmp_path, failure):
    from unittest.mock import AsyncMock
    from app.services import story_media

    client, _, db_path, profile = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    payload = {"topic": "互助", "characters": ["小兔"], "scene": "森林", "branching": False,
               "clientRequestId": "recover-new-images"}
    monkeypatch.delenv("ZHIYA_MEDIA_ROOT", raising=False)
    if failure == "continuation-templates":
        session = client.post("/api/v1/reading-sessions", headers=headers, json={
            "childProfileId": profile, "storyVersionId": 301, "mode": "PARENT_CHILD",
        }).json()["data"]["readingSessionId"]
        assert client.post(f"/api/v1/reading-sessions/{session}/events", headers=headers, json={
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "read-1",
        }).status_code == 201
        payload.update(sourceReadingSessionId=session, expectedRevision=1)
    else:
        payload["ageBand"] = "AGE_4_5"

    async def generate(request):
        # The source templates go offline during generation. The draft must reuse its pinned images.
        monkeypatch.setattr(story_media, "DEFAULT_MEDIA_ROOT", tmp_path)
        count = json.loads(request.context_text)["newPageCount"]
        return ModelGenerateResult(success=True, text=json.dumps({"title": "森林", "pages": ["小兔向前走。"] * count}))

    model = AsyncMock()
    model.generate.side_effect = generate
    monkeypatch.setattr(client.app.state, "model_adapter", model)
    url = "/api/v1/story-generations"
    with monkeypatch.context() as offline:
        if failure == "continuation-templates":
            offline.setattr(story_media, "DEFAULT_MEDIA_ROOT", tmp_path)
        else:
            blobs = db_path.parent / f".{db_path.name}.media_pins" / "blobs"
            blobs.parent.mkdir(exist_ok=True)
            blobs.touch()
        unavailable = client.post(url, headers=headers, json=payload)
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["retryable"] is True
        model.generate.assert_not_awaited()
    if failure == "original-pin-store":
        blobs.unlink()

    recovered = client.post(url, headers=headers, json=payload)
    assert recovered.status_code == 201, recovered.text
    data = recovered.json()["data"]
    assert client.get(f"/api/v1/media/{data['pages'][-1]['imageRef']}").status_code == 200
    replay = client.post(url, headers=headers, json=payload)
    assert replay.status_code == 200 and replay.json()["data"] == data
    model.generate.assert_awaited_once()


def test_continuation_preflight_carried_blob_missing_and_save_retry_once(cont_env, monkeypatch, tmp_path):
    from PIL import Image
    import app.api.v1.story_generations as sg
    from app.services.story_media import MediaDependencyError

    client, _, db_path, profile = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    media_root = tmp_path / "media"
    media_root.mkdir()
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(media_root))
    (media_root / "p1.png").write_bytes(b"")
    Image.new("RGB", (4, 4), "green").save(media_root / "p1.png")
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_pages SET image_ref = 'p1.png' WHERE version_id = 301 AND page_no = 1")

    # Pin version 301 so its manifest and blob exist on disk.
    assert client.get("/api/v1/media/asset-301-page-1").status_code == 200
    sid = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile, "storyVersionId": 301, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    for page, ev in ((1, "STARTED"), (2, "PAGE_CHANGED")):
        assert client.post(f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
            "eventType": ev, "pageNumber": page, "expectedRevision": page - 1, "clientEventId": f"c-{page}",
        }).status_code == 201

    model_calls = 0

    class StoryModel:
        async def generate(self, request):
            nonlocal model_calls
            model_calls += 1
            return ModelGenerateResult(success=True, text=json.dumps({
                "title": "重试续写", "pages": ["新第3页。", "新第4页。"],
            }))

    monkeypatch.setattr(client.app.state, "model_adapter", StoryModel())
    req = {
        "sourceReadingSessionId": sid, "expectedRevision": 2, "topic": "互助",
        "characters": ["小狐狸"], "scene": "森林", "branching": False, "clientRequestId": "blob-check-1",
    }

    # Remove ONLY the specific carried page blob while manifest remains intact.
    blobs_dir = db_path.parent / f".{db_path.name}.media_pins" / "blobs"
    blob_file = next(blobs_dir.iterdir())
    backup_blob = tmp_path / blob_file.name
    blob_file.rename(backup_blob)

    pre_fail = client.post("/api/v1/story-generations", headers=headers, json=req)
    assert pre_fail.status_code == 503 and pre_fail.json()["error"]["retryable"] is True
    assert model_calls == 0

    # Restore the blob and retry with the same clientRequestId -> succeeds with 1 model call.
    blob_file.write_bytes(b"unreadable image bytes")
    corrupt = client.post("/api/v1/story-generations", headers=headers, json=req)
    assert corrupt.status_code == 503 and corrupt.json()["error"]["retryable"] is True
    assert model_calls == 0
    backup_blob.replace(blob_file)
    pre_ok = client.post("/api/v1/story-generations", headers=headers, json=req)
    assert pre_ok.status_code == 201, pre_ok.text
    assert model_calls == 1

    # Transient MediaDependencyError during save_draft retries once in memory without a second model call.
    real_ensure = sg.ensure_version_pages_media
    transient_attempts = 0

    def flaky_save_ensure(db, version_row, page_numbers):
        nonlocal transient_attempts
        # Reserve calls ensure_version_pages_media once; save_draft calls it a second time.
        transient_attempts += 1
        if transient_attempts == 2:
            raise MediaDependencyError("transient storage glitch")
        return real_ensure(db, version_row, page_numbers)

    monkeypatch.setattr(sg, "ensure_version_pages_media", flaky_save_ensure)
    transient_res = client.post("/api/v1/story-generations", headers=headers,
                                json={**req, "clientRequestId": "save-transient-1"})
    assert transient_res.status_code == 201, transient_res.text
    assert model_calls == 2
    assert transient_attempts == 3
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM story_versions").fetchone()[0] == 3

    # Persistent MediaDependencyError during save_draft fails after 1 retry and replays 503.
    save_calls = 0

    def persistent_save_ensure(db, version_row, page_numbers):
        nonlocal save_calls
        save_calls += 1
        if save_calls >= 2:
            raise MediaDependencyError("persistent storage outage")
        return real_ensure(db, version_row, page_numbers)

    monkeypatch.setattr(sg, "ensure_version_pages_media", persistent_save_ensure)
    persistent_res = client.post("/api/v1/story-generations", headers=headers,
                                 json={**req, "clientRequestId": "save-persist-1"})
    assert persistent_res.status_code == 503 and persistent_res.json()["error"]["retryable"] is False
    assert model_calls == 3
    assert save_calls == 3
    assert client.post("/api/v1/story-generations", headers=headers,
                       json={**req, "clientRequestId": "save-persist-1"}).status_code == 503
    assert model_calls == 3
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM story_versions").fetchone()[0] == 3



def _seed_continuation_draft(db_path: Path, version_id: int, version_no: int, src_session_id: int,
                             src_revision: int, src_page: int, prefix: list[int], *,
                             status: str = "PENDING_REVIEW", branching: int = 0, src_version_id: int = 301):
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             license_note, status, created_at, source_version_id, source_session_id,
             source_revision, source_page_no, resume_page_no, source_visited_pages_json, branching)
            VALUES (?, 201, ?, '小狐狸续写', '["互助"]', 3, 5, '原创授权', ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, version_no, status, src_version_id, src_session_id, src_revision, src_page,
             len(prefix) + 1, json.dumps(prefix), branching))
        if branching == 0:
            for pno, nxt in ((1, 2), (2, 3), (3, 4), (4, 5), (5, None)):
                db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (?, ?, ?, 'none', ?)",
                           (version_id, pno, f"v{version_no}第{pno}页", nxt))
        else:
            for pno, nxt in ((1, 2), (2, 3), (3, None), (4, 5), (5, None)):
                db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (?, ?, ?, 'none', ?)",
                           (version_id, pno, f"v{version_no}第{pno}页", nxt))
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (?, 6, ?, 'none', NULL)",
                       (version_id, f"v{version_no}第6页"))
            db.execute("INSERT INTO story_branches VALUES (901, ?, 3, 4, '去森林', 'PENDING')", (version_id,))
            db.execute("INSERT INTO story_branches VALUES (902, ?, 3, 6, '回家去', 'PENDING')", (version_id,))
        for carried_page, source_page in enumerate(prefix, start=1):
            db.execute(
                """UPDATE story_pages SET body_text = (
                       SELECT body_text FROM story_pages WHERE version_id = ? AND page_no = ?
                   ) WHERE version_id = ? AND page_no = ?""",
                (src_version_id, source_page, version_id, carried_page),
            )


def test_approve_story_version_retires_prior_verifies_dag_and_rejects_invalid(cont_env):
    first, second, db_path, pid = cont_env
    h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    h2 = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}

    sid = first.post("/api/v1/reading-sessions", headers=h1, json={
        "childProfileId": pid, "storyVersionId": 301, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=h1, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "s1",
    }).status_code == 201
    assert first.post(f"/api/v1/reading-sessions/{sid}/events", headers=h1, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "s2",
    }).status_code == 201

    _seed_continuation_draft(db_path, 302, 2, sid, 2, 2, [1, 2], branching=1)
    # Also add an invalid 3-page draft (303) and a cyclic draft (304) and a WITHDRAWN version (305).
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             license_note, status, created_at, branching)
            VALUES (303, 201, 3, '过短草稿', '["互助"]', 3, 5, 'ok', 'PENDING_REVIEW', 1, 0)""")
        for pno, nxt in ((1, 2), (2, 3), (3, None)):
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (303, ?, 'short', 'none', ?)",
                       (pno, nxt))
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             license_note, status, created_at, branching)
            VALUES (304, 201, 4, '环路草稿', '["互助"]', 3, 5, 'ok', 'PENDING_REVIEW', 1, 0)""")
        for pno, nxt in ((1, 2), (2, 3), (3, 4), (4, 2)):
            db.execute("INSERT INTO story_pages (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (304, ?, 'loop', 'none', ?)",
                       (pno, nxt))
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             license_note, status, created_at, branching)
            VALUES (305, 201, 5, '撤回版', '["互助"]', 3, 5, 'ok', 'WITHDRAWN', 1, 0)""")

    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.post("/api/v1/story-versions/302/approvals", json={"versionNumber": 2, "reviewedAllBranches": True}).status_code == 401
    assert first.post("/api/v1/story-versions/302/approvals", json={"versionNumber": 2, "reviewedAllBranches": True}).status_code == 403
    assert second.post("/api/v1/story-versions/302/approvals", headers=h2, json={"versionNumber": 2, "reviewedAllBranches": True}).status_code == 404
    assert first.post("/api/v1/story-versions/305/approvals", headers=h1, json={"versionNumber": 5, "reviewedAllBranches": True}).status_code == 404
    assert first.post("/api/v1/story-versions/302/approvals", headers=h1, json={"versionNumber": 2, "reviewedAllBranches": False}).status_code == 422
    assert first.post("/api/v1/story-versions/302/approvals", headers=h1, json={"versionNumber": 1, "reviewedAllBranches": True}).status_code == 409
    assert first.post("/api/v1/story-versions/303/approvals", headers=h1, json={"versionNumber": 3, "reviewedAllBranches": True}).status_code == 422
    assert first.post("/api/v1/story-versions/304/approvals", headers=h1, json={"versionNumber": 4, "reviewedAllBranches": True}).status_code == 422

    approved = first.post("/api/v1/story-versions/302/approvals", headers=h1,
                          json={"versionNumber": 2, "reviewedAllBranches": True})
    assert approved.status_code == 200 and approved.json()["requestId"] == approved.headers["X-Request-ID"]
    adata = approved.json()["data"]
    assert adata["status"] == "APPROVED_PRIVATE"
    assert adata["continuation"] == {
        "sourceReadingSessionId": sid,
        "sourceStoryVersionId": 301,
        "sourceRevision": 2,
        "sourcePageNumber": 2,
        "sourceVisitedPageNumbers": [1, 2],
        "resumePageNumber": 3,
    }
    assert all(b["reviewStatus"] == "APPROVED" for b in adata["branches"])

    # Old version 301 was atomically retired keeping reviewed_at=100, and source session stays IN_PROGRESS.
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT status, reviewed_by_id, reviewed_at FROM story_versions WHERE id = 301").fetchone() == ("RETIRED", 1, 100)
        assert db.execute("SELECT status, revision FROM reading_sessions WHERE id = ?", (sid,)).fetchone() == ("IN_PROGRESS", 2)
    assert [v["storyVersionId"] for v in first.get("/api/v1/storybooks", params={"scope": "mine"}).json()["data"]["items"]] == [302, 301]


def test_continuation_metadata_is_omitted_from_granted_child_books(cont_env):
    guardian, _, db_path, profile_id = cont_env
    headers = {"X-CSRF-Token": guardian.cookies["zhiya_csrf"]}
    sessions_url = "/api/v1/reading-sessions"
    session_body = {"childProfileId": profile_id, "mode": "PARENT_CHILD"}
    source = guardian.post(sessions_url, headers=headers, json={**session_body, "storyVersionId": 301})
    assert source.status_code == 201
    source_id = source.json()["data"]["readingSessionId"]
    started = guardian.post(f"{sessions_url}/{source_id}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start-source",
    })
    assert started.status_code == 201
    _seed_continuation_draft(db_path, 302, 2, source_id, 1, 1, [1], branching=1)

    detail_url = "/api/v1/story-versions/302"
    shelf_url = "/api/v1/storybooks"
    approved = guardian.post(f"{detail_url}/approvals", headers=headers, json={
        "versionNumber": 2, "reviewedAllBranches": True,
    })
    shelf = guardian.get(shelf_url, params={"scope": "mine"})
    detail = guardian.get(detail_url)
    assert approved.status_code == shelf.status_code == detail.status_code == 200
    source_detail = guardian.get(f"{sessions_url}/{source_id}").json()["data"]["session"]
    assert source_detail["childProfileId"] == profile_id
    assert source_detail["sourceType"] == "ORIGINAL"
    parent_detail = detail.json()["data"]
    for book in (approved.json()["data"], shelf.json()["data"]["items"][0], parent_detail):
        assert book["storyVersionId"] == 302
        assert book["sourceStoryVersionId"] == 301
        assert book["continuation"] == {
            "sourceReadingSessionId": source_id,
            "sourceStoryVersionId": 301,
            "sourceRevision": 1,
            "sourcePageNumber": 1,
            "sourceVisitedPageNumbers": [1],
            "resumePageNumber": 2,
        }

    session = guardian.post(sessions_url, headers=headers, json={**session_body, "storyVersionId": 302})
    assert session.status_code == 201
    session_id = session.json()["data"]["readingSessionId"]
    grant = guardian.put(f"{sessions_url}/{session_id}/mode-settings", headers=headers, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": [302], "timeLimitSeconds": 600,
    })
    assert grant.status_code == 200
    assert grant.json()["data"]["allowedStoryVersionIds"] == [302]

    child_session = guardian.get(f"{sessions_url}/{session_id}").json()["data"]["session"]
    assert "childProfileId" not in child_session and "sourceType" not in child_session
    child_shelf = guardian.get(shelf_url)
    child_detail = guardian.get(detail_url)
    assert child_shelf.status_code == child_detail.status_code == 200
    [shelf_book] = child_shelf.json()["data"]["items"]
    for book in (shelf_book, child_detail.json()["data"]):
        assert book["storyVersionId"] == 302
        assert "continuation" not in book
        assert "sourceStoryVersionId" not in book
        assert book["pages"] == parent_detail["pages"]
        assert [page["text"] for page in book["pages"][:2]] == ["v1第1页", "v2第2页"]
        assert book["branches"] == parent_detail["branches"]
        assert [branch["reviewStatus"] for branch in book["branches"]] == ["APPROVED", "APPROVED"]


@pytest.mark.parametrize("continuation", [
    pytest.param({"sourceReadingSessionId": None}, id="one-null"),
    pytest.param({"sourceReadingSessionId": None, "expectedRevision": None, "clientRequestId": None}, id="all-null"),
    pytest.param({"sourceReadingSessionId": 1, "expectedRevision": None, "clientRequestId": "switch"}, id="mixed"),
])
def test_create_reading_session_rejects_explicit_null_continuation_fields(cont_env, continuation):
    guardian, _, db_path, profile_id = cont_env
    headers = {"X-CSRF-Token": guardian.cookies["zhiya_csrf"]}
    with closing(sqlite3.connect(db_path)) as db:
        before = db.execute("SELECT COUNT(*) FROM reading_sessions").fetchone()
        response = guardian.post("/api/v1/reading-sessions", headers=headers, json={
            "childProfileId": profile_id, "storyVersionId": 301, "mode": "PARENT_CHILD", **continuation,
        })
        assert response.status_code == 422
        assert db.execute("SELECT COUNT(*) FROM reading_sessions").fetchone() == before


def test_continuation_switch_tc_fr17_04_and_idempotency(cont_env):
    first, second, db_path, pid = cont_env
    h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    h2 = {"X-CSRF-Token": second.cookies["zhiya_csrf"]}

    sid1 = first.post("/api/v1/reading-sessions", headers=h1, json={
        "childProfileId": pid, "storyVersionId": 301, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    assert first.post(f"/api/v1/reading-sessions/{sid1}/events", headers=h1, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "e1",
    }).status_code == 201
    assert first.post(f"/api/v1/reading-sessions/{sid1}/events", headers=h1, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "e2",
    }).status_code == 201

    _seed_continuation_draft(db_path, 302, 2, sid1, 2, 2, [1, 2], branching=1)
    assert first.post("/api/v1/story-versions/302/approvals", headers=h1,
                      json={"versionNumber": 2, "reviewedAllBranches": True}).status_code == 200

    switch_body = {
        "childProfileId": pid,
        "storyVersionId": 302,
        "mode": "PARENT_CHILD",
        "sourceReadingSessionId": sid1,
        "expectedRevision": 2,
        "clientRequestId": "switch-001",
    }
    assert second.post("/api/v1/reading-sessions", headers=h2, json=switch_body).status_code == 404

    simultaneous = Barrier(2, timeout=5)

    def switch():
        simultaneous.wait()
        return first.post("/api/v1/reading-sessions", headers=h1, json=switch_body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(switch) for _ in range(2)]
        replayed, switched = sorted(
            (pending.result() for pending in requests), key=lambda response: response.status_code,
        )
    assert switched.status_code == 201 and switched.json()["requestId"] == switched.headers["X-Request-ID"]
    sdata = switched.json()["data"]
    sid2 = sdata["readingSessionId"]
    assert sid2 != sid1
    assert sdata == {
        "readingSessionId": sid2,
        "storyVersionId": 302,
        "sourceReadingSessionId": sid1,
        "startPageNumber": 3,
        "carriedPrefixPageCount": 2,
        "currentPage": 3,
        "visitedPageNumbers": [1, 2, 3],
        "mode": "PARENT_CHILD",
        "status": "IN_PROGRESS",
        "revision": 1,
        "expiresAt": None,
    }

    # Old session is FINISHED(CONTINUED) with fact report, and new session can choose branch at page 3.
    old_view = first.get(f"/api/v1/reading-sessions/{sid1}").json()["data"]
    assert old_view["session"]["status"] == "FINISHED"
    assert old_view["report"]["status"] == "READY" and "第 1、2 页" in old_view["report"]["summary"]
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT end_reason FROM reading_sessions WHERE id = ?", (sid1,)).fetchone()[0] == "CONTINUED"

    # Concurrent same-key replay returns 200 for the original successor.
    assert replayed.status_code == 200 and replayed.json()["data"] == sdata
    assert replayed.json()["requestId"] == replayed.headers["X-Request-ID"] != switched.json()["requestId"]
    with closing(sqlite3.connect(db_path)) as db:
        successors = db.execute(
            "SELECT id FROM reading_sessions WHERE source_reading_session_id = ?", (sid1,),
        ).fetchall()
        reports = db.execute("SELECT session_id FROM reading_reports WHERE session_id = ?", (sid1,)).fetchall()
        starts = db.execute(
            "SELECT id FROM interaction_events WHERE session_id = ? AND event_type = 'STARTED'", (sid2,),
        ).fetchall()
        assert successors == [(sid2,)]
        assert reports == [(sid1,)]
        assert len(starts) == 1
        assert db.execute("SELECT COUNT(*) FROM request_executions WHERE scope_type = 'SESSION_CREATE'").fetchone() == (1,)

    # A changed body conflicts with the original key. A new key reports the completed switch.
    assert first.post("/api/v1/reading-sessions", headers=h1,
                      json={**switch_body, "expectedRevision": 3}).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    repeat_switch_body = {**switch_body, "clientRequestId": "switch-002"}
    already_switched = first.post("/api/v1/reading-sessions", headers=h1, json=repeat_switch_body)
    assert already_switched.status_code == 409
    assert already_switched.json()["error"]["code"] == "CONTINUATION_ALREADY_SWITCHED"
    replayed_failure = first.post("/api/v1/reading-sessions", headers=h1, json=repeat_switch_body)
    assert replayed_failure.status_code == 409
    assert replayed_failure.json()["error"] == already_switched.json()["error"]

    chosen = first.post(f"/api/v1/reading-sessions/{sid2}/branches", headers=h1, json={
        "branchId": 901, "expectedRevision": 1, "clientEventId": "b-cont",
    })
    assert chosen.status_code == 201 and chosen.json()["data"]["currentPage"] == 4
    assert chosen.json()["data"]["visitedPageNumbers"] == [1, 2, 3, 4]

    other_failure = first.post("/api/v1/reading-sessions", headers=h1, json={
        **switch_body, "sourceReadingSessionId": sid2, "clientRequestId": "switch-003",
    })
    assert other_failure.status_code == 409
    assert other_failure.json()["error"]["code"] == "SESSION_CONFLICT"

    # Deleting source session clears its ID and preserves successor recovery metadata.
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute(
            "SELECT status FROM request_executions WHERE scope_type = 'SESSION_CREATE' AND client_request_id = ?",
            ("switch-002",),
        ).fetchone() == ("FAILED",)
    assert first.delete(f"/api/v1/reading-sessions/{sid1}", headers=h1).status_code == 204
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute(
            "SELECT status FROM request_executions WHERE scope_type = 'SESSION_CREATE' AND client_request_id = ?",
            ("switch-002",),
        ).fetchone() is None
        assert db.execute(
            "SELECT status FROM request_executions WHERE scope_type = 'SESSION_CREATE' AND client_request_id = ?",
            ("switch-003",),
        ).fetchone() == ("FAILED",)
    restored = first.get(f"/api/v1/reading-sessions/{sid2}").json()["data"]["session"]
    assert "sourceReadingSessionId" not in restored
    assert restored["storyVersionId"] == 302
    assert restored["currentPage"] == 4
    assert restored["startPageNumber"] == 3
    assert restored["carriedPrefixPageCount"] == 2


def test_continuation_switch_stale_snapshot_and_atomic_rollback_tc_fr17_05(cont_env):
    first, _, db_path, pid = cont_env
    h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}

    sid1 = first.post("/api/v1/reading-sessions", headers=h1, json={
        "childProfileId": pid, "storyVersionId": 301, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    assert first.post(f"/api/v1/reading-sessions/{sid1}/events", headers=h1, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "e1",
    }).status_code == 201
    assert first.post(f"/api/v1/reading-sessions/{sid1}/events", headers=h1, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "e2",
    }).status_code == 201

    _seed_continuation_draft(db_path, 302, 2, sid1, 2, 2, [1, 2], branching=0)
    assert first.post("/api/v1/story-versions/302/approvals", headers=h1,
                      json={"versionNumber": 2, "reviewedAllBranches": True}).status_code == 200

    # Simulate DB write failure mid-switch: entire transaction rolls back so source session stays intact.
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("""CREATE TRIGGER fail_switch_insert BEFORE INSERT ON reading_sessions
            WHEN NEW.source_reading_session_id IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'simulated db write failure'); END;""")
    switch_body = {
        "childProfileId": pid,
        "storyVersionId": 302,
        "mode": "PARENT_CHILD",
        "sourceReadingSessionId": sid1,
        "expectedRevision": 2,
        "clientRequestId": "switch-fail",
    }
    failed = first.post("/api/v1/reading-sessions", headers=h1, json=switch_body)
    assert failed.status_code == 503 and failed.json()["error"]["retryable"] is False
    with closing(sqlite3.connect(db_path)) as db, db:
        assert db.execute("SELECT status, end_reason, revision FROM reading_sessions WHERE id = ?", (sid1,)).fetchone() == (
            "IN_PROGRESS", None, 2,
        )
        assert db.execute("SELECT COUNT(*) FROM reading_sessions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM request_executions WHERE scope_type = 'SESSION_CREATE'").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_switch_insert")

    # Advance source session to page 3 (revision=3): stale snapshot at revision=2 now returns 409 without closing source.
    assert first.post(f"/api/v1/reading-sessions/{sid1}/events", headers=h1, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 3, "expectedRevision": 2, "clientEventId": "e3",
    }).status_code == 201
    stale = first.post("/api/v1/reading-sessions", headers=h1, json=switch_body)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "CONTINUATION_STALE"
    assert first.get(f"/api/v1/reading-sessions/{sid1}").json()["data"]["session"]["status"] == "IN_PROGRESS"
    # Source session can still continue to page 4 normally.
    assert first.post(f"/api/v1/reading-sessions/{sid1}/events", headers=h1, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 4, "expectedRevision": 3, "clientEventId": "e4",
    }).status_code == 201


def test_continuation_switch_from_branched_source_renumbers_carried_prefix(cont_env):
    first, _, db_path, pid = cont_env
    h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("UPDATE story_versions SET branching = 1 WHERE id = 301")
        db.execute("INSERT INTO story_branches VALUES (801, 301, 1, 3, '走近路', 'APPROVED')")
    sid1 = first.post("/api/v1/reading-sessions", headers=h1, json={
        "childProfileId": pid, "storyVersionId": 301, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    assert first.post(f"/api/v1/reading-sessions/{sid1}/events", headers=h1, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "b1",
    }).status_code == 201
    assert first.post(f"/api/v1/reading-sessions/{sid1}/branches", headers=h1, json={
        "branchId": 801, "expectedRevision": 1, "clientEventId": "b2",
    }).status_code == 201
    # Source visited [1, 3]; continuation maps carried prefix to 1..2 and resumes at page 3.
    _seed_continuation_draft(db_path, 306, 2, sid1, 2, 3, [1, 3], branching=0)
    approval_path = "/api/v1/story-versions/306/approvals"
    approval_body = {"versionNumber": 2, "reviewedAllBranches": True}
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_pages SET body_text = 'v1第2页' WHERE version_id = 306 AND page_no = 2")

    rejected = first.post(approval_path, headers=h1, json=approval_body)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    with closing(sqlite3.connect(db_path)) as db, db:
        assert db.execute("SELECT status FROM story_versions WHERE id = 301").fetchone() == ("APPROVED_PRIVATE",)
        assert db.execute("SELECT status FROM story_versions WHERE id = 306").fetchone() == ("PENDING_REVIEW",)
        db.execute("UPDATE story_pages SET body_text = 'v1第3页' WHERE version_id = 306 AND page_no = 2")

    approved = first.post(approval_path, headers=h1, json=approval_body)
    assert approved.status_code == 200
    assert [p["text"] for p in approved.json()["data"]["pages"][:2]] == ["v1第1页", "v1第3页"]
    switched = first.post("/api/v1/reading-sessions", headers=h1, json={
        "childProfileId": pid, "storyVersionId": 306, "mode": "PARENT_CHILD",
        "sourceReadingSessionId": sid1, "expectedRevision": 2, "clientRequestId": "switch-branched",
    })
    assert switched.status_code == 201
    data = switched.json()["data"]
    assert data["visitedPageNumbers"] == [1, 2, 3]
    assert data["carriedPrefixPageCount"] == 2 and data["startPageNumber"] == 3
    old_report = first.get(f"/api/v1/reading-sessions/{sid1}").json()["data"]["report"]
    assert "第 1、3 页" in old_report["summary"]


@pytest.mark.parametrize("ending", ["finish", "switch"])
def test_continuation_first_page_event_reuses_creation_key_and_report_counts_new_pages(cont_env, monkeypatch, ending):
    first, _, db_path, pid = cont_env
    monkeypatch.setattr(app.state, "model_adapter", None, raising=False)
    headers = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    sessions = "/api/v1/reading-sessions"
    created = first.post(sessions, headers=headers, json={
        "childProfileId": pid, "storyVersionId": 301, "mode": "PARENT_CHILD",
    })
    assert created.status_code == 201
    source_id = created.json()["data"]["readingSessionId"]
    source = f"{sessions}/{source_id}"
    assert first.post(f"{source}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start-source",
    }).status_code == 201
    _seed_continuation_draft(db_path, 302, 2, source_id, 1, 1, [1])
    assert first.post("/api/v1/story-versions/302/approvals", headers=headers, json={
        "versionNumber": 2, "reviewedAllBranches": True,
    }).status_code == 200

    request_key = "continuation-request"
    switched = first.post(sessions, headers=headers, json={
        "childProfileId": pid, "storyVersionId": 302, "mode": "PARENT_CHILD",
        "sourceReadingSessionId": source_id, "expectedRevision": 1, "clientRequestId": request_key,
    })
    assert switched.status_code == 201
    successor_id = switched.json()["data"]["readingSessionId"]
    successor = f"{sessions}/{successor_id}"
    page_event = {
        "eventType": "PAGE_CHANGED", "pageNumber": 3, "expectedRevision": 1, "clientEventId": request_key,
    }
    advanced = first.post(f"{successor}/events", headers=headers, json=page_event)
    assert advanced.status_code == 201
    assert first.get(successor).json()["data"]["session"]["visitedPageNumbers"] == [1, 2, 3]
    replayed = first.post(f"{successor}/events", headers=headers, json=page_event)
    assert replayed.status_code == 200 and replayed.json()["data"] == advanced.json()["data"]

    assert first.delete(source, headers=headers).status_code == 204
    if ending == "finish":
        finished = first.post(f"{successor}/completions", headers=headers, json={
            "reason": "USER_FINISH", "expectedRevision": 2, "clientEventId": "finish-successor",
        })
        assert finished.status_code == 200
    else:
        _seed_continuation_draft(db_path, 303, 3, successor_id, 2, 3, [1, 2, 3], src_version_id=302)
        assert first.post("/api/v1/story-versions/303/approvals", headers=headers, json={
            "versionNumber": 3, "reviewedAllBranches": True,
        }).status_code == 200
        next_switch = first.post(sessions, headers=headers, json={
            "childProfileId": pid, "storyVersionId": 303, "mode": "PARENT_CHILD",
            "sourceReadingSessionId": successor_id, "expectedRevision": 2, "clientRequestId": "next-continuation",
        })
        assert next_switch.status_code == 201
    report = first.get(successor).json()["data"]["report"]
    assert report["summary"] == "本次共读阅读了第 2、3 页。"


def test_approval_enforces_backend_safety_freezes_media_and_handles_dependency_recovery(cont_env, tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image

    first, second, db_path, pid = cont_env
    h1 = {"X-CSRF-Token": first.cookies["zhiya_csrf"]}
    media_root = tmp_path / "media"
    media_root.mkdir()
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(media_root))

    def make_png(color: tuple[int, int, int]) -> bytes:
        buf = BytesIO()
        Image.new("RGB", (4, 4), color).save(buf, format="PNG")
        return buf.getvalue()

    green_png = make_png((0, 200, 0))
    red_png = make_png((200, 0, 0))
    (media_root / "resume.png").write_bytes(green_png)

    sid1 = first.post("/api/v1/reading-sessions", headers=h1, json={
        "childProfileId": pid, "storyVersionId": 301, "mode": "PARENT_CHILD",
    }).json()["data"]["readingSessionId"]
    assert first.post(f"/api/v1/reading-sessions/{sid1}/events", headers=h1, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "m1",
    }).status_code == 201
    assert first.post(f"/api/v1/reading-sessions/{sid1}/events", headers=h1, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "m2",
    }).status_code == 201

    _seed_continuation_draft(db_path, 307, 2, sid1, 2, 2, [1, 2], branching=1)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_pages SET image_ref = 'resume.png' WHERE version_id = 307 AND page_no = 3")
        db.execute("UPDATE story_pages SET body_text = '请模仿危险操作玩火' WHERE version_id = 307 AND page_no = 3")

    # Even with reviewedAllBranches=True, backend safety check blocks unsafe text with 422.
    unsafe = first.post("/api/v1/story-versions/307/approvals", headers=h1,
                        json={"versionNumber": 2, "reviewedAllBranches": True})
    assert unsafe.status_code == 422 and unsafe.json()["error"]["code"] == "VALIDATION_ERROR"

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_pages SET body_text = '安全续写第3页' WHERE version_id = 307 AND page_no = 3")
        db.execute("UPDATE story_branches SET choice_label = '询问家庭地址' WHERE id = 901")
    unsafe_branch = first.post("/api/v1/story-versions/307/approvals", headers=h1,
                               json={"versionNumber": 2, "reviewedAllBranches": True})
    assert unsafe_branch.status_code == 422 and unsafe_branch.json()["error"]["code"] == "VALIDATION_ERROR"

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_branches SET choice_label = '去森林' WHERE id = 901")

    # A pending draft from an existing database binds its preview on the owner's first read.
    preview_url = "/api/v1/media/asset-307-page-3"
    assert second.get(preview_url).status_code == 404
    with ThreadPoolExecutor(max_workers=2) as pool:
        reads = [pool.submit(first.get, preview_url) for _ in range(2)]
        assert [read.result().content for read in reads] == [green_png, green_png]
    (media_root / "resume.png").write_bytes(red_png)

    # Missing media root during API-026 returns retryable 503 and rolls back status/retirement.
    offline_root = tmp_path / "media-offline"
    media_root.rename(offline_root)
    dep_fail = first.post("/api/v1/story-versions/307/approvals", headers=h1,
                          json={"versionNumber": 2, "reviewedAllBranches": True})
    assert dep_fail.status_code == 503 and dep_fail.json()["error"]["retryable"] is True
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT status FROM story_versions WHERE id = 307").fetchone()[0] == "PENDING_REVIEW"
        assert db.execute("SELECT status FROM story_versions WHERE id = 301").fetchone()[0] == "APPROVED_PRIVATE"
    offline_root.rename(media_root)

    approved = first.post("/api/v1/story-versions/307/approvals", headers=h1,
                          json={"versionNumber": 2, "reviewedAllBranches": True})
    assert approved.status_code == 200
    p3 = next(p for p in approved.json()["data"]["pages"] if p["pageNumber"] == 3)
    assert p3["imageRef"] == "asset-307-page-3"

    # Approval uses the previously reviewed green image after the template was replaced.
    assert first.get("/api/v1/media/asset-307-page-3").content == green_png
    assert second.get("/api/v1/media/asset-307-page-3").status_code == 404

    # Temporarily move blobs directory before API-011 switch: resume_page media verification fails closed with retryable 503.
    blobs_dir = db_path.parent / f".{db_path.name}.media_pins" / "blobs"
    blobs_offline = tmp_path / "blobs-offline"
    blobs_dir.rename(blobs_offline)
    switch_req = {
        "childProfileId": pid, "storyVersionId": 307, "mode": "PARENT_CHILD",
        "sourceReadingSessionId": sid1, "expectedRevision": 2, "clientRequestId": "switch-media-307",
    }
    media_503 = first.post("/api/v1/reading-sessions", headers=h1, json=switch_req)
    assert media_503.status_code == 503 and media_503.json()["error"]["retryable"] is True
    assert first.get(f"/api/v1/reading-sessions/{sid1}").json()["data"]["session"]["status"] == "IN_PROGRESS"

    blobs_offline.rename(blobs_dir)
    switched = first.post("/api/v1/reading-sessions", headers=h1, json=switch_req)
    assert switched.status_code == 201
    sid2 = switched.json()["data"]["readingSessionId"]

    # Withdrawing target version blocks SESSION_CREATE replay with 404.
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'WITHDRAWN' WHERE id = 307")
    assert first.post("/api/v1/reading-sessions", headers=h1, json=switch_req).status_code == 404
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET status = 'APPROVED_PRIVATE' WHERE id = 307")
    assert first.post("/api/v1/reading-sessions", headers=h1, json=switch_req).status_code == 200

    # Deleting continuation session cleans up its SESSION_CREATE idempotency row.
    assert first.delete(f"/api/v1/reading-sessions/{sid2}", headers=h1).status_code == 204
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM request_executions WHERE scope_type = 'SESSION_CREATE'").fetchone()[0] == 0
