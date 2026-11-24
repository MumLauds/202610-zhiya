"""Regression tests for story version approval lineage, branches, and pinned media."""

import sqlite3
from contextlib import closing
from io import BytesIO

import pytest
from PIL import Image

from test_continuations import _seed_continuation_draft, cont_env


def _seed_draft(cont_env, *, source_branch=False, branching=0):
    client, _, db_path, profile_id = cont_env
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    created = client.post("/api/v1/reading-sessions", headers=headers, json={
        "childProfileId": profile_id, "storyVersionId": 301, "mode": "PARENT_CHILD",
    })
    assert created.status_code == 201, created.text
    session_url = f"/api/v1/reading-sessions/{created.json()['data']['readingSessionId']}"
    started = client.post(f"{session_url}/events", headers=headers, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "source-start",
    })
    assert started.status_code == 201, started.text
    step = {"expectedRevision": 1, "clientEventId": "source-next"}
    if source_branch:
        advanced = client.post(f"{session_url}/branches", headers=headers, json={**step, "branchId": 801})
        source_page = 3
    else:
        advanced = client.post(f"{session_url}/events", headers=headers, json={
            **step, "eventType": "PAGE_CHANGED", "pageNumber": 2,
        })
        source_page = 2
    assert advanced.status_code == 201, advanced.text
    _seed_continuation_draft(
        db_path, 302, 2, created.json()["data"]["readingSessionId"], 2, source_page, [1, source_page],
        branching=branching,
    )
    return session_url


def _approve(client):
    return client.post(
        "/api/v1/story-versions/302/approvals",
        headers={"X-CSRF-Token": client.cookies["zhiya_csrf"]},
        json={"versionNumber": 2, "reviewedAllBranches": True},
    )


def _version_states(db_path):
    with closing(sqlite3.connect(db_path)) as db:
        return db.execute("SELECT id, status FROM story_versions ORDER BY id").fetchall()


def _png(color):
    buffer = BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def illustrated_draft(cont_env, tmp_path, monkeypatch):
    client, _, db_path, _ = cont_env
    media_root = tmp_path / "media"
    media_root.mkdir()
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(media_root))
    original_png = _png("green")
    (media_root / "prefix.png").write_bytes(original_png)
    (media_root / "other.png").write_bytes(_png("red"))
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET branching = 1 WHERE id = 301")
        db.execute("INSERT INTO story_branches VALUES (801, 301, 1, 3, '走近路', 'APPROVED')")
        db.execute("UPDATE story_pages SET image_ref = 'prefix.png' WHERE version_id = 301 AND page_no = 3")
    _seed_draft(cont_env, source_branch=True)
    original = client.get("/api/v1/media/asset-301-page-3")
    assert original.status_code == 200 and original.content == original_png
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_pages SET image_ref = 'prefix.png' WHERE version_id = 302 AND page_no = 2")
    return media_root, original_png


def test_approval_rejects_continuation_of_another_owned_book(cont_env):
    client, _, db_path, _ = cont_env
    _seed_draft(cont_env)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("INSERT INTO storybooks VALUES (202, 1, 'ORIGINAL', 1)")
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max, license_note,
             status, reviewed_by_id, reviewed_at, created_at, branching)
            VALUES (310, 202, 1, '独立故事B', '[]', 3, 5, '原创授权',
                    'APPROVED_PRIVATE', 1, 100, 1, 0)""")
        db.execute("""INSERT INTO story_pages
            (version_id, page_no, body_text, image_ref, default_next_page_number)
            SELECT 310, page_no, body_text, image_ref, default_next_page_number
            FROM story_pages WHERE version_id = 301""")
        db.execute("UPDATE story_versions SET storybook_id = 202 WHERE id = 302")

    rejected = _approve(client)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _version_states(db_path) == [(301, "APPROVED_PRIVATE"), (302, "PENDING_REVIEW"), (310, "APPROVED_PRIVATE")]


def test_approval_requires_source_path_edges_even_after_session_advances(cont_env):
    client, _, db_path, _ = cont_env
    branch_sql = "INSERT INTO story_branches VALUES (801, 301, 1, 3, '走近路', ?)"
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET branching = 1 WHERE id = 301")
        db.execute(branch_sql, ("APPROVED",))
    session_url = _seed_draft(cont_env, source_branch=True)
    advanced = client.post(f"{session_url}/events", headers={"X-CSRF-Token": client.cookies["zhiya_csrf"]}, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 4, "expectedRevision": 2, "clientEventId": "source-after-draft",
    })
    assert advanced.status_code == 201, advanced.text

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("DELETE FROM story_branches WHERE id = 801")
        db.execute("UPDATE story_versions SET branching = 0 WHERE id = 301")
    rejected = _approve(client)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _version_states(db_path) == [(301, "APPROVED_PRIVATE"), (302, "PENDING_REVIEW")]

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET branching = 1 WHERE id = 301")
        db.execute(branch_sql, ("PENDING",))
    rejected = _approve(client)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _version_states(db_path) == [(301, "APPROVED_PRIVATE"), (302, "PENDING_REVIEW")]

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_branches SET review_status = 'APPROVED' WHERE id = 801")
    approved = _approve(client)
    assert approved.status_code == 200, approved.text
    assert _version_states(db_path) == [(301, "RETIRED"), (302, "APPROVED_PRIVATE")]


@pytest.mark.parametrize(("source_revision", "source_page"), [(2, 3), (3, 2)], ids=["different-path", "future-revision"])
def test_approval_rejects_snapshot_inconsistent_with_live_session(cont_env, source_revision, source_page):
    client, _, db_path, _ = cont_env
    _seed_draft(cont_env)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET branching = 1 WHERE id = 301")
        db.execute("INSERT INTO story_branches VALUES (801, 301, 1, 3, '走近路', 'APPROVED')")
        db.execute(
            "UPDATE story_versions SET source_revision = ?, source_page_no = ?, source_visited_pages_json = ? WHERE id = 302",
            (source_revision, source_page, f"[1,{source_page}]"),
        )
        db.execute(
            """UPDATE story_pages SET body_text = (
                SELECT body_text FROM story_pages WHERE version_id = 301 AND page_no = ?
            ) WHERE version_id = 302 AND page_no = 2""",
            (source_page,),
        )

    rejected = _approve(client)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _version_states(db_path) == [(301, "APPROVED_PRIVATE"), (302, "PENDING_REVIEW")]


def test_approval_requires_path_actually_read_at_older_revision(cont_env):
    client, _, db_path, _ = cont_env
    session_url = _seed_draft(cont_env)
    advanced = client.post(f"{session_url}/events", headers={"X-CSRF-Token": client.cookies["zhiya_csrf"]}, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 3, "expectedRevision": 2, "clientEventId": "source-after-draft",
    })
    assert advanced.status_code == 201, advanced.text

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_versions SET branching = 1 WHERE id = 301")
        db.execute("INSERT INTO story_branches VALUES (801, 301, 1, 3, '走近路', 'APPROVED')")
        db.execute("""UPDATE story_versions SET source_page_no = 3, source_visited_pages_json = '[1,3]'
            WHERE id = 302""")
        db.execute("UPDATE story_pages SET body_text = 'v1第3页' WHERE version_id = 302 AND page_no = 2")

    rejected = _approve(client)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _version_states(db_path) == [(301, "APPROVED_PRIVATE"), (302, "PENDING_REVIEW")]

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("""UPDATE story_versions SET source_page_no = 2, source_visited_pages_json = '[1,2]'
            WHERE id = 302""")
        db.execute("UPDATE story_pages SET body_text = 'v1第2页' WHERE version_id = 302 AND page_no = 2")

    approved = _approve(client)
    assert approved.status_code == 200, approved.text
    assert _version_states(db_path) == [(301, "RETIRED"), (302, "APPROVED_PRIVATE")]


@pytest.mark.parametrize(("revision", "prefix"), [(1, [1, 2, 3]), (2, [1, 2, 3, 4])], ids=["started", "branch-chosen"])
def test_approval_recovers_older_successor_snapshot(cont_env, revision, prefix):
    client, _, db_path, profile_id = cont_env
    source_url = _seed_draft(cont_env, branching=1)
    approved = _approve(client)
    assert approved.status_code == 200, approved.text

    sessions_url = "/api/v1/reading-sessions"
    headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
    switched = client.post(sessions_url, headers=headers, json={
        "childProfileId": profile_id, "storyVersionId": 302, "mode": "PARENT_CHILD",
        "sourceReadingSessionId": int(source_url.rsplit("/", 1)[1]),
        "expectedRevision": 2, "clientRequestId": "switch-to-second-version",
    })
    assert switched.status_code == 201, switched.text
    successor = switched.json()["data"]
    assert successor["visitedPageNumbers"] == [1, 2, 3]
    session_id = successor["readingSessionId"]
    session_url = f"{sessions_url}/{session_id}"

    chosen = client.post(f"{session_url}/branches", headers=headers, json={
        "branchId": 901, "expectedRevision": 1, "clientEventId": "source-branch",
    })
    assert chosen.status_code == 201, chosen.text
    advanced = client.post(f"{session_url}/events", headers=headers, json={
        "eventType": "PAGE_CHANGED", "pageNumber": 5, "expectedRevision": 2, "clientEventId": "source-page-five",
    })
    assert advanced.status_code == 201, advanced.text

    _seed_continuation_draft(db_path, 303, 3, session_id, revision, prefix[-1], prefix, src_version_id=302)
    approved = client.post("/api/v1/story-versions/303/approvals", headers=headers, json={
        "versionNumber": 3, "reviewedAllBranches": True,
    })
    assert approved.status_code == 200, approved.text
    assert _version_states(db_path) == [(301, "RETIRED"), (302, "RETIRED"), (303, "APPROVED_PRIVATE")]


def test_approval_rejects_distinct_branch_labels_with_same_destination(cont_env):
    client, _, db_path, _ = cont_env
    _seed_draft(cont_env, branching=1)
    with closing(sqlite3.connect(db_path)) as db, db:
        # Keep page 6 reachable even when both options lead to page 4.
        db.execute("UPDATE story_pages SET default_next_page_number = 6 WHERE version_id = 302 AND page_no = 5")
        db.execute("UPDATE story_branches SET next_page_no = 4 WHERE id = 902")

    rejected = _approve(client)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _version_states(db_path) == [(301, "APPROVED_PRIVATE"), (302, "PENDING_REVIEW")]
    with closing(sqlite3.connect(db_path)) as db, db:
        assert db.execute("SELECT review_status FROM story_branches ORDER BY id").fetchall() == [("PENDING",)] * 2
        db.execute("UPDATE story_branches SET next_page_no = 6 WHERE id = 902")

    corrected = _approve(client)
    assert corrected.status_code == 200, corrected.text


def test_approval_requires_matching_prefix_image_and_reuses_frozen_source(cont_env, illustrated_draft):
    client, _, db_path, _ = cont_env
    media_root, original_png = illustrated_draft
    replacement_png = _png("blue")
    (media_root / "prefix.png").write_bytes(replacement_png)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_pages SET image_ref = 'other.png' WHERE version_id = 302 AND page_no = 2")

    rejected = _approve(client)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _version_states(db_path) == [(301, "APPROVED_PRIVATE"), (302, "PENDING_REVIEW")]

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_pages SET image_ref = 'prefix.png' WHERE version_id = 302 AND page_no = 2")
    approved = _approve(client)
    assert approved.status_code == 200, approved.text
    fetched = client.get("/api/v1/story-versions/302")
    assert fetched.status_code == 200, fetched.text
    for response in (approved, fetched):
        carried = response.json()["data"]["pages"][1]
        assert carried["pageNumber"] == 2 and carried["text"] == "v1第3页"
        image = client.get(f"/api/v1/media/{carried['imageRef']}")
        assert image.status_code == 200 and image.content == original_png


def test_approval_commit_failure_keeps_previous_version_and_retry_uses_current_image(cont_env, illustrated_draft):
    client, other, db_path, _ = cont_env
    media_root, original_png = illustrated_draft
    (media_root / "resume.png").write_bytes(_png("red"))
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE story_pages SET image_ref = 'resume.png' WHERE version_id = 302 AND page_no = 3")
        # A deferred FK violation fails at commit, after the approval writes succeed.
        db.execute("CREATE TABLE approval_parent (id INTEGER PRIMARY KEY)")
        db.execute("""CREATE TABLE approval_commit_fault (
            id INTEGER REFERENCES approval_parent(id) DEFERRABLE INITIALLY DEFERRED
        )""")
        db.execute("""CREATE TRIGGER fail_approval_commit AFTER UPDATE OF status ON story_versions
            WHEN NEW.id = 302 AND NEW.status = 'APPROVED_PRIVATE'
            BEGIN INSERT INTO approval_commit_fault VALUES (1); END""")

    failed = _approve(client)
    assert failed.status_code == 503, failed.text
    assert failed.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert _version_states(db_path) == [(301, "APPROVED_PRIVATE"), (302, "PENDING_REVIEW")]
    assert other.get("/api/v1/media/asset-302-page-3").status_code == 404
    with closing(sqlite3.connect(db_path)) as db, db:
        assert db.execute("SELECT COUNT(*) FROM approval_commit_fault").fetchone() == (0,)
        assert db.execute("SELECT approved_page_media_json FROM story_versions WHERE id = 302").fetchone() == (None,)
        db.execute("DROP TRIGGER fail_approval_commit")

    current_png = _png("blue")
    (media_root / "resume.png").write_bytes(current_png)
    approved = _approve(client)
    assert approved.status_code == 200, approved.text
    assert _version_states(db_path) == [(301, "RETIRED"), (302, "APPROVED_PRIVATE")]

    (media_root / "resume.png").write_bytes(_png("red"))
    pages = approved.json()["data"]["pages"]
    for page, expected in ((pages[1], original_png), (pages[2], current_png)):
        image = client.get(f"/api/v1/media/{page['imageRef']}")
        assert image.status_code == 200 and image.content == expected


def test_deleting_source_session_blocks_unreviewed_continuation_approval(cont_env, illustrated_draft):
    client, _, db_path, _ = cont_env
    media_root, original_png = illustrated_draft
    with closing(sqlite3.connect(db_path)) as db, db:
        source_id = db.execute("SELECT source_session_id FROM story_versions WHERE id = 302").fetchone()[0]
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max, license_note, status, created_at, branching)
            SELECT 304, storybook_id, 4, title, topics_json, age_min, age_max, license_note, 'DRAFT', created_at, 0
            FROM story_versions WHERE id = 301""")
    _seed_continuation_draft(db_path, 303, 3, source_id, 2, 3, [1, 3], status="DRAFT")

    deleted = client.delete(
        f"/api/v1/reading-sessions/{source_id}", headers={"X-CSRF-Token": client.cookies["zhiya_csrf"]},
    )
    assert deleted.status_code == 204, deleted.text
    (media_root / "prefix.png").write_bytes(_png("blue"))

    rejected = _approve(client)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "SESSION_CONFLICT"
    assert _version_states(db_path) == [
        (301, "APPROVED_PRIVATE"), (302, "CHECK_FAILED"), (303, "CHECK_FAILED"), (304, "DRAFT"),
    ]
    original = client.get("/api/v1/story-versions/301")
    assert original.status_code == 200, original.text
    image_ref = original.json()["data"]["pages"][2]["imageRef"]
    image = client.get(f"/api/v1/media/{image_ref}")
    assert image.status_code == 200 and image.content == original_png


def test_approved_continuation_keeps_frozen_image_after_source_session_deletion(cont_env, illustrated_draft):
    client, _, db_path, _ = cont_env
    media_root, original_png = illustrated_draft
    with closing(sqlite3.connect(db_path)) as db:
        source_id = db.execute("SELECT source_session_id FROM story_versions WHERE id = 302").fetchone()[0]
    approved = _approve(client)
    assert approved.status_code == 200, approved.text
    approved_page = approved.json()["data"]["pages"][1]

    deleted = client.delete(
        f"/api/v1/reading-sessions/{source_id}", headers={"X-CSRF-Token": client.cookies["zhiya_csrf"]},
    )
    assert deleted.status_code == 204, deleted.text
    (media_root / "prefix.png").write_bytes(_png("blue"))

    fetched = client.get("/api/v1/story-versions/302")
    assert fetched.status_code == 200, fetched.text
    fetched_page = fetched.json()["data"]["pages"][1]
    assert fetched_page["text"] == approved_page["text"] == "v1第3页"
    assert fetched_page["imageRef"] == approved_page["imageRef"]
    image = client.get(f"/api/v1/media/{fetched_page['imageRef']}")
    assert image.status_code == 200 and image.content == original_png
    assert _version_states(db_path) == [(301, "RETIRED"), (302, "APPROVED_PRIVATE")]


def test_approval_freezes_image_for_original_without_continuation(cont_env, tmp_path, monkeypatch):
    client, _, db_path, _ = cont_env
    monkeypatch.setenv("ZHIYA_MEDIA_ROOT", str(tmp_path))
    image_file = tmp_path / "original.png"
    original_png = _png("green")
    image_file.write_bytes(original_png)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max,
             license_note, status, created_at, branching)
            VALUES (302, 201, 2, '小狐狸原创第二版', '[]', 3, 5, '原创授权', 'PENDING_REVIEW', 1, 0)""")
        db.execute("""INSERT INTO story_pages
            (version_id, page_no, body_text, image_ref, default_next_page_number)
            SELECT 302, page_no, body_text,
                   CASE WHEN page_no = 1 THEN 'original.png' ELSE image_ref END,
                   default_next_page_number
            FROM story_pages WHERE version_id = 301""")
        db.execute("""UPDATE story_pages SET image_ref = 'optional-missing.png', image_license_note = 'NO-COMMERCIAL'
                      WHERE version_id = 302 AND page_no = 2""")

    approved = _approve(client)
    assert approved.status_code == 200, approved.text
    assert _version_states(db_path) == [(301, "RETIRED"), (302, "APPROVED_PRIVATE")]
    image_ref = approved.json()["data"]["pages"][0]["imageRef"]
    assert approved.json()["data"]["pages"][1]["imageRef"] is None

    image_file.write_bytes(_png("red"))
    fetched = client.get("/api/v1/story-versions/302")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["pages"][0]["imageRef"] == image_ref
    image = client.get(f"/api/v1/media/{image_ref}")
    assert image.status_code == 200 and image.content == original_png
