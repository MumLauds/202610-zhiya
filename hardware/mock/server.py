#!/usr/bin/env python3
"""H05: lightweight, standard-library HTTP mock for the hardware DEMO flow.

The server intentionally has no account system, no cookies, no device token and
no cloud AI call. It serves deterministic local fixtures and keeps reading
sessions in memory. Every response is marked as a local Mock response; it must
not be treated as a real backend integration result.

Usage:
    python hardware/mock/server.py
    python hardware/mock/server.py --host 127.0.0.1 --port 8765
    python hardware/mock/server.py --fixtures hardware/mock/fixtures
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import json
import re
import sys
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURES_DIR = REPO_ROOT / "hardware" / "mock" / "fixtures"
FIXTURE_NAMES = (
    "storybooks.json",
    "story-details.json",
    "demo-session.json",
    "voice-scenarios.json",
    "error-scenarios.json",
)
MAX_BODY_BYTES = 64 * 1024
DEMO_PROFILE_ID = 1
DEMO_NOTICE = "本地 DEMO / Mock 响应，不是真实后端记录。"


class MockFixtureError(RuntimeError):
    """Fixtures are missing or malformed."""


class ApiError(Exception):
    """A controlled API error returned by the mock."""

    def __init__(self, status: int, code: str, message: str, retryable: bool = False,
                 retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass
class SessionState:
    reading_session_id: int
    story_version_id: int
    mode: str = "PARENT_CHILD"
    current_page: int = 1
    visited_pages: list[int] = field(default_factory=list)
    status: str = "CREATED"
    revision: int = 0
    expires_at: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    idempotency: dict[str, dict[str, Any]] = field(default_factory=dict)
    report: dict[str, Any] | None = None
    next_event_id: int = 1

    def session_object(self) -> dict[str, Any]:
        return {
            "readingSessionId": self.reading_session_id,
            "storyVersionId": self.story_version_id,
            "currentPage": self.current_page,
            "visitedPageNumbers": list(self.visited_pages),
            "mode": self.mode,
            "status": self.status,
            "revision": self.revision,
            "expiresAt": self.expires_at,
            "demoOnly": True,
        }


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MockFixtureError(f"缺少 fixture: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MockFixtureError(f"fixture 无法解析: {path}: {exc}") from exc


def request_digest(kind: str, body: dict[str, Any]) -> str:
    canonical = {"kind": kind, "body": body}
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def request_id() -> str:
    return "mock-" + uuid.uuid4().hex[:12]


def error_payload(code: str, message: str, retryable: bool, request: str | None = None) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "retryable": retryable},
        "requestId": request or request_id(),
    }


def success_payload(data: Any, request: str | None = None) -> dict[str, Any]:
    return {"data": data, "requestId": request or request_id()}


def public_event(event: dict[str, Any]) -> dict[str, Any]:
    """Project an internal event onto the API-013 public shape."""
    return {
        "interactionEventId": event["interactionEventId"],
        "eventType": event["eventType"],
        "pageNumber": event["pageNumber"],
    }


def load_fixtures(fixtures_dir: Path) -> dict[str, Any]:
    fixtures_dir = fixtures_dir.resolve()
    paths = {name: fixtures_dir / name for name in FIXTURE_NAMES}
    for path in paths.values():
        if not path.is_file():
            raise MockFixtureError(f"缺少 fixture: {path}")
    loaded = {name: read_json(path) for name, path in paths.items()}

    books = loaded["storybooks.json"]
    details = loaded["story-details.json"]
    session_fixture = loaded["demo-session.json"]
    voice = loaded["voice-scenarios.json"]
    errors = loaded["error-scenarios.json"]

    if not isinstance(books.get("items"), list) or len(books["items"]) != 20:
        raise MockFixtureError("storybooks.json 必须包含 20 本原创绘本")
    for item in books["items"]:
        if item.get("status") != "PUBLISHED" or item.get("demoOnly") is not True:
            raise MockFixtureError("storybooks.json 必须是 PUBLISHED 且 demoOnly=true 的 DEMO 投影")
    if any("pages" in item or "branches" in item for item in books["items"]):
        raise MockFixtureError("storybooks.json 只允许轻量书架元数据，不得包含 pages/branches")
    if not isinstance(details.get("items"), list) or len(details["items"]) != 20:
        raise MockFixtureError("story-details.json 必须包含 20 本详情")
    if not isinstance(voice.get("scenarios"), list):
        raise MockFixtureError("voice-scenarios.json 缺少 scenarios")
    if not isinstance(errors.get("scenarios"), list):
        raise MockFixtureError("error-scenarios.json 缺少 scenarios")
    if session_fixture.get("profile", {}).get("childProfileId") != DEMO_PROFILE_ID:
        raise MockFixtureError("demo-session 必须使用 childProfileId=1")

    by_version = {item["storyVersionId"]: item for item in details["items"]}
    if len(by_version) != 20:
        raise MockFixtureError("storyVersionId 必须唯一")
    total_pages = sum(len(item.get("pages", [])) for item in details["items"])
    if total_pages != 104:
        raise MockFixtureError(f"详情页数必须为 104，实际 {total_pages}")
    cover_count = sum(
        2 for item in details["items"]
        if item.get("localCover", {}).get("thumbnail") and item.get("localCover", {}).get("detail")
    )
    if cover_count != 40:
        raise MockFixtureError("H03 资源引用必须为 40 个")
    main_states = voice.get("states")
    if main_states != ["IDLE", "RECORDING", "UPLOADING", "RECOGNIZING", "THINKING", "SYNTHESIZING", "PLAYING"]:
        raise MockFixtureError("voice 主状态必须与 H04 的 7 个状态一致")
    scenario_ids = {item.get("scenarioId") for item in voice["scenarios"]}
    if "success" not in scenario_ids:
        raise MockFixtureError("voice-scenarios 缺少 success")
    error_ids = {item.get("scenarioId") for item in errors["scenarios"]}
    if not {"AUTH_REQUIRED", "SESSION_CONFLICT", "REQUEST_OUTCOME_UNKNOWN", "NO_BOOKS"} <= error_ids:
        raise MockFixtureError("error-scenarios 缺少关键场景")
    unknown = next(item for item in errors["scenarios"] if item["scenarioId"] == "REQUEST_OUTCOME_UNKNOWN")
    if not any(pattern.endswith("/questions") for pattern in unknown.get("appliesTo", [])):
        raise MockFixtureError("REQUEST_OUTCOME_UNKNOWN 必须覆盖 questions 路由")
    dependency = next(item for item in errors["scenarios"] if item["scenarioId"] == "DEPENDENCY_UNAVAILABLE")
    if dependency.get("retryableByMethod", {}).get("POST") is not False:
        raise MockFixtureError("DEPENDENCY_UNAVAILABLE 的 POST retryable 必须为 false")
    if dependency.get("retryableByMethod", {}).get("GET") is not True:
        raise MockFixtureError("DEPENDENCY_UNAVAILABLE 的 GET retryable 必须为 true")
    tts = next(item for item in voice["scenarios"] if item.get("scenarioId") == "tts_failed")
    actions = {action.get("actionId") for action in tts.get("recoveryActions", [])}
    if actions != {"RETRY_SYNTHESIS", "TEXT_ONLY"} or tts.get("recoverTo") != "SYNTHESIZING":
        raise MockFixtureError("tts_failed 必须区分重试合成与只看文字")

    initial_session = session_fixture.get("session", {})
    if initial_session.get("storyVersionId") not in by_version:
        raise MockFixtureError("demo-session 的 storyVersionId 不在 fixture 中")
    return {
        "books": books,
        "details": details,
        "details_by_version": by_version,
        "demo": session_fixture,
        "voice": voice,
        "errors": errors,
        "errors_by_id": {item["scenarioId"]: item for item in errors["scenarios"]},
        "fixtures_dir": fixtures_dir,
    }


class MockState:
    def __init__(self, fixtures_dir: Path) -> None:
        self.fixtures = load_fixtures(fixtures_dir)
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.books = copy.deepcopy(self.fixtures["books"]["items"])
            self.details = copy.deepcopy(self.fixtures["details"]["items"])
            self.details_by_version = {item["storyVersionId"]: item for item in self.details}
            self.voice = copy.deepcopy(self.fixtures["voice"])
            self.error_scenarios = copy.deepcopy(self.fixtures["errors"])
            self.error_by_id = {item["scenarioId"]: item for item in self.error_scenarios["scenarios"]}
            initial = copy.deepcopy(self.fixtures["demo"]["session"])
            self.next_session_id = max(initial["readingSessionId"] + 1, 2)
            self.create_idempotency: dict[str, dict[str, Any]] = {}
            self.sessions: dict[int, SessionState] = {}
            self.sessions[initial["readingSessionId"]] = SessionState(
                reading_session_id=initial["readingSessionId"],
                story_version_id=initial["storyVersionId"],
                current_page=initial["currentPage"],
                visited_pages=list(initial["visitedPageNumbers"]),
                status=initial["status"],
                revision=initial["revision"],
            )

    def version_detail(self, version_id: int) -> dict[str, Any]:
        with self.lock:
            detail = self.details_by_version.get(version_id)
            if detail is None:
                raise ApiError(404, "RESOURCE_NOT_FOUND", "绘本不可用")
            return copy.deepcopy(detail)

    def books_snapshot(self) -> list[dict[str, Any]]:
        """Return a deep copy of the lightweight shelf records."""
        with self.lock:
            return copy.deepcopy(self.books)

    def meta_snapshot(self) -> dict[str, int]:
        """Return all counters from one locked state generation."""
        with self.lock:
            return {
                "bookCount": len(self.books),
                "detailCount": len(self.details),
                "pageCount": sum(len(item["pages"]) for item in self.details),
                "voiceScenarioCount": len(self.voice["scenarios"]),
                "errorScenarioCount": len(self.error_scenarios["scenarios"]),
            }

    def sessions_snapshot(self) -> list[dict[str, Any]]:
        """Return one locked snapshot ordered newest-first, before pagination."""
        with self.lock:
            return [
                item.session_object()
                for item in sorted(
                    self.sessions.values(),
                    key=lambda session: session.reading_session_id,
                    reverse=True,
                )
            ]

    def session_snapshot(self, session_id: int) -> dict[str, Any]:
        """Return session + public events + report from one locked generation."""
        with self.lock:
            session = self.session_for_update(session_id)
            return {
                "session": session.session_object(),
                "events": [public_event(event) for event in session.events],
                "report": copy.deepcopy(session.report),
            }

    def create_session(self, profile_id: int, version_id: int, client_request_id: str | None = None,
                       digest: str | None = None) -> tuple[dict[str, Any], int]:
        """Create a session, replaying a saved API-011 request when keyed."""
        with self.lock:
            if client_request_id is not None:
                previous = self.create_idempotency.get(client_request_id)
                if previous is not None:
                    if previous["digest"] != digest:
                        raise ApiError(409, "IDEMPOTENCY_CONFLICT", "请求标识已用于其他操作")
                    return copy.deepcopy(previous["data"]), 200
            if profile_id != DEMO_PROFILE_ID:
                raise ApiError(404, "RESOURCE_NOT_FOUND", "演示档案不可用")
            if version_id not in self.details_by_version:
                raise ApiError(404, "RESOURCE_NOT_FOUND", "绘本不可用")
            session_id = self.next_session_id
            self.next_session_id += 1
            session = SessionState(reading_session_id=session_id, story_version_id=version_id)
            self.sessions[session_id] = session
            data = session.session_object()
            if client_request_id is not None:
                self.create_idempotency[client_request_id] = {
                    "digest": digest,
                    "data": copy.deepcopy(data),
                }
            return data, 201

    def session_for_update(self, session_id: int) -> SessionState:
        """Return a live session for a write transaction.

        Callers must hold ``self.lock`` for the whole read-validate-mutate
        sequence. The method re-enters the RLock so accidental nesting is safe,
        but it never hands a live session to a read-only HTTP response.
        """
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                raise ApiError(404, "RESOURCE_NOT_FOUND", "会话不可用")
            return session

    def has_session(self, session_id: int) -> bool:
        with self.lock:
            return session_id in self.sessions

    def has_ask_event(self, session_id: int, event_id: int) -> bool:
        """Check a saved ASK event under the same lock as session reads."""
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return False
            return any(
                event.get("interactionEventId") == event_id and event.get("eventType") == "ASK"
                for event in session.events
            )

    def error_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        with self.lock:
            scenario = self.error_by_id.get(scenario_id)
            return copy.deepcopy(scenario) if scenario is not None else None

    def errors_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.error_scenarios)

    def voice_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.voice)

    def voice_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        with self.lock:
            scenario = next(
                (item for item in self.voice["scenarios"] if item["scenarioId"] == scenario_id),
                None,
            )
            return copy.deepcopy(scenario) if scenario is not None else None


class MockHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], fixtures_dir: Path, quiet: bool = False) -> None:
        self.state = MockState(fixtures_dir)
        self.quiet = quiet
        super().__init__(address, MockRequestHandler)


class MockRequestHandler(BaseHTTPRequestHandler):
    server: MockHTTPServer
    server_version = "ZhiyaMock/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        if not self.server.quiet:
            print("[MOCK] " + (fmt % args), file=sys.stderr)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch("POST")

    def _read_body(self) -> dict[str, Any]:
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text or "0")
        except ValueError as exc:
            raise ApiError(422, "VALIDATION_ERROR", "Content-Length 不合法") from exc
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "VALIDATION_ERROR", "请求体过大")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(422, "VALIDATION_ERROR", "请求体必须是 UTF-8 JSON 对象") from exc
        if not isinstance(body, dict):
            raise ApiError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象")
        return body

    def _drain_body(self) -> None:
        """Consume an unused POST body so Windows does not abort the connection."""
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return
        if length > MAX_BODY_BYTES:
            return
        if length > 0:
            self.rfile.read(length)

    def _dispatch(self, method: str) -> None:
        self.request_id = request_id()
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        self.query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        try:
            scenario = self._injected_scenario(path)
            if scenario and scenario.get("mode") == "error":
                if method == "POST":
                    self._drain_body()
                self._send_error_from_scenario(scenario)
                return

            if method == "GET" and path == "/api/v1/health":
                self._health()
            elif method == "GET" and path == "/mock/meta":
                self._meta()
            elif method == "GET" and path == "/api/v1/storybooks":
                self._storybooks(scenario)
            elif method == "GET" and re.fullmatch(r"/api/v1/story-versions/\d+", path):
                self._story_detail(int(path.rsplit("/", 1)[1]), scenario)
            elif method == "GET" and path == "/api/v1/reading-sessions":
                self._list_sessions()
            elif method == "POST" and path == "/api/v1/reading-sessions":
                self._create_session()
            elif method == "GET" and re.fullmatch(r"/api/v1/reading-sessions/\d+", path):
                self._get_session(int(path.rsplit("/", 1)[1]))
            elif method == "POST" and re.fullmatch(r"/api/v1/reading-sessions/\d+/events", path):
                self._event(int(path.split("/")[-2]))
            elif method == "POST" and re.fullmatch(r"/api/v1/reading-sessions/\d+/completions", path):
                self._completion(int(path.split("/")[-2]))
            elif method == "POST" and re.fullmatch(r"/api/v1/reading-sessions/\d+/questions", path):
                self._question(int(path.split("/")[-2]))
            elif method == "GET" and path == "/mock/voice/scenarios":
                self._voice_scenarios()
            elif method == "GET" and re.fullmatch(r"/mock/voice/scenarios/[A-Za-z0-9_-]+", path):
                self._voice_scenario(path.rsplit("/", 1)[1])
            elif method == "POST" and path == "/mock/voice/simulate":
                self._voice_simulate()
            elif method == "POST" and path == "/mock/reset":
                self._reset()
            elif method == "GET" and path == "/mock/errors":
                self._send_json(200, success_payload(self.server.state.errors_snapshot(), self.request_id))
            else:
                if method == "POST":
                    self._drain_body()
                raise ApiError(404, "RESOURCE_NOT_FOUND", "Mock 路径不存在")
        except ApiError as exc:
            self._send_api_error(exc)
        except Exception as exc:  # defensive boundary; never expose stack traces to clients
            print(f"[MOCK] internal error: {exc!r}", file=sys.stderr)
            self._send_api_error(ApiError(500, "MOCK_INTERNAL_ERROR", "Mock 内部错误"))

    def _injected_scenario(self, path: str) -> dict[str, Any] | None:
        scenario_id = self.headers.get("X-Mock-Error") or self.query.get("mockError")
        if not scenario_id:
            return None
        scenario = self.server.state.error_scenario(scenario_id)
        if scenario is None:
            raise ApiError(422, "VALIDATION_ERROR", f"未知 Mock 错误场景: {scenario_id}")
        if not any(fnmatch.fnmatch(path, pattern) for pattern in scenario.get("appliesTo", [])):
            return None
        return scenario

    def _send_error_from_scenario(self, scenario: dict[str, Any]) -> None:
        retryable = scenario.get("retryable", False)
        by_method = scenario.get("retryableByMethod") or {}
        if self.command in by_method:
            retryable = by_method[self.command]
        self._send_json(
            int(scenario["status"]),
            error_payload(
                scenario["code"],
                scenario["message"],
                bool(retryable),
                self.request_id,
            ),
            {"Retry-After": str(scenario["retryAfter"])} if scenario.get("retryAfter") else None,
        )

    def _remember_failure(self, session: SessionState, client_event_id: str, digest: str,
                          kind: str, exc: ApiError) -> None:
        """Persist a deterministic failed request for same-key replay."""
        session.idempotency[client_event_id] = {
            "digest": digest,
            "kind": kind,
            "outcome": "error",
            "status": exc.status,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "retryAfter": exc.retry_after,
            },
        }

    @staticmethod
    def _remember_success(session: SessionState, client_event_id: str, digest: str, kind: str,
                          first_status: int, data: dict[str, Any]) -> None:
        """Save a successful response and the separate replay status."""
        session.idempotency[client_event_id] = {
            "digest": digest,
            "kind": kind,
            "outcome": "success",
            "status": first_status,
            "replayStatus": 200,
            "data": data,
        }

    def _replay_payload(self, replay: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if replay.get("outcome") == "error":
            error = replay["error"]
            return int(replay["status"]), error_payload(
                error["code"], error["message"], bool(error["retryable"]), self.request_id
            )
        data = copy.deepcopy(replay["data"])
        data["requestId"] = self.request_id
        return int(replay.get("replayStatus", replay["status"])), data

    def _send_api_error(self, exc: ApiError) -> None:
        self._send_json(
            exc.status,
            error_payload(exc.code, exc.message, exc.retryable, self.request_id),
            {"Retry-After": str(exc.retry_after)} if exc.retry_after else None,
        )

    def _send_json(self, status: int, payload: Any, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Mock-Server", "zhiya-h05")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _meta(self) -> None:
        counts = self.server.state.meta_snapshot()
        self._send_json(200, success_payload({
            "mode": "DEMO",
            "mock": True,
            "notice": DEMO_NOTICE,
            **counts,
        }, self.request_id))

    def _health(self) -> None:
        self._send_json(200, success_payload({
            "status": "ok",
            "mode": "DEMO",
            "mock": True,
            "notice": DEMO_NOTICE,
        }, self.request_id))

    def _storybooks(self, scenario: dict[str, Any] | None) -> None:
        if self.query.get("scope") == "mine":
            raise ApiError(403, "FORBIDDEN", "Mock 未实现家长历史书架")
        items = self.server.state.books_snapshot()
        age_band = self.query.get("ageBand")
        topic = self.query.get("topic")
        if age_band:
            items = [item for item in items if item.get("ageBand") == age_band]
        if topic:
            items = [item for item in items if topic in item.get("topics", [])]
        if self.query.get("mockCover") == "missing" or (scenario and scenario.get("action") == "stripLocalCover"):
            for item in items:
                item["coverRef"] = None
                item["localCover"] = None
        if scenario and scenario.get("mode") == "payload" and scenario.get("scenarioId") == "NO_BOOKS":
            items = []
        page = self._query_int("page", 1, 1, 9007199254740991)
        page_size = self._query_int("pageSize", 20, 1, 50)
        offset = (page - 1) * page_size
        sliced = items[offset:offset + page_size]
        self._send_json(200, success_payload({
            "items": sliced,
            "page": page,
            "pageSize": page_size,
        }, self.request_id))

    def _story_detail(self, version_id: int, scenario: dict[str, Any] | None) -> None:
        detail = self.server.state.version_detail(version_id)
        if self.query.get("mockCover") == "missing" or (scenario and scenario.get("action") == "stripLocalCover"):
            detail["coverRef"] = None
            detail["localCover"] = None
        self._send_json(200, success_payload(detail, self.request_id))

    def _query_int(self, name: str, default: int, minimum: int, maximum: int) -> int:
        raw = self.query.get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ApiError(422, "VALIDATION_ERROR", f"{name} 必须是整数") from exc
        if not minimum <= value <= maximum:
            raise ApiError(422, "VALIDATION_ERROR", f"{name} 超出范围")
        return value

    def _create_session(self) -> None:
        body = self._read_body()
        allowed = {"childProfileId", "storyVersionId", "mode", "clientRequestId"}
        if set(body) - allowed:
            raise ApiError(422, "VALIDATION_ERROR", "包含未知字段")
        profile_id = body.get("childProfileId")
        if not isinstance(profile_id, int) or isinstance(profile_id, bool):
            raise ApiError(422, "VALIDATION_ERROR", "childProfileId 必须是整数")
        version_id = body.get("storyVersionId")
        if not isinstance(version_id, int) or isinstance(version_id, bool) or version_id < 1:
            raise ApiError(422, "VALIDATION_ERROR", "storyVersionId 不合法")
        if body.get("mode") != "PARENT_CHILD":
            raise ApiError(422, "VALIDATION_ERROR", "Mock 首版只支持 PARENT_CHILD")
        client_request_id = None
        if "clientRequestId" in body:
            value = body["clientRequestId"]
            if not isinstance(value, str) or not 1 <= len(value) <= 64:
                raise ApiError(422, "VALIDATION_ERROR", "clientRequestId 必须为 1～64 字符")
            client_request_id = value
        digest = request_digest("create_session", body) if client_request_id is not None else None
        session_payload, status = self.server.state.create_session(
            profile_id, version_id, client_request_id, digest
        )
        self._send_json(status, success_payload(session_payload, self.request_id))

    def _list_sessions(self) -> None:
        page = self._query_int("page", 1, 1, 9007199254740991)
        page_size = self._query_int("pageSize", 20, 1, 50)
        sessions = self.server.state.sessions_snapshot()
        offset = (page - 1) * page_size
        self._send_json(200, success_payload({
            "items": sessions[offset:offset + page_size],
            "page": page,
            "pageSize": page_size,
        }, self.request_id))

    def _get_session(self, session_id: int) -> None:
        self._send_json(200, success_payload(
            self.server.state.session_snapshot(session_id),
            self.request_id,
        ))

    def _event(self, session_id: int) -> None:
        body = self._read_body()
        allowed = {"eventType", "pageNumber", "expectedRevision", "clientEventId"}
        if set(body) - allowed:
            raise ApiError(422, "VALIDATION_ERROR", "包含未知字段")
        event_type = body.get("eventType")
        if event_type not in ("STARTED", "PAGE_CHANGED"):
            raise ApiError(422, "VALIDATION_ERROR", "eventType 只支持 STARTED/PAGE_CHANGED")
        page_number = self._body_int(body, "pageNumber", 1)
        expected_revision = self._body_int(body, "expectedRevision", 0)
        client_event_id = self._client_event_id(body)

        replay_response: tuple[int, dict[str, Any]] | None = None
        response: Any = None
        with self.server.state.lock:
            session = self.server.state.session_for_update(session_id)
            digest = request_digest("events", body)
            replay = self._replay(session, client_event_id, digest)
            if replay is not None:
                replay_response = self._replay_payload(replay)
            else:
                try:
                    if session.status == "FINISHED":
                        raise ApiError(409, "SESSION_CONFLICT", "本次阅读已结束")
                    if expected_revision != session.revision:
                        raise ApiError(409, "SESSION_CONFLICT", "会话修订号已变化，请先读取会话")
                    if event_type == "STARTED":
                        if session.status != "CREATED" or page_number != 1 or expected_revision != 0:
                            raise ApiError(409, "SESSION_CONFLICT", "当前状态不允许 STARTED")
                        session.status = "IN_PROGRESS"
                        session.current_page = 1
                        session.visited_pages.append(1)
                        session.revision += 1
                    else:
                        if session.status != "IN_PROGRESS":
                            raise ApiError(409, "SESSION_CONFLICT", "会话尚未开始")
                        current_page = self._page(session.story_version_id, session.current_page)
                        next_page = current_page.get("defaultNextPageNumber")
                        # Keep Mock API-015 semantics aligned with the real backend:
                        # only the current page's official next page is accepted.
                        # The DEMO seeds are linear; additionally reject a visited
                        # page so a malformed loop cannot turn H06 local re-display
                        # into a server-accepted PAGE_CHANGED event.
                        if page_number in session.visited_pages:
                            raise ApiError(409, "SESSION_CONFLICT", "目标页已访问，旧页仅支持本地回看")
                        if next_page != page_number:
                            raise ApiError(409, "SESSION_CONFLICT", "目标页不可达")
                        session.current_page = page_number
                        session.visited_pages.append(page_number)
                        session.revision += 1
                except ApiError as exc:
                    # Deterministic failures keep the original clientEventId.
                    # A corrected retry must use a new key, matching request_executions.
                    self._remember_failure(session, client_event_id, digest, "events", exc)
                    raise
                event_id = session.next_event_id
                session.next_event_id += 1
                event = {
                    "interactionEventId": event_id,
                    "eventType": event_type,
                    "pageNumber": page_number,
                    "clientEventId": client_event_id,
                    "digest": digest,
                }
                session.events.append(event)
                response = success_payload({"interactionEventId": event_id, "revision": session.revision}, self.request_id)
                self._remember_success(session, client_event_id, digest, "events", 201, response)
        if replay_response is not None:
            self._send_json(replay_response[0], replay_response[1], None)
        else:
            self._send_json(201, response)

    def _completion(self, session_id: int) -> None:
        body = self._read_body()
        allowed = {"reason", "expectedRevision", "clientEventId"}
        if set(body) - allowed:
            raise ApiError(422, "VALIDATION_ERROR", "包含未知字段")
        if body.get("reason") != "USER_FINISH":
            raise ApiError(422, "VALIDATION_ERROR", "Mock 只支持 reason=USER_FINISH")
        expected_revision = self._body_int(body, "expectedRevision", 0)
        client_event_id = self._client_event_id(body)
        replay_response: tuple[int, dict[str, Any]] | None = None
        response: Any = None
        with self.server.state.lock:
            session = self.server.state.session_for_update(session_id)
            digest = request_digest("completion", body)
            replay = self._replay(session, client_event_id, digest)
            if replay is not None:
                replay_response = self._replay_payload(replay)
            else:
                try:
                    if session.status == "FINISHED":
                        response = success_payload({"session": session.session_object(), "report": session.report}, self.request_id)
                        self._remember_success(session, client_event_id, digest, "completion", 200, response)
                    else:
                        if session.status != "IN_PROGRESS" or expected_revision != session.revision:
                            raise ApiError(409, "SESSION_CONFLICT", "阅读进度已更新，请刷新后再结束")
                        session.status = "FINISHED"
                        session.revision += 1
                        session.report = {
                            "readingSessionId": session.reading_session_id,
                            "status": "READY",
                            "summary": "本地 DEMO 模拟记录：用于验证结束页，不是正式阅读报告。",
                            "activities": [],
                            "demoOnly": True,
                        }
                        response = success_payload({"session": session.session_object(), "report": session.report}, self.request_id)
                        self._remember_success(session, client_event_id, digest, "completion", 200, response)
                except ApiError as exc:
                    self._remember_failure(session, client_event_id, digest, "completion", exc)
                    raise
        if replay_response is not None:
            self._send_json(replay_response[0], replay_response[1], None)
        else:
            self._send_json(200, response)

    def _question(self, session_id: int) -> None:
        body = self._read_body()
        allowed = {"inputText", "pageNumber", "expectedRevision", "clientEventId"}
        if set(body) - allowed:
            raise ApiError(422, "VALIDATION_ERROR", "包含未知字段")
        input_text = body.get("inputText")
        if not isinstance(input_text, str) or not 1 <= len(input_text.strip()) <= 200:
            raise ApiError(422, "VALIDATION_ERROR", "inputText 必须为 1～200 字符")
        page_number = self._body_int(body, "pageNumber", 1)
        expected_revision = self._body_int(body, "expectedRevision", 0)
        client_event_id = self._client_event_id(body)
        replay_response: tuple[int, dict[str, Any]] | None = None
        response: Any = None
        with self.server.state.lock:
            session = self.server.state.session_for_update(session_id)
            digest = request_digest("question", body)
            replay = self._replay(session, client_event_id, digest)
            if replay is not None:
                replay_response = self._replay_payload(replay)
            else:
                try:
                    if session.status != "IN_PROGRESS" or session.current_page != page_number:
                        raise ApiError(409, "SESSION_CONFLICT", "会话页码或状态不允许提问")
                    if expected_revision != session.revision:
                        raise ApiError(409, "SESSION_CONFLICT", "会话修订号已变化，请先读取会话")
                except ApiError as exc:
                    self._remember_failure(session, client_event_id, digest, "question", exc)
                    raise
                event_id = session.next_event_id
                session.next_event_id += 1
                session.revision += 1
                output_text = "这是本地 Mock 的固定回答，不是真实 AI 输出。"
                event = {
                    "interactionEventId": event_id,
                    "eventType": "ASK",
                    "pageNumber": page_number,
                    "clientEventId": client_event_id,
                    "digest": digest,
                    "resultStatus": "SUCCESS",
                }
                session.events.append(event)
                response = success_payload({
                    "interactionEventId": event_id,
                    "revision": session.revision,
                    "pageNumber": page_number,
                    "inputText": input_text,
                    "outputText": output_text,
                    "status": "GENERATED",
                    "strategyType": "CONTEXT_QUESTION",
                    "sources": [],
                    "demoOnly": True,
                }, self.request_id)
                self._remember_success(session, client_event_id, digest, "question", 201, response)
        if replay_response is not None:
            self._send_json(replay_response[0], replay_response[1], None)
        else:
            self._send_json(201, response)

    def _page(self, version_id: int, page_number: int) -> dict[str, Any]:
        detail = self.server.state.version_detail(version_id)
        for page in detail["pages"]:
            if page["pageNumber"] == page_number:
                return page
        raise ApiError(409, "SESSION_CONFLICT", "目标页不存在")

    def _body_int(self, body: dict[str, Any], name: str, minimum: int) -> int:
        value = body.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ApiError(422, "VALIDATION_ERROR", f"{name} 必须是 >= {minimum} 的整数")
        return value

    def _client_event_id(self, body: dict[str, Any]) -> str:
        value = body.get("clientEventId")
        if not isinstance(value, str) or not 1 <= len(value) <= 64:
            raise ApiError(422, "VALIDATION_ERROR", "clientEventId 必须为 1～64 字符")
        return value

    def _replay(self, session: SessionState, client_event_id: str, digest: str) -> dict[str, Any] | None:
        previous = session.idempotency.get(client_event_id)
        if previous is None:
            return None
        if previous["digest"] != digest:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "请求标识已用于其他操作")
        return previous

    def _voice_scenarios(self) -> None:
        self._send_json(200, success_payload(self.server.state.voice_snapshot(), self.request_id))

    def _voice_scenario(self, scenario_id: str) -> None:
        scenario = self.server.state.voice_scenario(scenario_id)
        if scenario is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "语音场景不存在")
        self._send_json(200, success_payload(scenario, self.request_id))

    def _voice_simulate(self) -> None:
        body = self._read_body()
        scenario_id = body.get("scenarioId")
        if not isinstance(scenario_id, str):
            raise ApiError(422, "VALIDATION_ERROR", "scenarioId 必须是字符串")
        session_id = body.get("sessionId")
        if session_id is not None and (not isinstance(session_id, int) or isinstance(session_id, bool) or session_id < 1):
            raise ApiError(422, "VALIDATION_ERROR", "sessionId 必须是正整数")
        interaction_event_id = body.get("interactionEventId")
        if interaction_event_id is not None and (
            not isinstance(interaction_event_id, int)
            or isinstance(interaction_event_id, bool)
            or interaction_event_id < 1
        ):
            raise ApiError(422, "VALIDATION_ERROR", "interactionEventId 必须是正整数")
        scenario = self.server.state.voice_scenario(scenario_id)
        if scenario is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "语音场景不存在")
        if session_id is not None and not self.server.state.has_session(session_id):
            raise ApiError(404, "RESOURCE_NOT_FOUND", "会话不可用")
        if scenario_id == "tts_failed":
            if session_id is None or interaction_event_id is None:
                raise ApiError(422, "VALIDATION_ERROR", "tts_failed 重试必须复用已保存的 interactionEventId")
            if not self.server.state.has_ask_event(session_id, interaction_event_id):
                raise ApiError(422, "VALIDATION_ERROR", "interactionEventId 不是该会话已保存的 ASK 事件")
        data = scenario
        data["sessionId"] = session_id
        data["interactionEventId"] = interaction_event_id
        data["mockPlayback"] = True
        data["audioUrl"] = None
        self._send_json(200, success_payload(data, self.request_id))

    def _reset(self) -> None:
        self._drain_body()
        self.server.state.reset()
        self._send_json(200, success_payload({"reset": True, "mode": "DEMO", "mock": True}, self.request_id))


def create_server(host: str = "127.0.0.1", port: int = 8765, fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
                  quiet: bool = False) -> MockHTTPServer:
    return MockHTTPServer((host, port), fixtures_dir, quiet=quiet)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.host, args.port, args.fixtures, quiet=args.quiet)
    except (MockFixtureError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: Mock 服务监听非回环地址；仅限受控局域网演示，不得暴露到公网。", file=sys.stderr)
    print(f"Zhiya H05 Mock listening on http://{args.host}:{server.server_address[1]}", file=sys.stderr)
    print(f"Fixtures: {args.fixtures.resolve()}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Mock stopped.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
