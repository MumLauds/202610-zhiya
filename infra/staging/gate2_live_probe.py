"""Exercise the deployed reading code locally against approved Beijing Flash with synthetic books."""

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import uuid
from contextlib import closing
from hashlib import sha256
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from gate1_live_probe import Capture, ROOT
from gate2_live_fixtures import BOOKS
from app.config import Settings
from app.db import yaya_agent as ledger
from app.main import create_app
from app.model_gateway import MAX_RESPONSE_BYTES, ModelGateway
from app.services.yaya_agent import PROMPT_HASH, YayaAgent
from app.services.yaya_agent_config import MODEL, WORKSPACE, YayaSettings
from app.speech_config import SpeechSettings

EXPECTED_CALLS = sum(not case[1].startswith("local-") for _, _, _, cases in BOOKS for case in cases)
MOCK_KEY = "synthetic-gate2-preflight"
MOCK_ENDPOINT = "https://ws-gate2-preflight.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"


def git(*args):
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args], cwd=ROOT, text=True).strip()


def seed(db):
    db.executescript((ROOT / "database" / "schema.sql").read_text(encoding="utf-8"))
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("INSERT INTO accounts VALUES (901000, 'synthetic-gate2-author', '!', 'ADMIN', 1)")
    for version, (name, title, pages, _) in enumerate(BOOKS, 901_001):
        db.execute("INSERT INTO storybooks VALUES (?, NULL, 'LIBRARY', 1)", (version,))
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max, source_ref, license_note,
             status, reviewed_by_id, reviewed_at, created_at, branching)
            VALUES (?, ?, 1, ?, '[]', 3, 6, ?, '合成测试素材', 'PUBLISHED', 901000, 1, 1, 1)""",
                   (version, version, title, f"synthetic-gate2-{name}"))
        db.executemany("""INSERT INTO story_pages
            (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (?, ?, ?, '', ?)""",
                       [(version, page, body, next_page) for page, (body, next_page)
                        in enumerate(zip(pages, (None, 4, 4, 1), strict=True), 1)])
        db.executemany("INSERT INTO story_branches VALUES (?, ?, 1, ?, ?, 'APPROVED')",
                       [(version * 10 + page, version, page, f"合成路线{page}") for page in (2, 3)])


def preflight_reply(request):
    body = json.loads(request.content)
    if str(request.url) != MOCK_ENDPOINT or body["model"] != MODEL:
        raise RuntimeError("Preflight attempted an unexpected outbound route")
    content = json.dumps({"reply": "我们继续看这一页吧。", "next_action": "RESPOND"}, ensure_ascii=False)
    return httpx.Response(200, json={
        "model": MODEL, "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 10}, "request_id": "offline-mock",
    })


class PreflightCapture(Capture):
    async def handle_async_request(self, request):
        response = preflight_reply(request)
        self.calls.append({"messages": json.loads(request.content)["messages"], "http_status": 200,
                           "raw_response": response.text})
        return response


def evaluate(output, *, candidate, preflight):
    changed_runtime = git("status", "--porcelain", "--", "backend", "database")
    if changed_runtime or git("diff", "--name-only", candidate, "HEAD", "--", "backend", "database"):
        raise RuntimeError(f"The test requires clean runtime files at candidate {candidate}")
    fixture = Path(__file__).with_name("gate2_live_fixtures.py").read_bytes()
    key, endpoint = os.environ["YAYA_AGENT2_API_KEY"], os.environ["YAYA_AGENT2_API_URL"]
    if not preflight and (not WORKSPACE.fullmatch(endpoint) or key.startswith("synthetic-")
                          or key in (os.getenv("MODEL_API_KEY"), os.getenv("DASHSCOPE_API_KEY"))):
        raise RuntimeError("Use an approved, dedicated Beijing workspace and testing key")

    with tempfile.TemporaryDirectory(prefix="gate2-local-synthetic-") as temp:
        db_path = Path(temp).resolve() / "gate2.sqlite3"
        os.environ.update(ZHIYA_DB_PATH=str(db_path), YAYA_AGENT2_MODE="off", YAYA_AGENT2_STAGE="staging")
        with closing(sqlite3.connect(db_path)) as db, db:
            seed(db)
        ledger.install()
        app = create_app(Settings(), SpeechSettings(), yaya_settings=YayaSettings())
        with TestClient(app, base_url="https://testserver") as client, output.open("x", encoding="utf-8") as out:
            def write(row):
                text = json.dumps(row, ensure_ascii=False)
                if key and key in text:
                    raise RuntimeError("Credential appeared in capture; row was not stored")
                out.write(text + "\n")
                out.flush()

            def post(route, data):
                response = client.post("/api/v1" + route, json=data,
                                       headers={"X-CSRF-Token": client.cookies.get("zhiya_csrf", "")})
                if response.status_code != 201:
                    raise RuntimeError(f"Local synthetic {route} returned HTTP {response.status_code}")
                return response.json()

            accounts = []
            for name, _, _, _ in BOOKS:
                account = {"account": f"gate2-{name}-only", "password": "synthetic-gate2-local-login"}
                post("/auth/registrations", account)
                guardian = post("/auth/sessions", account)["data"]["accountId"]
                profile = post("/child-profiles", {"ageBand": "AGE_3_4", "preferredMinutes": 10})["data"]["childProfileId"]
                accounts.append((account, guardian, profile))
            os.environ.update(YAYA_AGENT2_MODE="allowlist",
                              YAYA_AGENT2_ALLOWLIST=",".join(str(a[1]) for a in accounts),
                              YAYA_AGENT2_INPUT_ALLOWLIST=",".join(f"{a[1]}:{a[2]}" for a in accounts))
            settings = YayaSettings.from_env()
            transport_type = PreflightCapture if preflight else Capture
            transport = transport_type(endpoint, MAX_RESPONSE_BYTES)
            gateway = ModelGateway(settings.model, transport=transport)
            app.state.yaya_agent = YayaAgent(settings, gateway)
            write({"revision": candidate, "scope": "synthetic_offline_preflight" if preflight else
                   "gate2_real_beijing_provider_local_asgi_synthetic_only", "model": MODEL,
                   "prompt_sha256": PROMPT_HASH, "fixture_sha256": sha256(fixture).hexdigest(),
                   "probe_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                   "books": len(BOOKS), "planned_model_turns": EXPECTED_CALLS,
                   "input_micros_per_million": settings.input_micros_per_million,
                   "output_micros_per_million": settings.output_micros_per_million})

            def open_session(version, profile):
                data = post("/reading-sessions", {"childProfileId": profile, "storyVersionId": version,
                                                 "mode": "PARENT_CHILD"})["data"]
                base = f"/reading-sessions/{data['readingSessionId']}"
                rev = post(base + "/events", {"eventType": "STARTED", "pageNumber": 1,
                                              "expectedRevision": 0, "clientEventId": "start"})["data"]["revision"]
                return base, rev

            rows = []
            stop = None
            try:
                for version, (book, account_info) in enumerate(zip(BOOKS, accounts, strict=True), 901_001):
                    name, title, pages, cases = book
                    account, _, profile = account_info
                    post("/auth/sessions", account)
                    base, rev = open_session(version, profile)
                    current, walked, history = 1, [1], []
                    for number, (page, kind, text, criterion) in enumerate(cases, 1):
                        if page == 3 and page != current:
                            base, rev = open_session(version, profile)
                            current, walked, history = 1, [1], []
                        if current != page:
                            nav = {"expectedRevision": rev, "clientEventId": f"nav-{number}"}
                            if current == 1 and page in (2, 3):
                                nav["branchId"] = version * 10 + page
                                rev = post(base + "/branches", nav)["data"]["revision"]
                            else:
                                nav.update(eventType="PAGE_CHANGED", pageNumber=page)
                                rev = post(base + "/events", nav)["data"]["revision"]
                            current, history = page, []
                            walked.append(page)
                        case_id = f"{name}-{number:02}"
                        before, started = len(transport.calls), time.perf_counter()
                        response = post(base + "/events", {
                            "eventType": "ANSWER", "inputText": text, "pageNumber": page,
                            "expectedRevision": rev, "clientEventId": case_id,
                        })
                        backend_ms = round(1000 * (time.perf_counter() - started), 2)
                        data = response["data"]
                        rev = data["revision"]
                        receipt = data["agentReceipt"]
                        calls = transport.calls[before:]
                        with closing(sqlite3.connect(db_path)) as db:
                            amount = db.execute("SELECT ceiling_micros, settled_micros FROM yaya_agent_calls WHERE call_id=?",
                                                (response["requestId"],)).fetchone()
                        for call in calls:
                            call.pop("_start", None)
                            try:
                                envelope = json.loads(call.get("raw_response", ""))
                            except ValueError:
                                envelope = {}
                            if isinstance(envelope, dict):
                                call.update(model_returned=envelope.get("model"), usage=envelope.get("usage"))
                                call["provider_request_id"] = call.get("provider_request_id") or envelope.get("request_id")
                        row = {"case": case_id, "book": name, "title": title, "category": kind, "input": text,
                               "criterion": criterion, "page": page, "read_path": list(walked),
                               "page_texts": {p: pages[p - 1] for p in dict.fromkeys(walked) if p <= page},
                               "recent_dialogue": history[-4:], "displayed_reply": data.get("replyText"),
                               "followup": data.get("followupQuestion"), "receipt": receipt, "provider_attempts": calls,
                               "backend_ms": backend_ms,
                               "reservation_and_settlement": amount}
                        rows.append(row)
                        write(row)
                        history.append([text, data.get("replyText")])
                        if any(call.get("http_status") in {401, 403, 429} for call in calls):
                            stop = "provider_denied_or_rate_limited"
                            break
                        if len(calls) > 1 or (kind.startswith("local-") and calls):
                            stop = "unexpected_provider_dispatch"
                            break
                    print(f"{name}: {sum(row['book'] == name for row in rows)} local replies recorded", flush=True)
                    if stop:
                        break
            finally:
                client.portal.call(gateway.__aexit__, None, None, None)
            verified = sum(call.get("http_status") == 200 and bool(call.get("provider_request_id"))
                           and call.get("model_returned") == MODEL for row in rows for call in row["provider_attempts"])
            write({"rows": len(rows), "provider_attempts": len(transport.calls),
                   "verified_real_responses": 0 if preflight else verified, "verified_mock_responses": verified if preflight else 0,
                   "stop_reason": stop})
            if preflight:
                if stop or verified != EXPECTED_CALLS or len(rows) != EXPECTED_CALLS + 6:
                    raise RuntimeError("Offline preflight did not exercise every expected model turn and local control")
                for row in rows:
                    for call in row["provider_attempts"]:
                        user = call["messages"][-1]["content"]
                        material = json.loads(user.split("\n", 1)[1].split("\n\n用户输入：\n", 1)[0])
                        timeline = material["visitedTimeline"]
                        visited = list(dict.fromkeys(p for p in row["read_path"] if p < row["page"]))[-3:]
                        if (material["currentPage"] != row["page"]
                                or list(dict.fromkeys(event["page"] for event in timeline)) != visited
                                or any(event["quote"] not in row["page_texts"][event["page"]] for event in timeline)
                                or any(quote not in row["page_texts"][row["page"]] for quote in material["currentQuotes"])):
                            raise RuntimeError(f"Unexpected outbound page evidence: {row['case']}")
                    if row["category"].startswith("local-") and (
                        row["receipt"]["action"] != "LISTEN" or row["provider_attempts"] or row["followup"]
                    ):
                        raise RuntimeError(f"Unexpected listening behavior: {row['case']}")
            elif stop or verified < 60:
                raise RuntimeError("Capture ended below the 60-response target or with a provider stop. Check private evidence.")


if __name__ == "__main__":
    logging.getLogger("zhiya").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--approved-synthetic-staging", action="store_true")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--candidate", help="Full runtime commit SHA; defaults to clean HEAD")
    args = parser.parse_args()
    candidate = args.candidate or git("rev-parse", "HEAD")
    if re.fullmatch(r"[0-9a-f]{40}", candidate) is None:
        parser.error("Candidate must be a full commit SHA")
    target = args.evidence.resolve()
    if target.is_relative_to(ROOT) or target.exists():
        parser.error("Use a private new path outside the checkout")
    if args.preflight:
        os.environ.update(YAYA_AGENT2_API_URL=MOCK_ENDPOINT, YAYA_AGENT2_API_KEY=MOCK_KEY,
                          YAYA_AGENT2_LEDGER_SECRET=uuid.uuid4().hex)
    elif not all(os.getenv(name) for name in ("YAYA_AGENT2_API_URL", "YAYA_AGENT2_API_KEY", "YAYA_AGENT2_LEDGER_SECRET")):
        parser.exit(3, "BLOCKED: approved dedicated staging credentials required.\n")
    evaluate(target, candidate=candidate, preflight=args.preflight)
