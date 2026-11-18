"""Offline tests for the H05 fixture builder and standard-library Mock server.

Run from the repository root:
    python -m unittest discover -s hardware/tests/mock -v

The tests use temporary directories and a loopback HTTP server. They do not
require Pillow, a real ESP32 board, a cloud AI service or the production
backend.
"""

from __future__ import annotations

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
        cls.opener = build_opener(ProxyHandler({}))

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
            with self.opener.open(request, timeout=5) as response:
                return HttpResult(response.status, json.loads(response.read().decode("utf-8")), dict(response.headers))
        except HTTPError as exc:
            return HttpResult(exc.code, json.loads(exc.read().decode("utf-8")), dict(exc.headers))

    def fixture(self, name: str) -> dict:
        return json.loads((self.fixtures_dir / name).read_text(encoding="utf-8"))

    # ---------------- data conversion ----------------

    def test_fixture_counts_and_draft_flags(self) -> None:
        books = self.fixture("storybooks.json")["items"]
        details = self.fixture("story-details.json")["items"]
        self.assertEqual(20, len(books))
        self.assertEqual(20, len(details))
        self.assertEqual(104, sum(len(item["pages"]) for item in details))
        self.assertEqual({item["storybookId"] for item in books}, set(range(2, 22)))
        self.assertTrue(all(item["status"] == "DRAFT" for item in books))
        self.assertTrue(all(item["coverRef"] is None for item in books))
        self.assertTrue(all(page["imageRef"] is None for item in details for page in item["pages"]))
        self.assertEqual(20, len({item["storybookId"] for item in books}))

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

    def test_source_files_have_not_changed(self) -> None:
        fixture = self.fixture("storybooks.json")
        self.assertEqual(5, len(fixture["sourceFiles"]))
        for entry in fixture["sourceFiles"]:
            self.assertEqual(entry["sha256"], sha256_file(REPO_ROOT / entry["path"]))

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

    def test_reading_session_start_forward_back_and_last_page(self) -> None:
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
        back = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 1, "expectedRevision": 2, "clientEventId": "back-1",
        })
        self.assertEqual(201, back.status)
        self.assertEqual(3, back.data["revision"])
        bad = self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "PAGE_CHANGED", "pageNumber": 6, "expectedRevision": 3, "clientEventId": "skip-6",
        })
        self.assertEqual(409, bad.status)
        self.assertEqual("SESSION_CONFLICT", bad.error["code"])

    def test_idempotent_replay_and_conflict(self) -> None:
        body = {"eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "same"}
        first = self.request("POST", "/api/v1/reading-sessions/1/events", body)
        self.assertEqual(201, first.status)
        replay = self.request("POST", "/api/v1/reading-sessions/1/events", body)
        self.assertEqual(201, replay.status)
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

    def test_question_does_not_change_revision_and_is_mock_marked(self) -> None:
        self.request("POST", "/api/v1/reading-sessions/1/events", {
            "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0, "clientEventId": "question-start",
        })
        answer = self.request("POST", "/api/v1/reading-sessions/1/questions", {
            "inputText": "小狐狸为什么带伞？", "pageNumber": 1, "expectedRevision": 1,
            "clientEventId": "question-1",
        })
        self.assertEqual(200, answer.status)
        self.assertEqual("GENERATED", answer.data["status"])
        self.assertIn("Mock", answer.data["outputText"])
        current = self.request("GET", "/api/v1/reading-sessions/1")
        self.assertEqual(1, current.data["session"]["revision"])
        wrong_page = self.request("POST", "/api/v1/reading-sessions/1/questions", {
            "inputText": "问题", "pageNumber": 2, "expectedRevision": 1, "clientEventId": "question-2",
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
                self.assertEqual("IDLE", scenario["recoverTo"])
                self.assertTrue(any(state["state"].startswith("ERROR_") for state in scenario["states"]))
            if scenario.get("tts"):
                self.assertIsNone(scenario["tts"]["audioUrl"])
                self.assertEqual("SIMULATED", scenario["tts"]["playbackMode"])
        self.assertEqual(
            {"ERROR_ASR", "ERROR_TIMEOUT", "ERROR_TTS", "ERROR_NETWORK", "ERROR_TOO_SHORT"},
            set(self.fixture("voice-scenarios.json")["errorStates"]),
        )

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

    def test_payload_injection_and_cover_missing(self) -> None:
        empty = self.request("GET", "/api/v1/storybooks?mockError=NO_BOOKS")
        self.assertEqual(200, empty.status)
        self.assertEqual([], empty.data["items"])
        self.assertEqual(0, empty.data["total"])
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
