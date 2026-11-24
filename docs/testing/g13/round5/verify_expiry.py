"""G13 round five: allow two five-minute child grants to expire on the server's real clock."""
import json
import secrets
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from functools import partial
from importlib.metadata import version
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "round2"))
from verify import ModelGateway, ROOT, TITLE, local_app, seed_fixture


def utc_at(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds")


def request(page, path, payload=None):
    return page.evaluate("""async ([url, data]) => {
        const headers = {'Content-Type': 'application/json'};
        const cookie = document.cookie.split('; ').find(c => c.startsWith('zhiya_csrf='));
        if (cookie) headers['X-CSRF-Token'] = decodeURIComponent(cookie.slice(11));
        const response = await fetch(url, data === null ? {} :
            {method: 'POST', headers, body: JSON.stringify(data)});
        return [response.status, await response.json()];
    }""", [path, payload])


def grant(page, origin, account, password):
    page.goto(origin + "/#auth")
    assert page.evaluate("window.isSecureContext")
    assert request(page, "/api/v1/auth/registrations", {"account": account, "password": password})[0] == 201
    page.get_by_placeholder("3–64 个字符").fill(account)
    page.get_by_placeholder("至少 8 位").fill(password)
    page.get_by_role("button", name="登录家长空间", exact=True).click()
    page.get_by_role("heading", name=TITLE, exact=True).wait_for()
    page.get_by_role("button", name="开始阅读", exact=True).click()
    page.get_by_role("button", name="＋ 添加孩子").click()
    page.get_by_label("昵称").fill("联调小芽")
    page.get_by_role("button", name="保存档案").click()
    page.get_by_role("heading", name="联调小芽", exact=True).wait_for()
    page.locator("nav.top-nav").get_by_role("button", name="绘本馆").click()
    page.get_by_role("heading", name=TITLE, exact=True).wait_for()
    with page.expect_response(lambda r: r.url.endswith("/reading-sessions") and r.request.method == "POST") as created:
        page.get_by_role("button", name="开始阅读", exact=True).click()
    assert created.value.status == 201
    sid = created.value.json()["data"]["readingSessionId"]
    page.get_by_role("button", name="设置并进入儿童模式").click()
    page.get_by_role("combobox").select_option("5")
    with page.expect_response(lambda r: r.url.endswith(f"/{sid}/mode-settings") and r.request.method == "PUT") as granted:
        page.get_by_role("button", name="开启儿童模式", exact=True).click()
    assert granted.value.status == 200 and granted.value.request.post_data_json["timeLimitSeconds"] == 300
    page.get_by_role("heading", name="阅读空间已为孩子准备好").wait_for()
    assert page.url.endswith(f"#child/{sid}") and page.locator("nav.top-nav").count() == 0
    return sid, granted.value.json()["data"]["expiresAt"]


def stored(path, sid):
    # Only read from the application's fixture database after the initial seed.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")  # Read all tables from one SQLite snapshot.
        session = dict(db.execute("""SELECT s.status,s.revision,s.end_reason,s.ended_at,s.visited_pages_json,
            g.issued_at,g.expires_at,g.revoked_at FROM reading_sessions s
            JOIN child_grants g ON g.id=s.grant_id WHERE s.id=?""", (sid,)).fetchone())
        snapshots = {}
        for name, query in (
            ("events", "SELECT * FROM interaction_events WHERE session_id=? ORDER BY id"),
            ("writes", "SELECT * FROM request_executions WHERE scope_type='SESSION_EVENT' AND scope_id=? ORDER BY id"),
            ("reports", "SELECT * FROM reading_reports WHERE session_id=?"),
        ):
            snapshots[name] = [dict(row) for row in db.execute(query, (sid,))]
        return session, snapshots


def counts(rows):
    return {name: len(items) for name, items in rows.items()}


def probe_state(snapshot):
    session, rows = snapshot
    return {"status": session["status"], "revision": session["revision"], "reason": session["end_reason"],
            "endedAt": utc_at(session["ended_at"]) if session["ended_at"] is not None else None,
            "grantRevoked": session["revoked_at"] is not None, **counts(rows)}


def check_expired(path, case, peer, calls):
    page, sid = case["page"], case["sid"]
    started = case["started"]
    url = f"/api/v1/reading-sessions/{sid}"
    expected_status, reason = ("FINISHED", "TIME_LIMIT") if started else ("INTERRUPTED", "GRANT_REVOKED")
    pages = [1] if started else []
    before = stored(path, sid)
    assert before == case["initial"], "Session changed before its first expired request"
    peer_before = stored(path, peer["sid"])
    timestamp = time.time_ns() // 1_000_000
    assert timestamp >= case["expires"]
    code, body = request(page, url)
    assert code == 401 and body["error"]["code"] == "AUTH_REQUIRED" and "data" not in body
    denied = {url: code}
    after = stored(path, sid)
    session, _ = after
    assert (session["status"], session["end_reason"], session["revision"]) == (expected_status, reason, int(started) + 1)
    assert session["ended_at"] == case["expires"] and session["revoked_at"] is None
    peer_after = stored(path, peer["sid"])
    assert peer_after == peer_before, "First expired request changed the other family's session"

    # Only read SQLite until the first request's deferred report has settled.
    deadline = time.monotonic() + 12
    while True:
        terminal, rows = stored(path, sid)
        if not started or any(r["status"] == "READY" for r in rows["reports"]):
            break
        assert time.monotonic() < deadline, "Timed out waiting for the time-limit report"
        time.sleep(0.1)
    assert (terminal["status"], terminal["end_reason"], terminal["revision"]) == (expected_status, reason, int(started) + 1)
    assert terminal["revoked_at"] is None and terminal["ended_at"] == terminal["expires_at"]
    assert json.loads(terminal["visited_pages_json"]) == pages
    if started:
        assert rows["reports"][0]["summary"] == "本次共读阅读了第 1 页。"
    assert [e["event_type"] for e in rows["events"]] == (["STARTED"] if started else [])
    assert counts(rows) == {"events": int(started), "writes": 2 * int(started), "reports": int(started)}

    code, body = request(page, "/api/v1/child-profiles")
    assert code == 401 and body["error"]["code"] == "AUTH_REQUIRED" and "data" not in body
    denied["/api/v1/child-profiles"] = code
    assert stored(path, sid) == (terminal, rows) and stored(path, peer["sid"]) == peer_before

    with page.expect_response(lambda r: r.url.endswith("/child-profiles") and r.request.method == "GET") as identity:
        page.reload()
    assert identity.value.status == 401
    expired_heading = "请家长解锁设备"
    expect(page.get_by_role("heading", name=expired_heading, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="退出儿童模式", exact=True)).to_be_visible()
    assert page.locator("nav.top-nav").count() == 0 and stored(path, sid) == (terminal, rows)
    child_route = page.evaluate("location.hash")

    page.get_by_label("家长账号").fill(case["account"])
    page.get_by_label("密码", exact=True).fill(case["password"])
    with page.expect_response(lambda r: r.url.endswith("/auth/parent-unlocks") and r.request.method == "POST") as unlock:
        page.get_by_role("button", name="退出儿童模式").click()
    assert unlock.value.status == 200
    page.wait_for_url("**/#account/overview")
    page.locator("nav.top-nav").get_by_role("button", name="成长记录").click()
    label = f"阅读记录 #{sid} · 浏览 {len(pages)} 页 · {'已完成' if started else '已中断'}"
    history_card = page.locator("article.recent-item").filter(has_text=TITLE).get_by_text(label, exact=True)
    expect(history_card).to_be_visible()
    profiles_code, profiles = request(page, "/api/v1/child-profiles")
    assert profiles_code == 200 and len(profiles["data"]["items"]) == 1
    detail_code, body = request(page, url)
    assert detail_code == 200
    detail = body["data"]
    session = detail["session"]
    assert (session["readingSessionId"], session["status"], session["revision"], session["visitedPageNumbers"]) == (
        sid, expected_status, terminal["revision"], pages)
    assert [e["eventType"] for e in detail["events"]] == [e["event_type"] for e in rows["events"]]
    if started:
        report = detail["report"]
        assert (report["readingSessionId"], report["status"], report["summary"]) == (sid, "READY", rows["reports"][0]["summary"])
    else:
        assert detail["report"] is None
    unlocked_state, unlocked_rows = stored(path, sid)
    assert unlocked_state["revoked_at"] is not None and unlocked_rows == rows
    assert {**unlocked_state, "revoked_at": None} == terminal
    calls_after_unlock = len(calls)
    refreshes = []
    for _ in range(2):
        with page.expect_response(lambda r: r.url.partition("?")[0].endswith("/reading-sessions")
                                  and r.request.method == "GET") as history:
            page.reload()
        assert history.value.status == 200 and history.value.json()["data"]["items"] == [session]
        expect(history_card).to_be_visible()
        status, refreshed = request(page, url)
        assert status == 200 and refreshed["data"] == detail
        assert stored(path, sid) == (unlocked_state, rows) and len(calls) == calls_after_unlock
        refreshes.append({"historyHttp": history.value.status, "detailHttp": status, **counts(rows),
                          "revision": session["revision"], "modelCallsAcrossCases": len(calls)})

    return {"case": "started" if started else "notStarted", "sessionId": sid,
            "issuedAt": utc_at(case["issued"]), "expiresAt": utc_at(case["expires"]), "firstExpiredRequestAt": utc_at(timestamp),
            "elapsedSinceGrantMs": timestamp - case["issued"], "beforeExpiry": case["before"],
            "firstExpiredProbe": {"path": url, "http": denied[url], "before": probe_state(before),
                                  "after": probe_state(after), "peerSessionId": peer["sid"],
                                  "peerBefore": probe_state(peer_before), "peerAfter": probe_state(peer_after)},
            "afterExpiryBeforeUnlock": {"denied": denied, "status": terminal["status"], "reason": reason,
                                        "endedAt": utc_at(terminal["ended_at"]), "grantRevoked": False, **counts(rows)},
            "childRefresh": {"profilesHttp": identity.value.status, "route": child_route, "heading": expired_heading,
                             "unlockButtonVisible": True, **counts(rows)},
            "afterUnlock": {"unlockHttp": unlock.value.status, "profilesHttp": profiles_code, "sessionHttp": detail_code,
                            "historyLabel": label, "visitedPageNumbers": pages, "report": detail["report"]},
            "guardianRefreshes": refreshes}


def test_natural_expiry(tmp_path, monkeypatch):
    path = tmp_path / "fixture.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    seed_fixture(path)
    calls = []

    def provider(req):
        assert str(req.url) == "https://fixture.invalid/model"
        prompt = json.loads(req.content)["messages"]
        assert "本次共读阅读了第 1 页。" in "\n".join(m["content"] for m in prompt)
        calls.append("reading report")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": '{"suggestionId":"NONE"}'}}]})

    monkeypatch.setattr("app.main.ModelGateway", partial(ModelGateway, transport=httpx.MockTransport(provider)))
    with local_app(tmp_path) as (origin, pin), sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", chromium_sandbox=True,
                                             args=["--ignore-certificate-errors-spki-list=" + pin])
        cases = []
        for started, account in ((True, "g13-started"), (False, "g13-waiting")):
            page = browser.new_page(locale="zh-CN")  # Independent browser context and cookie jar.
            password = secrets.token_urlsafe(18)
            sid, expires_at = grant(page, origin, account, password)
            url = f"/api/v1/reading-sessions/{sid}"
            if started:
                status, body = request(page, url + "/events", {"eventType": "STARTED", "expectedRevision": 0,
                                       "pageNumber": 1, "clientEventId": "g13-start-before-expiry"})
                assert status == 201 and body["data"]["revision"] == 1
            status, body = request(page, url)
            assert status == 200 and body["data"]["report"] is None
            before, rows = stored(path, sid)
            expected = "IN_PROGRESS" if started else "CREATED"
            assert before["status"] == body["data"]["session"]["status"] == expected
            assert before["revoked_at"] is None and not rows["reports"] and not calls
            assert before["expires_at"] - before["issued_at"] == 300_000
            assert abs(datetime.fromisoformat(expires_at).timestamp() - before["expires_at"] / 1000) < 0.001
            profile_status, forbidden = request(page, "/api/v1/child-profiles")
            assert profile_status == 403 and forbidden["error"]["code"] == "FORBIDDEN"
            print(f"Session {sid} {expected}, expires {utc_at(before['expires_at'])}", flush=True)
            cases.append({"initial": (before, rows), "started": started, "account": account, "password": password, "page": page, "sid": sid,
                          "issued": before["issued_at"], "expires": before["expires_at"], "before": {
                              "sessionHttp": status, "profilesHttp": profile_status, "sessionStatus": expected, **counts(rows)}})
        assert cases[0]["sid"] != cases[1]["sid"]
        deadline = max(c["expires"] for c in cases)
        while time.time_ns() // 1_000_000 <= deadline:
            time.sleep(min(1.0, max(0.01, deadline / 1000 - time.time())))
        outcomes = [check_expired(path, cases[0], cases[1], calls), check_expired(path, cases[1], cases[0], calls)]
        assert calls == ["reading report"]
        result = {"commit": subprocess.check_output(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
            "atUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "browser": "Chrome " + browser.version,
            "playwright": version("playwright"), "pytest": version("pytest"), "modelTransport": "httpx.MockTransport",
            "modelCalls": len(calls), "grantSeconds": 300, "cases": outcomes}
        browser.close()
    out = tmp_path / "result.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"G13 result: {out}")
