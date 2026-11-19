"""G13 child handoff, guided answer, replay and parent unlock on a loopback HTTPS server."""
import json
import secrets
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "round2"))
from verify import ModelGateway, ROOT, SOURCE, TITLE, local_app, seed_fixture


def request(page, path, payload=None):
    return page.evaluate("""async ([path, payload]) => {
        const token = document.cookie.split('; ').find(c => c.startsWith('zhiya_csrf='));
        const headers = {'Content-Type': 'application/json'};
        if (token) headers['X-CSRF-Token'] = decodeURIComponent(token.slice('zhiya_csrf='.length));
        const res = await fetch(path, payload === null ? {} : {
            method: 'POST', headers, body: JSON.stringify(payload)
        });
        return [res.status, await res.json()];
    }""", [path, payload])


def enter_child_mode(page, origin, password):
    account = "g13-family"
    def button(name):
        return page.get_by_role("button", name=name, exact=True)

    page.goto(origin + "/#auth")
    assert page.evaluate("window.isSecureContext")
    assert request(page, "/api/v1/auth/registrations", {"account": account, "password": password})[0] == 201
    page.get_by_placeholder("3–64 个字符").fill(account)
    page.get_by_placeholder("至少 8 位").fill(password)
    button("登录家长空间").click()
    page.get_by_role("heading", name=TITLE, exact=True).wait_for()
    button("开始阅读").click()
    button("＋ 添加孩子").click()
    page.get_by_label("昵称").fill("联调小芽")
    button("保存档案").click()
    page.get_by_role("heading", name="联调小芽", exact=True).wait_for()
    page.locator("nav.top-nav").get_by_role("button", name="绘本馆").click()
    page.get_by_role("heading", name=TITLE, exact=True).wait_for()
    with page.expect_response(lambda r: r.url.endswith("/reading-sessions") and r.request.method == "POST") as created:
        button("开始阅读").click()
    assert created.value.status == 201
    sid = created.value.json()["data"]["readingSessionId"]
    button("设置并进入儿童模式").click()
    with page.expect_response(lambda r: r.url.endswith(f"/{sid}/mode-settings") and r.request.method == "PUT") as granted:
        button("开启儿童模式").click()
    assert granted.value.status == 200 and granted.value.json()["data"]["mode"] == "CHILD_SOLO"
    page.get_by_role("heading", name="阅读空间已为孩子准备好").wait_for()
    assert page.url.endswith(f"#child/{sid}") and page.locator("nav.top-nav").count() == 0
    return sid


def test_child_handoff_and_answer(tmp_path, monkeypatch):
    path = tmp_path / "fixture.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    seed_fixture(path)
    prompts = []

    def provider(req):
        assert str(req.url) == "https://fixture.invalid/model"
        prompt = json.loads(req.content)["messages"][1]["content"]
        prompts.append(prompt)
        question = prompt.split("候选问句：\n", 1)[1].splitlines()[0]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                          "message": {"role": "assistant", "content": question}}]})

    monkeypatch.setattr("app.main.ModelGateway", partial(ModelGateway, transport=httpx.MockTransport(provider)))
    with local_app(tmp_path) as (origin, pin), sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", chromium_sandbox=True,
                                     args=["--ignore-certificate-errors-spki-list=" + pin])
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        password = secrets.token_urlsafe(18)
        sid = enter_child_mode(page, origin, password)
        url = f"/api/v1/reading-sessions/{sid}"
        options = ["是", "不是", "不知道"]
        commit = subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT),
                                          "rev-parse", "HEAD"], text=True).strip()
        result = {"commit": commit, "atUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "browser": f"Chrome {browser.version}", "sessionId": sid, "writes": [], "reloads": []}

        def send(endpoint, payload, expected):
            status, body = request(page, url + endpoint, payload)
            assert status == expected, (status, body)
            data = body["data"] if status < 400 else body["error"]
            result["writes"].append({"clientEventId": payload["clientEventId"], "http": status,
                                     **{k: data[k] for k in ("interactionEventId", "revision", "status",
                                        "strategyType", "answerOptions", "code") if k in data}, "modelCalls": len(prompts)})
            return data

        def reload(expected_events, revision):
            with page.expect_response(lambda r: r.url == origin + url and r.request.method == "GET") as restored:
                page.reload()
            assert restored.value.status == 200
            data = restored.value.json()["data"]
            page.get_by_role("heading", name="阅读空间已为孩子准备好").wait_for()
            assert page.url.endswith(f"#child/{sid}") and data["session"]["readingSessionId"] == sid
            assert data["session"]["mode"] == "CHILD_SOLO" and data["session"]["revision"] == revision
            assert data["report"] is None and data["answerOptions"] == options
            assert [e["eventType"] for e in data["events"]] == expected_events
            result["reloads"].append({"http": 200, "revision": revision, "answerOptions": data["answerOptions"],
                                      "report": data["report"], "events": data["events"]})
            return data

        profiles = "/api/v1/child-profiles"
        forbidden_status, forbidden = request(page, profiles)
        assert forbidden_status == 403 and forbidden["error"]["code"] == "FORBIDDEN"
        result["childProfilesBeforeUnlock"] = {"http": forbidden_status, "code": forbidden["error"]["code"]}
        def payload(key, revision, **fields):
            return {"clientEventId": key, "expectedRevision": revision, "pageNumber": 1, **fields}

        started = send("/events", payload("child-start", 0, eventType="STARTED"), 201)
        first = send("/guidance", payload("child-initial", started["revision"], feedback="INITIAL"), 201)
        assert first["status"] == "GENERATED" and first["answerOptions"] == options and first["sources"] == [SOURCE]
        assert first["outputText"].endswith("吗？") and first["strategyType"] == "CONTEXT_QUESTION"
        data = reload(["STARTED", "QUESTION"], first["revision"])
        assert data["events"][-1]["outputText"] == first["outputText"]
        rejected = send("/events", payload("child-invalid", first["revision"], eventType="ANSWER", inputText="其它"), 422)
        assert rejected["code"] == "VALIDATION_ERROR"
        answer_payload = payload("child-answer", first["revision"], eventType="ANSWER", inputText=data["answerOptions"][0])
        answer = send("/events", answer_payload, 201)
        assert answer["revision"] == first["revision"] + 1 and send("/events", answer_payload, 200) == answer
        follow = send("/guidance", payload("child-understood", answer["revision"], feedback="UNDERSTOOD"), 201)
        assert follow["status"] == "GENERATED" and follow["sources"] == [SOURCE] and follow["answerOptions"] == options
        assert follow["outputText"] != first["outputText"] and follow["outputText"].endswith("吗？")
        assert follow["strategyType"] == "CONTEXT_QUESTION"
        assert first["outputText"] in prompts[1]
        skip = send("/guidance", payload("child-skip", follow["revision"], feedback="SKIPPED"), 201)
        assert (skip["status"], skip["strategyType"], skip["outputText"]) == ("FALLBACK", "SKIP", "")
        assert len(prompts) == 2 and send("/events", answer_payload, 200) == answer
        data = reload(["STARTED", "QUESTION", "ANSWER", "FEEDBACK", "FEEDBACK"], skip["revision"])
        assert data["events"][-2]["outputText"] == follow["outputText"]
        assert data["events"][-1]["feedback"] == "SKIPPED"

        page.get_by_label("家长账号").fill("g13-family")
        page.get_by_label("密码", exact=True).fill(password)
        with page.expect_response(lambda r: r.url.endswith("/auth/parent-unlocks")) as unlocked:
            page.get_by_role("button", name="退出儿童模式").click()
        assert unlocked.value.status == 200
        page.wait_for_url("**/#account/overview")
        page.locator("nav.top-nav").wait_for()
        status, entries = request(page, profiles)
        assert status == 200 and len(entries["data"]["items"]) == 1
        status, guardian = request(page, url)
        final_session = guardian["data"]["session"]
        assert status == 200 and final_session["status"] == "INTERRUPTED"
        assert final_session["revision"] == skip["revision"] + 1
        with closing(sqlite3.connect(path)) as db:
            reason, revision, revoked = db.execute(
                """SELECT s.end_reason,s.revision,g.revoked_at IS NOT NULL FROM reading_sessions s
                   JOIN child_grants g ON g.id=s.grant_id WHERE s.id=?""", (sid,),
            ).fetchone()
            assert (reason, revision, revoked) == ("GRANT_REVOKED", final_session["revision"], 1)
            rows = db.execute("SELECT event_type,request_key,input_text,feedback,revision_after FROM interaction_events "
                              "WHERE session_id=? ORDER BY id", (sid,)).fetchall()
            assert [r[0] for r in rows] == ["STARTED", "QUESTION", "ANSWER", "FEEDBACK", "FEEDBACK"]
            assert [r[-1] for r in rows] == [1, 2, 3, 4, 5] and rows[2][2] == options[0]
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
        result["guardianUnlock"] = {"http": unlocked.value.status, "guardianProfilesHttp": 200,
                                    "view": "account/overview", "sessionStatus": final_session["status"],
                                    "endReason": reason, "finalRevision": revision, "grantRevoked": bool(revoked)}
        result["databaseEvents"] = [dict(zip(["type", "key", "input", "feedback", "revision"], row)) for row in rows]
        result["provider"] = {"mode": "httpx.MockTransport", "calls": len(prompts)}
        result["ui"] = ["guardian login and profile", "shelf open", "child mode handoff", "reload handoff", "parent unlock"]
        result["sameOriginRequests"] = ["registration", "child profiles permission", "STARTED", "INITIAL", "ANSWER",
                                        "UNDERSTOOD", "SKIPPED", "guardian session check"]
        context.close()
        browser.close()
    output = tmp_path / "result.json"
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"G13 round 3 result: {output}")
