"""G13 round two. Run with pytest, a built frontend, Chrome, OpenSSL and Python Playwright."""
import asyncio
import base64
import hashlib
import json
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from functools import partial
from importlib.metadata import version
from pathlib import Path
from threading import Thread

import httpx
import uvicorn
from playwright.sync_api import sync_playwright
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "database")]
from app.config import Settings
from app.main import create_app
from app.model_gateway import ModelGateway
from app.speech_config import SpeechSettings
from init_db import connect, initialize

TITLE = "纸船停在哪里"
SOURCE = {"knowledgeItemId": 81, "sourceTitle": "G13 原创观察提示"}


def seed_fixture(path):
    initialize(path)
    with closing(connect(path)) as db, db:
        db.execute("INSERT INTO accounts VALUES (9000,'fixture-reviewer','no-login','ADMIN',1)")
        db.execute("INSERT INTO storybooks VALUES (11,NULL,'LIBRARY',1)")
        db.execute("""INSERT INTO story_versions (id,storybook_id,version_no,title,topics_json,
                   age_min,age_max,license_note,status,reviewed_by_id,reviewed_at,created_at,branching)
                   VALUES (11,11,1,?,'["自然"]',3,6,'原创测试夹具','PUBLISHED',9000,1,1,0)""", (TITLE,))
        db.execute("""INSERT INTO story_pages (version_id,page_no,body_text,image_ref)
                   VALUES (11,1,'小园把一只纸船放进水里。','fixture-no-image')""")
        db.execute("""INSERT INTO knowledge_items (id,version_no,item_key,title,source_title,source_ref,
                   locator,age_band,topics_json,task_types_json,content,status,reviewed_at,created_at)
                   VALUES (81,1,'g13-local','观察纸船',?,'fixture','p1','AGE_4_5','["自然"]',
                   '["GUIDANCE"]','让孩子先观察画面里的角色。','APPROVED',1,1)""", (SOURCE["sourceTitle"],))


@contextmanager
def local_app(directory, *, application=None):
    # Trust only this disposable loopback key in this browser process.
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    public_key = subprocess.check_output(["openssl", "pkey", "-in", str(key), "-pubout", "-outform", "DER"])
    pin = base64.b64encode(hashlib.sha256(public_key).digest()).decode("ascii")
    app = application if application is not None else create_app(
        Settings(model_api_url="https://fixture.invalid/model", model_api_key="fixture"), SpeechSettings())
    app.mount("/", StaticFiles(directory=ROOT / "frontend" / "dist", html=True))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, ssl_certfile=str(cert), ssl_keyfile=str(key),
                                               timeout_keep_alive=1, timeout_graceful_shutdown=8,
                                               access_log=False, log_level="warning"))
        loop_factory = asyncio.SelectorEventLoop
        if application is not None and sys.platform == "win32":
            # Live ASR needs a subprocess-capable loop for FFmpeg.
            loop_factory = asyncio.ProactorEventLoop
        thread = Thread(target=asyncio.run, args=(server.serve(sockets=[sock]),),
                        kwargs={"loop_factory": loop_factory}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        try:
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline, "Local HTTPS server did not start"
                time.sleep(0.05)
            yield f"https://127.0.0.1:{sock.getsockname()[1]}", pin
        finally:
            server.should_exit = True
            thread.join(20)
            key.unlink(missing_ok=True)
            assert not thread.is_alive(), "Local HTTPS server did not shut down"


def start_reading(page, origin):
    page.goto(origin + "/#auth")
    assert page.evaluate("window.isSecureContext")
    credentials = {"account": "g13-family", "password": secrets.token_urlsafe(18)}
    status = page.evaluate("""async data => (await fetch('/api/v1/auth/registrations', {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)
    })).status""", credentials)
    assert status == 201
    def button(name):
        return page.get_by_role("button", name=name, exact=True)

    page.get_by_placeholder("3–64 个字符").fill(credentials["account"])
    page.get_by_placeholder("至少 8 位").fill(credentials["password"])
    button("登录家长空间").click()
    page.get_by_role("heading", name=TITLE, exact=True).wait_for()
    button("开始阅读").click()
    button("＋ 添加孩子").click()
    page.get_by_label("昵称").fill("联调小芽")
    with page.expect_response(lambda r: r.url.endswith("/child-profiles") and r.request.method == "POST") as saved:
        button("保存档案").click()
    assert saved.value.status == 201
    page.get_by_role("heading", name="联调小芽", exact=True).wait_for()
    page.locator("nav.top-nav").get_by_role("button", name="绘本馆").click()
    page.get_by_role("heading", name=TITLE, exact=True).wait_for()
    button("开始阅读").click()
    with page.expect_response(lambda r: r.url.endswith("/events") and r.request.method == "POST") as started:
        button("开始本次阅读").click()
    started_payload = started.value.request.post_data_json
    assert started.value.status == 201 and started_payload["eventType"] == "STARTED"
    return int(started.value.url.split("/")[-2]), started_payload["clientEventId"]


def database_state(path, sid):
    with closing(connect(path)) as db:
        revision, = db.execute("SELECT revision FROM reading_sessions WHERE id=?", (sid,)).fetchone()
        events = db.execute("""SELECT id,event_type,revision_after FROM interaction_events
                            WHERE session_id=? ORDER BY id""", (sid,)).fetchall()
        evidence = db.execute("""SELECT e.event_id,e.knowledge_item_id FROM event_evidence e
                              JOIN interaction_events i ON i.id=e.event_id
                              WHERE i.session_id=? ORDER BY e.event_id,e.rank_no""", (sid,)).fetchall()
        db.row_factory = sqlite3.Row
        executions = [dict(row) for row in db.execute("""
            SELECT * FROM request_executions WHERE scope_type='SESSION_EVENT' AND scope_id=? ORDER BY id
        """, (sid,))]
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        return {"revision": revision, "events": events, "executions": executions, "evidence": evidence}


def test_guidance_replay_and_browser_refresh(tmp_path, monkeypatch):
    path = tmp_path / "fixture.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    seed_fixture(path)
    model_calls = []

    def provider(request):
        assert str(request.url) == "https://fixture.invalid/model"
        context = json.loads(request.content)["messages"][1]["content"]
        selected = context.split("候选问句：\n", 1)[1].splitlines()[0]
        model_calls.append({"prompt": context, "question": selected})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                    "message": {"role": "assistant", "content": selected}}]})

    monkeypatch.setattr("app.main.ModelGateway", partial(ModelGateway, transport=httpx.MockTransport(provider)))
    with local_app(tmp_path) as (origin, pin), sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", chromium_sandbox=True,
                                             args=["--ignore-certificate-errors-spki-list=" + pin])
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        sid, start_key = start_reading(page, origin)
        url = f"/api/v1/reading-sessions/{sid}"
        state = database_state(path, sid)
        assert state["revision"] == 1 and [e[1] for e in state["events"]] == ["STARTED"]
        baseline_executions = state["executions"]
        assert [(row["client_request_id"], row["status"]) for row in baseline_executions] == [(start_key, "SUCCEEDED")]
        commit = subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT),
                                          "rev-parse", "HEAD"], text=True).strip()
        summary = {"commit": commit, "atUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "browser": "Chrome " + browser.version, "pytest": version("pytest"),
                   "playwright": version("playwright"), "sessionId": sid, "pageNumber": 1, "writes": []}

        def send(payload):
            status, body = page.evaluate("""async ([path, data]) => {
                const csrf = decodeURIComponent(document.cookie.split('; ')
                    .find(value => value.startsWith('zhiya_csrf=')).slice('zhiya_csrf='.length));
                const r = await fetch(path + '/guidance', {method:'POST',
                    headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify(data)});
                return [r.status, await r.json()];
            }""", [url, payload])
            assert status in (200, 201), (status, body.get("error"))
            data = body["data"]
            snapshot = database_state(path, sid)
            summary["writes"].append({"key": payload["clientEventId"], "feedback": payload["feedback"],
                                      "expectedRevision": payload["expectedRevision"], "http": status,
                                      "eventId": data["interactionEventId"], "replyRevision": data["revision"],
                                      "dbRevision": snapshot["revision"], "eventCount": len(snapshot["events"]),
                                      "sessionExecutions": len(snapshot["executions"]),
                                      "guidanceExecutions": len(snapshot["executions"]) - len(baseline_executions),
                                      "evidenceRows": len(snapshot["evidence"]), "modelCalls": len(model_calls)})
            return status, data

        first_payload = {"clientEventId": "g13-initial", "expectedRevision": 1, "pageNumber": 1, "feedback": "INITIAL"}
        replies = []
        for payload, event_type, strategy in (
            (first_payload, "QUESTION", "CONTEXT_QUESTION"),
            (dict(first_payload, clientEventId="g13-feedback", feedback="NOT_UNDERSTOOD"),
             "FEEDBACK", "CONCRETE_RECALL"),
        ):
            count = len(replies) + 1
            if replies:
                payload["expectedRevision"] = replies[-1]["revision"]
            status, reply = send(payload)
            assert status == 201 and reply["status"] == "GENERATED" and reply["sources"] == [SOURCE]
            assert reply["strategyType"] == strategy and reply["revision"] == count + 1
            assert len(model_calls) == count
            assert reply["outputText"] and reply["outputText"] == model_calls[-1]["question"]
            previous_executions = state["executions"]
            state = database_state(path, sid)
            event_id = reply["interactionEventId"]
            assert len(state["events"]) == count + 1 and state["events"][-1] == (event_id, event_type, count + 1)
            executions = state["executions"]
            assert executions[:-1] == previous_executions
            assert executions[-1]["client_request_id"] == payload["clientEventId"] and executions[-1]["status"] == "SUCCEEDED"
            assert len(state["evidence"]) == count and state["evidence"][-1] == (event_id, SOURCE["knowledgeItemId"])
            assert state["revision"] == reply["revision"]
            assert send(payload) == (200, reply)
            assert database_state(path, sid) == state and len(model_calls) == count
            replies.append(reply)

        assert replies[0]["outputText"] in model_calls[1]["prompt"]
        assert replies[1]["outputText"] != replies[0]["outputText"]
        assert send(first_payload) == (200, replies[0])
        assert database_state(path, sid) == state and len(model_calls) == 2
        with page.expect_response(lambda r: r.url == origin + url and r.request.method == "GET") as restored:
            page.reload()
        assert restored.value.status == 200
        data = restored.value.json()["data"]
        expected = [{"interactionEventId": reply["interactionEventId"], "eventType": kind, "pageNumber": 1,
                     "outputText": reply["outputText"], "strategyType": reply["strategyType"]}
                    for reply, kind in zip(replies, ("QUESTION", "FEEDBACK"))]
        expected[1]["feedback"] = "NOT_UNDERSTOOD"
        assert data["session"]["readingSessionId"] == sid and data["session"]["revision"] == 3
        assert data["events"][0]["eventType"] == "STARTED" and data["events"][1:] == expected
        assert database_state(path, sid) == state and len(model_calls) == 2
        summary["refresh"] = {"http": 200, "revision": 3, "events": data["events"]}
        summary["database"] = {**state, "executions": [(row["client_request_id"], row["status"])
                                                     for row in state["executions"]]}
        summary["provider"] = {"mode": "httpx.MockTransport", "calls": len(model_calls),
                               "selectedQuestions": [call["question"] for call in model_calls]}
        context.close()
        browser.close()
    output = tmp_path / "result.json"
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"G13 result: {output}")
