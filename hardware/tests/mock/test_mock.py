"""Offline tests for the H05 fixture builder and standard-library Mock server.

Run from the repository root:
    python -m unittest discover -s hardware/tests/mock -v

The tests use temporary directories and a loopback HTTP server. They do not
require Pillow, a real ESP32 board, a cloud AI service or the production
backend.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "hardware" / "mock"))

import build_fixtures  # noqa: E402
import server as mock_server  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HttpResult:
    def __init__(self, status: int, payload: dict, headers: dict[str, str]) -> None:
        self.status = status
        self.payload = payload
        self.headers = headers

    @property
    def data(self) -> dict:
        return self.payload["data"]

    @property
    def error(self) -> dict:
        return self.payload["error"]


class MockTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fixtures_dir = Path(cls.tmp.name) / "fixtures"
        build_fixtures.build(REPO_ROOT, cls.fixtures_dir)
        cls.httpd = mock_server.create_server("127.0.0.1", 0, cls.fixtures_dir, quiet=True)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.request("POST", "/mock/reset", {})

    def request(self, method: str, path: str, body: dict | None = None,
                headers: dict[str, str] | None = None) -> HttpResult:
        payload = None
        request_headers = dict(headers or {})
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = Request(self.base + path, data=payload, headers=request_headers, method=method)
        try:
            # A fresh opener per call keeps concurrent tests independent.
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                return HttpResult(response.status, json.loads(response.read().decode("utf-8")), dict(response.headers))
        except HTTPError as exc:
            return HttpResult(exc.code, json.loads(exc.read().decode("utf-8")), dict(exc.headers))

    def fixture(self, name: str) -> dict:
        return json.loads((self.fixtures_dir / name).read_text(encoding="utf-8"))

    # ---------------- data conversion ----------------

    def test_fixture_counts_and_published_flags(self) -> None:
        books = self.fixture("storybooks.json")["items"]
        details = self.fixture("story-details.json")["items"]
        self.assertEqual(20, len(books))
        self.assertEqual(20, len(details))
        self.assertEqual(104, sum(len(item["pages"]) for item in details))
        self.assertEqual({item["storybookId"] for item in books}, set(range(2, 22)))
        self.assertTrue(all(item["status"] == "PUBLISHED" for item in books))
        self.assertTrue(all(item["demoOnly"] for item in books))
        self.assertTrue(all(item["coverRef"] is None for item in books))
        self.assertTrue(all(page["imageRef"] is None for item in details for page in item["pages"]))
        self.assertTrue(all(page["localPageAsset"] is None for item in details for page in item["pages"]))
        self.assertEqual(20, len({item["storybookId"] for item in books}))
        versions = json.loads((REPO_ROOT / "data" / "seed" / "story_versions.json").read_text(encoding="utf-8"))
        drafts = [item for item in versions if item["status"] == "DRAFT"]
        self.assertEqual(1, len(drafts))
        self.assertEqual(1, drafts[0]["storybook_id"])
        self.assertNotIn(1, {item["storybookId"] for item in books})

    def test_book_no_mapping_and_version_resolution(self) -> None:
        mapping = json.loads((REPO_ROOT / "data" / "seed" / "cover_mapping.json").read_text(encoding="utf-8"))
        versions = json.loads((REPO_ROOT / "data" / "seed" / "story_versions.json").read_text(encoding="utf-8"))
        details = {item["storybookId"]: item for item in self.fixture("story-details.json")["items"]}
        manifest_assets = json.loads(
            (REPO_ROOT / "hardware" / "assets" / "generated" / "assets-manifest.json").read_text(encoding="utf-8")
        )["assets"]
        assets_by_key = {(asset["bookNo"], asset["assetRole"]): asset for asset in manifest_assets}
        self.assertEqual(20, len(mapping))
        for cover in mapping:
            book = details[cover["storybook_id"]]
            expected_version = next(item for item in versions if item["storybook_id"] == cover["storybook_id"])
            self.assertEqual(1, sum(1 for item in versions if item["storybook_id"] == cover["storybook_id"]))
            self.assertEqual(cover["book_no"], assets_by_key[(cover["book_no"], "THUMBNAIL")]["bookNo"])
            self.assertEqual(expected_version["id"], book["storyVersionId"])
            for role_name, role in (("thumbnail", "THUMBNAIL"), ("detail", "DETAIL")):
                asset = book["localCover"][role_name]
                expected = assets_by_key[(cover["book_no"], role)]
                self.assertEqual(cover["cover_path"], expected["sourcePath"])
                self.assertEqual(asset["assetPath"], expected["assetPath"])
                self.assertTrue((REPO_ROOT / asset["assetPath"]).is_file())

    def test_pages_start_at_one_and_last_page_is_null(self) -> None:
        for detail in self.fixture("story-details.json")["items"]:
            numbers = [page["pageNumber"] for page in detail["pages"]]
            self.assertEqual(list(range(1, len(numbers) + 1)), numbers)
            self.assertIsNone(detail["pages"][-1]["defaultNextPageNumber"])
            for page in detail["pages"][:-1]:
                self.assertEqual(page["pageNumber"] + 1, page["defaultNextPageNumber"])
                self.assertTrue(page["text"].strip())
            for page in detail["pages"]:
                self.assertIsNone(page["localPageAsset"])
                self.assertEqual("MISSING_INNER_ILLUSTRATION", page["imageStatus"])

    def test_books_and_details_are_consistent(self) -> None:
        books = {item["storybookId"]: item for item in self.fixture("storybooks.json")["items"]}
        details = {item["storybookId"]: item for item in self.fixture("story-details.json")["items"]}
        self.assertEqual(set(books), set(details))
        for book_id, book in books.items():
            detail = details[book_id]
            for key in ("storyVersionId", "versionNumber", "title", "status", "ageBand", "pageCount", "localCover"):
                self.assertEqual(book[key], detail[key], key)
            self.assertEqual(book["pageCount"], len(detail["pages"]))

    def test_h03_assets_are_unchanged_and_complete(self) -> None:
        manifest = json.loads((REPO_ROOT / "hardware" / "assets" / "generated" / "assets-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(40, len(manifest["assets"]))
        for asset in manifest["assets"]:
            path = REPO_ROOT / asset["assetPath"]
            self.assertTrue(path.is_file())
            self.assertEqual(asset["sha256"], sha256_file(path))
            self.assertEqual(asset["fileSize"], path.stat().st_size)
            self.assertEqual(asset["format"], "JPEG")

    def test_source_files_use_stable_lf_text_digest(self) -> None:
        fixture = self.fixture("storybooks.json")
        self.assertEqual("sha256-utf8-lf-v1", fixture["sourceDigest"])
        self.assertEqual(5, len(fixture["sourceFiles"]))
        for entry in fixture["sourceFiles"]:
            self.assertEqual(entry["sha256"], build_fixtures.sha256_text_file(REPO_ROOT / entry["path"]))
        self.assertEqual(
            fixture["sourceFiles"],
            self.fixture("story-details.json")["sourceFiles"],
        )
        with tempfile.TemporaryDirectory() as temp:
            lf_path = Path(temp) / "lf.json"
            crlf_path = Path(temp) / "crlf.json"
            text = '{"title":"知芽","line":1}\n{"line":2}\n'
            lf_path.write_bytes(text.encode("utf-8"))
            crlf_path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
            self.assertEqual(
                build_fixtures.sha256_text_file(lf_path),
                build_fixtures.sha256_text_file(crlf_path),
            )

    def test_rebuild_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            other_path = Path(other) / "fixtures"
            build_fixtures.build(REPO_ROOT, other_path)
            for name in build_fixtures.OUTPUT_FILES:
                self.assertEqual(
                    (self.fixtures_dir / name).read_bytes(),
                    (other_path / name).read_bytes(),
                    name,
                )
            self.assertEqual([], build_fixtures.check(REPO_ROOT, other_path))

    # ---------------- HTTP server ----------------

    def test_health_and_meta_are_mock_marked(self) -> None:
        health = self.request("GET", "/api/v1/health")
        self.assertEqual(200, health.status)
        self.assertEqual("ok", health.data["status"])
        self.assertEqual("DEMO", health.data["mode"])
        self.assertTrue(health.data["mock"])
        meta = self.request("GET", "/mock/meta")
        self.assertEqual(200, meta.status)
        self.assertEqual(20, meta.data["bookCount"])
        self.assertEqual(104, meta.data["pageCount"])

    def test_storybooks_and_detail(self) -> None:
        response = self.request("GET", "/api/v1/storybooks")
        self.assertEqual(200, response.status)
        self.assertEqual(20, len(response.data["items"]))
        mapping = json.loads((REPO_ROOT / "data" / "seed" / "cover_mapping.json").read_text(encoding="utf-8"))
        self.assertEqual(mapping[0]["book_no"], "B-002")
        self.assertEqual("THUMBNAIL", response.data["items"][0]["localCover"]["thumbnail"]["assetRole"])
        filtered = self.request("GET", "/api/v1/storybooks?ageBand=AGE_5_6")
        self.assertEqual(200, filtered.status)
        self.assertEqual(6, len(filtered.data["items"]))
        detail = self.request("GET", "/api/v1/story-versions/2")
        self.assertEqual(200, detail.status)
        self.assertEqual(6, len(detail.data["pages"]))
        self.assertIsNone(detail.data["coverRef"])
        self.assertIsNone(detail.data["pages"][0]["imageRef"])

    def test_storybook_list_is_lightweight(self) -> None:
        response = self.request("GET", "/api/v1/storybooks")
        self.assertEqual(200, response.status)
        self.assertEqual(20, len(response.data["items"]))
        self.assertEqual({"items", "page", "pageSize"}, set(response.data))
        fixture = self.fixture("storybooks.json")["items"]
        expected_keys = set(fixture[0])
        for item in response.data["items"]:
            self.assertEqual(expected_keys, set(item))
            self.assertNotIn("pages", item)
            self.assertNotIn("branches", item)
            self.assertNotIn("summary", item)
            self.assertTrue(item["demoOnly"])
        detail = self.request("GET", "/api/v1/story-versions/2")
        self.assertEqual(200, detail.status)
        self.assertIn("pages", detail.data)
        self.assertIn("branches", detail.data)

    def test_storybook_fixture_is_lightweight_before_http(self) -> None:
        fixture = self.fixture("storybooks.json")["items"]
        for item in fixture:
            self.assertNotIn("pages", item)
            self.assertNotIn("branches", item)
            self.assertNotIn("summary", item)
            self.assertNotIn("text", item)

    def test_page_changed_rejects_visited_page_even_if_default_next_loops(self) -> None:
        # A malformed/legacy fixture must not turn H06 local re-display into
        # an accepted server event, even if defaultNextPageNumber points back.
        with self.httpd.state.lock:
            detail = self.httpd.state.details_by_version[2]
            detail["pages"][1]["defaultNextPageNumber"] = 1
        started = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "loop-start",
        })
        self.assertEqual(201, started.status)
        page_two = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "loop-page-2",
        })
        self.assertEqual(201, page_two.status)
        back = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 1, "expectedRevision": 2, "clientEventId": "loop-back-1",
        })
        self.assertEqual(409, back.status)
        self.assertEqual("SESSION_CONFLICT", back.error["code"])
        current = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(2, current.data["session"]["revision"])
        self.assertEqual(2, current.data["session"]["currentPage"])
        self.assertEqual([1, 2], current.data["session"]["visitedPageNumbers"])

    def test_storybook_list_is_rejected_if_fixture_contains_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixtures_dir = Path(temp) / "fixtures"
            build_fixtures.build(REPO_ROOT, fixtures_dir)
            books_path = fixtures_dir / "storybooks.json"
            books = json.loads(books_path.read_text(encoding="utf-8"))
            books["items"][0]["pages"] = [{"pageNumber": 1}]
            books_path.write_text(json.dumps(books, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(mock_server.MockFixtureError):
                mock_server.load_fixtures(fixtures_dir)

    def test_reading_session_forward_rejects_backward_and_skip(self) -> None:
        started = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start-1",
        })
        self.assertEqual(201, started.status)
        self.assertEqual(1, started.data["revision"])
        current = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual("IN_PROGRESS", current.data["session"]["status"])
        self.assertEqual([1], current.data["session"]["visitedPageNumbers"])

        next_page = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "page-2",
        })
        self.assertEqual(201, next_page.status)
        self.assertEqual(2, next_page.data["revision"])
        # Returning to an old page is H06 local re-display, not an API-015 event.
        back = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 1, "expectedRevision": 2, "clientEventId": "back-1",
        })
        self.assertEqual(409, back.status)
        self.assertEqual("SESSION_CONFLICT", back.error["code"])
        after_back = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(2, after_back.data["session"]["revision"])
        self.assertEqual(2, after_back.data["session"]["currentPage"])
        self.assertEqual([1, 2], after_back.data["session"]["visitedPageNumbers"])
        bad = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 6, "expectedRevision": 2, "clientEventId": "skip-6",
        })
        self.assertEqual(409, bad.status)
        self.assertEqual("SESSION_CONFLICT", bad.error["code"])

    def test_idempotent_replay_and_conflict(self) -> None:
        body = {"eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "same"}
        first = self.request("POST", "/api/v1/reading-sessions/1/events", body)
        self.assertEqual(201, first.status)
        replay = self.request("POST", "/api/v1/reading-sessions/1/events", body)
        self.assertEqual(200, replay.status)
        self.assertEqual(first.data["interactionEventId"], replay.data["interactionEventId"])
        current = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(1, current.data["session"]["revision"])
        changed = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "same",
        })
        self.assertEqual(409, changed.status)
        self.assertEqual("IDEMPOTENCY_CONFLICT", changed.error["code"])

    def test_wrong_revision_is_conflict(self) -> None:
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start-wrong",
        })
        bad = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 0, "clientEventId": "wrong-rev",
        })
        self.assertEqual(409, bad.status)
        self.assertEqual("SESSION_CONFLICT", bad.error["code"])

    def test_duplicate_started_and_forbidden_are_rejected(self) -> None:
        first = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start-first",
        })
        self.assertEqual(201, first.status)
        second = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 1, "clientEventId": "start-second",
        })
        self.assertEqual(409, second.status)
        forbidden = self.request("GET", "/api/v1/storybooks", headers={"X-Mock-Error": "FORBIDDEN"})
        self.assertEqual(403, forbidden.status)
        self.assertEqual("FORBIDDEN", forbidden.error["code"])
        missing = self.request("GET", "/api/v1/story-versions/999")
        self.assertEqual(404, missing.status)

    def test_create_session_and_list(self) -> None:
        created = self.request("POST", "/api/v1/reading-sessions", {
            "childProfileId": 1, "storyVersionId": 2, "mode": "PARENT_CHILD",
        })
        self.assertEqual(201, created.status)
        self.assertEqual("CREATED", created.data["status"])
        self.assertEqual(0, created.data["revision"])
        listed = self.request("GET", "/api/v1/reading-sessions")
        self.assertEqual(200, listed.status)
        self.assertGreaterEqual(len(listed.data["items"]), 2)
        self.assertNotIn("total", listed.data)

    def test_create_session_client_request_id_is_idempotent(self) -> None:
        body = {
            "childProfileId": 1, "storyVersionId": 2, "mode": "PARENT_CHILD",
            "clientRequestId": "create-same",
        }
        first = self.request("POST", "/api/v1/reading-sessions", body)
        self.assertEqual(201, first.status)
        replay = self.request("POST", "/api/v1/reading-sessions", body)
        self.assertEqual(200, replay.status)
        self.assertEqual(first.data["readingSessionId"], replay.data["readingSessionId"])
        conflict = self.request("POST", "/api/v1/reading-sessions", {
            "childProfileId": 1, "storyVersionId": 3, "mode": "PARENT_CHILD",
            "clientRequestId": "create-same",
        })
        self.assertEqual(409, conflict.status)
        self.assertEqual("IDEMPOTENCY_CONFLICT", conflict.error["code"])
        corrected = self.request("POST", "/api/v1/reading-sessions", {
            "childProfileId": 1, "storyVersionId": 3, "mode": "PARENT_CHILD",
            "clientRequestId": "create-fixed",
        })
        self.assertEqual(201, corrected.status)
        self.assertNotEqual(first.data["readingSessionId"], corrected.data["readingSessionId"])
        listed = self.request("GET", "/api/v1/reading-sessions")
        self.assertEqual(3, len(listed.data["items"]))
        empty = self.request("POST", "/api/v1/reading-sessions", {
            "childProfileId": 1, "storyVersionId": 2, "mode": "PARENT_CHILD",
            "clientRequestId": "",
        })
        self.assertEqual(422, empty.status)
        self.assertEqual("VALIDATION_ERROR", empty.error["code"])

    def test_session_list_orders_newest_first_before_pagination(self) -> None:
        second = self.request("POST", "/api/v1/reading-sessions", {
            "childProfileId": 1, "storyVersionId": 2, "mode": "PARENT_CHILD",
        })
        third = self.request("POST", "/api/v1/reading-sessions", {
            "childProfileId": 1, "storyVersionId": 3, "mode": "PARENT_CHILD",
        })
        self.assertEqual(201, second.status)
        self.assertEqual(201, third.status)
        first_page = self.request("GET", "/api/v1/reading-sessions?page=1&pageSize=1")
        self.assertEqual(third.data["readingSessionId"], first_page.data["items"][0]["readingSessionId"])
        second_page = self.request("GET", "/api/v1/reading-sessions?page=2&pageSize=1")
        self.assertEqual(second.data["readingSessionId"], second_page.data["items"][0]["readingSessionId"])
        third_page = self.request("GET", "/api/v1/reading-sessions?page=3&pageSize=1")
        self.assertEqual(1, third_page.data["items"][0]["readingSessionId"])

    def test_invalid_page_and_unknown_session(self) -> None:
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "start-page",
        })
        invalid = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 0, "expectedRevision": 1, "clientEventId": "page-zero",
        })
        self.assertEqual(422, invalid.status)
        unavailable = self.request("GET", "/api/v1/reading-sessions/999")
        self.assertEqual(404, unavailable.status)

    def test_finish_and_terminal_state(self) -> None:
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "finish-start",
        })
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "finish-page",
        })
        finished = self.request("POST", "/api/v1/reading-sessions/1/completions", {
            "reason": "USER_FINISH", "expectedRevision": 2, "clientEventId": "finish",
        })
        self.assertEqual(200, finished.status)
        self.assertEqual("FINISHED", finished.data["session"]["status"])
        self.assertEqual(3, finished.data["session"]["revision"])
        self.assertEqual("READY", finished.data["report"]["status"])
        replay = self.request("POST", "/api/v1/reading-sessions/1/completions", {
            "reason": "USER_FINISH", "expectedRevision": 2, "clientEventId": "finish",
        })
        self.assertEqual(200, replay.status)
        self.assertEqual(3, replay.data["session"]["revision"])
        after = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 3, "expectedRevision": 3, "clientEventId": "after-finish",
        })
        self.assertEqual(409, after.status)
        self.assertEqual("SESSION_CONFLICT", after.error["code"])

    def test_deterministic_failure_keeps_idempotency_key(self) -> None:
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "failure-start",
        })
        stale_body = {
            "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 0,
            "clientEventId": "stale-page-key",
        }
        first = self.request("POST", "/api/v1/reading-sessions/1/events", stale_body)
        self.assertEqual(409, first.status)
        self.assertEqual("SESSION_CONFLICT", first.error["code"])
        replay = self.request("POST", "/api/v1/reading-sessions/1/events", stale_body)
        self.assertEqual(409, replay.status)
        self.assertEqual("SESSION_CONFLICT", replay.error["code"])
        corrected_same_key = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1,
            "clientEventId": "stale-page-key",
        })
        self.assertEqual(409, corrected_same_key.status)
        self.assertEqual("IDEMPOTENCY_CONFLICT", corrected_same_key.error["code"])
        after = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(1, after.data["session"]["revision"])
        self.assertEqual(1, after.data["session"]["currentPage"])
        corrected_new_key = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 2, "expectedRevision": 1,
            "clientEventId": "stale-page-key-corrected",
        })
        self.assertEqual(201, corrected_new_key.status)
        self.assertEqual(2, corrected_new_key.data["revision"])

    def test_question_uses_api016_status_and_revision(self) -> None:
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "question-start",
        })
        body = {
            "inputText": "小狐狸为什么带伞？", "pageNumber": 1, "expectedRevision": 1,
            "clientEventId": "question-1",
        }
        answer = self.request("POST", "/api/v1/reading-sessions/1/questions", body)
        self.assertEqual(201, answer.status)
        self.assertEqual(2, answer.data["revision"])
        self.assertEqual("GENERATED", answer.data["status"])
        self.assertIn("Mock", answer.data["outputText"])
        replay = self.request("POST", "/api/v1/reading-sessions/1/questions", body)
        self.assertEqual(200, replay.status)
        self.assertEqual(answer.data["interactionEventId"], replay.data["interactionEventId"])
        current = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(2, current.data["session"]["revision"])
        self.assertEqual(2, len(current.data["events"]))
        wrong_page = self.request("POST", "/api/v1/reading-sessions/1/questions", {
            "inputText": "问题", "pageNumber": 2, "expectedRevision": 2, "clientEventId": "question-2",
        })
        self.assertEqual(409, wrong_page.status)

    def test_voice_scenarios_and_simulation(self) -> None:
        scenarios = self.request("GET", "/mock/voice/scenarios")
        self.assertEqual(200, scenarios.status)
        self.assertEqual(7, len(scenarios.data["states"]))
        self.assertEqual(8, len(scenarios.data["scenarios"][0]["states"]))
        self.assertEqual("IDLE", scenarios.data["scenarios"][0]["finalState"])
        success = self.request("POST", "/mock/voice/simulate", {"scenarioId": "success", "sessionId": 1})
        self.assertEqual(200, success.status)
        self.assertIsNone(success.data["audioUrl"])
        self.assertTrue(success.data["mockPlayback"])
        failed = self.request("POST", "/mock/voice/simulate", {"scenarioId": "asr_failed"})
        self.assertEqual("ERROR_ASR", failed.data["finalState"])
        missing = self.request("POST", "/mock/voice/simulate", {"scenarioId": "does-not-exist"})
        self.assertEqual(404, missing.status)

    def test_voice_failure_recovery_and_no_real_audio(self) -> None:
        scenarios = self.fixture("voice-scenarios.json")["scenarios"]
        for scenario in scenarios:
            self.assertIsNone(scenario.get("audioUrl"))
            if scenario["finalState"].startswith("ERROR_"):
                expected = "SYNTHESIZING" if scenario["scenarioId"] == "tts_failed" else "IDLE"
                self.assertEqual(expected, scenario["recoverTo"])
                self.assertTrue(any(state["state"].startswith("ERROR_") for state in scenario["states"]))
            if scenario.get("tts"):
                self.assertIsNone(scenario["tts"]["audioUrl"])
                self.assertEqual("SIMULATED", scenario["tts"]["playbackMode"])
        tts_failed = next(item for item in scenarios if item["scenarioId"] == "tts_failed")
        self.assertEqual(
            {"RETRY_SYNTHESIS", "TEXT_ONLY"},
            {action["actionId"] for action in tts_failed["recoveryActions"]},
        )
        by_action = {action["actionId"]: action for action in tts_failed["recoveryActions"]}
        self.assertEqual("SYNTHESIZING", by_action["RETRY_SYNTHESIS"]["targetState"])
        self.assertEqual("IDLE", by_action["TEXT_ONLY"]["targetState"])
        for action in by_action.values():
            self.assertTrue(action["requiresSavedInteractionEventId"])
            self.assertFalse(action["rerunQuestion"])
            self.assertFalse(action["rerunRecording"])
            self.assertFalse(action["recordsNewEvent"])
        self.assertEqual(
            {"ERROR_ASR", "ERROR_TIMEOUT", "ERROR_TTS", "ERROR_NETWORK", "ERROR_TOO_SHORT"},
            set(self.fixture("voice-scenarios.json")["errorStates"]),
        )

    def test_tts_retry_reuses_saved_event_without_new_question_or_recording(self) -> None:
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "tts-start",
        })
        answer = self.request("POST", "/api/v1/reading-sessions/1/questions", {
            "inputText": "小狐狸为什么带伞？", "pageNumber": 1, "expectedRevision": 1,
            "clientEventId": "tts-question",
        })
        self.assertEqual(201, answer.status)
        event_id = answer.data["interactionEventId"]
        before = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(2, before.data["session"]["revision"])
        self.assertEqual(2, len(before.data["events"]))
        missing = self.request("POST", "/mock/voice/simulate", {"scenarioId": "tts_failed", "sessionId": 1})
        self.assertEqual(422, missing.status)
        simulation = self.request("POST", "/mock/voice/simulate", {
            "scenarioId": "tts_failed", "sessionId": 1, "interactionEventId": event_id,
        })
        self.assertEqual(200, simulation.status)
        self.assertEqual("ERROR_TTS", simulation.data["finalState"])
        self.assertEqual("SYNTHESIZING", simulation.data["recoverTo"])
        self.assertEqual(event_id, simulation.data["interactionEventId"])
        self.assertIsNone(simulation.data["audioUrl"])
        self.assertTrue(simulation.data["mockPlayback"])
        self.assertTrue(simulation.data["answerText"])
        actions = {action["actionId"]: action for action in simulation.data["recoveryActions"]}
        self.assertEqual("SYNTHESIZING", actions["RETRY_SYNTHESIS"]["targetState"])
        self.assertEqual("IDLE", actions["TEXT_ONLY"]["targetState"])
        self.assertFalse(actions["RETRY_SYNTHESIS"]["rerunQuestion"])
        self.assertFalse(actions["RETRY_SYNTHESIS"]["rerunRecording"])
        self.assertFalse(actions["RETRY_SYNTHESIS"]["recordsNewEvent"])
        after = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(2, after.data["session"]["revision"])
        self.assertEqual(2, len(after.data["events"]))

    def test_error_injection(self) -> None:
        auth = self.request("GET", "/api/v1/health", headers={"X-Mock-Error": "AUTH_REQUIRED"})
        self.assertEqual(401, auth.status)
        self.assertEqual("AUTH_REQUIRED", auth.error["code"])
        conflict = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "forced",
        }, headers={"X-Mock-Error": "SESSION_CONFLICT"})
        self.assertEqual(409, conflict.status)
        unknown = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "unknown",
        }, headers={"X-Mock-Error": "REQUEST_OUTCOME_UNKNOWN"})
        self.assertEqual(504, unknown.status)
        self.assertFalse(unknown.error["retryable"])
        dep = self.request("GET", "/api/v1/health", headers={"X-Mock-Error": "DEPENDENCY_UNAVAILABLE"})
        self.assertEqual(503, dep.status)
        self.assertTrue(dep.error["retryable"])
        unknown_id = self.request("GET", "/api/v1/health", headers={"X-Mock-Error": "NOT_A_SCENARIO"})
        self.assertEqual(422, unknown_id.status)

    def test_dependency_503_retryable_differs_for_read_and_write(self) -> None:
        read = self.request("GET", "/api/v1/storybooks", headers={"X-Mock-Error": "DEPENDENCY_UNAVAILABLE"})
        self.assertEqual(503, read.status)
        self.assertEqual("DEPENDENCY_UNAVAILABLE", read.error["code"])
        self.assertTrue(read.error["retryable"])
        write = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0,
            "clientEventId": "write-503",
        }, headers={"X-Mock-Error": "DEPENDENCY_UNAVAILABLE"})
        self.assertEqual(503, write.status)
        self.assertEqual("DEPENDENCY_UNAVAILABLE", write.error["code"])
        self.assertFalse(write.error["retryable"])
        after = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(0, after.data["session"]["revision"])
        self.assertEqual([], after.data["session"]["visitedPageNumbers"])

    def test_question_504_then_session_read_recovers(self) -> None:
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "504-start",
        })
        body = {
            "inputText": "小狐狸为什么带伞？", "pageNumber": 1, "expectedRevision": 1,
            "clientEventId": "504-question",
        }
        unknown = self.request(
            "POST", "/api/v1/reading-sessions/1/questions", body,
            headers={"X-Mock-Error": "REQUEST_OUTCOME_UNKNOWN"},
        )
        self.assertEqual(504, unknown.status)
        self.assertEqual("REQUEST_OUTCOME_UNKNOWN", unknown.error["code"])
        self.assertFalse(unknown.error["retryable"])
        current = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(1, current.data["session"]["revision"])
        self.assertEqual(1, len(current.data["events"]))
        retry = self.request("POST", "/api/v1/reading-sessions/1/questions", {
            **body, "clientEventId": "504-question-retry",
        })
        self.assertEqual(201, retry.status)
        self.assertEqual(2, retry.data["revision"])

    def test_payload_injection_and_cover_missing(self) -> None:
        empty = self.request("GET", "/api/v1/storybooks?mockError=NO_BOOKS")
        self.assertEqual(200, empty.status)
        self.assertEqual([], empty.data["items"])
        self.assertNotIn("total", empty.data)
        missing = self.request("GET", "/api/v1/storybooks?mockCover=missing")
        self.assertIsNone(missing.data["items"][0]["localCover"])
        missing_detail = self.request("GET", "/api/v1/story-versions/2?mockCover=missing")
        self.assertIsNone(missing_detail.data["localCover"])

    def test_reset_restores_initial_session(self) -> None:
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "reset-start",
        })
        reset = self.request("POST", "/mock/reset", {})
        self.assertEqual(200, reset.status)
        current = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual("CREATED", current.data["session"]["status"])
        self.assertEqual(0, current.data["session"]["revision"])

    def test_concurrent_identical_started_is_idempotent(self) -> None:
        body = {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0,
            "clientEventId": "concurrent-start",
        }
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.request(
                "POST", "/api/v1/reading-sessions/1/events", body
            ), range(8)))
        statuses = [result.status for result in results]
        self.assertEqual(1, statuses.count(201))
        self.assertEqual(7, statuses.count(200))
        self.assertEqual({1}, {result.data["revision"] for result in results})
        current = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(1, current.data["session"]["revision"])
        self.assertEqual([1], current.data["session"]["visitedPageNumbers"])
        self.assertEqual(1, len(current.data["events"]))

    def test_concurrent_reads_lists_and_reset_are_consistent(self) -> None:
        def worker(index: int) -> HttpResult:
            mode = index % 4
            if mode == 0:
                return self.request("POST", "/mock/reset", {})
            if mode == 1:
                return self.request("GET", "/api/v1/reading-sessions/1")
            if mode == 2:
                return self.request("GET", "/api/v1/reading-sessions")
            return self.request("GET", "/api/v1/storybooks")

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(worker, range(24)))
        for result in results:
            self.assertEqual(200, result.status)
            self.assertIsInstance(result.data, dict)
            if "session" in result.data:
                self.assertIn("readingSessionId", result.data["session"])
                self.assertIn("revision", result.data["session"])
            if "items" in result.data:
                self.assertIsInstance(result.data["items"], list)
                if result.data["items"] and "storybookId" in result.data["items"][0]:
                    self.assertEqual(20, len(result.data["items"]))
        self.assertEqual(20, len(self.request("GET", "/api/v1/storybooks").data["items"]))

    def test_concurrent_snapshots_stay_coherent_during_reset(self) -> None:
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "snapshot-start",
        })

        def worker(index: int) -> HttpResult:
            mode = index % 6
            if mode == 0:
                return self.request("POST", "/mock/reset", {})
            if mode == 1:
                return self.request("GET", "/api/v1/reading-sessions/1")
            if mode == 2:
                return self.request("GET", "/api/v1/reading-sessions")
            if mode == 3:
                return self.request("GET", "/mock/meta")
            if mode == 4:
                return self.request("GET", "/mock/voice/scenarios")
            return self.request("GET", "/mock/errors")

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(worker, range(60)))
        for result in results:
            self.assertEqual(200, result.status)
            self.assertIsInstance(result.data, dict)
            session = result.data.get("session")
            if session:
                self.assertIn(session["status"], {"CREATED", "IN_PROGRESS", "FINISHED"})
                self.assertGreaterEqual(session["revision"], 0)
                self.assertEqual(
                    len(session["visitedPageNumbers"]),
                    len(set(session["visitedPageNumbers"])),
                )
                if session["status"] == "CREATED":
                    self.assertEqual(0, session["revision"])
                    self.assertEqual([], session["visitedPageNumbers"])
                else:
                    self.assertIn(session["currentPage"], session["visitedPageNumbers"])
                    self.assertGreaterEqual(session["revision"], len(session["visitedPageNumbers"]))
            items = result.data.get("items")
            if items and isinstance(items[0], dict) and "readingSessionId" in items[0]:
                for item in items:
                    self.assertEqual(set(["readingSessionId", "storyVersionId", "currentPage",
                                          "visitedPageNumbers", "mode", "status", "revision",
                                          "expiresAt", "demoOnly"]), set(item))
            if "bookCount" in result.data:
                self.assertEqual(20, result.data["bookCount"])
                self.assertEqual(104, result.data["pageCount"])
            if "states" in result.data:
                self.assertEqual(7, len(result.data["states"]))
                self.assertEqual(8, len(result.data["scenarios"]))
            elif "scenarios" in result.data and "errorScenarioCount" not in result.data:
                self.assertEqual(13, len(result.data["scenarios"]))
        final = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(200, final.status)
        self.assertIn(final.data["session"]["status"], {"CREATED", "IN_PROGRESS"})

    def test_concurrent_create_sessions_return_coherent_snapshots(self) -> None:
        def create(_: int) -> HttpResult:
            return self.request("POST", "/api/v1/reading-sessions", {
                "childProfileId": 1, "storyVersionId": 2, "mode": "PARENT_CHILD",
            })

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(create, range(16)))
        ids = set()
        for result in results:
            self.assertEqual(201, result.status)
            self.assertEqual("CREATED", result.data["status"])
            self.assertEqual(2, result.data["storyVersionId"])
            self.assertEqual(1, result.data["currentPage"])
            self.assertEqual(0, result.data["revision"])
            self.assertEqual([], result.data["visitedPageNumbers"])
            ids.add(result.data["readingSessionId"])
        self.assertEqual(16, len(ids))
        listed = self.request("GET", "/api/v1/reading-sessions?pageSize=50")
        self.assertEqual(200, listed.status)
        self.assertGreaterEqual(len(listed.data["items"]), 17)


if __name__ == "__main__":
    unittest.main(verbosity=2)
