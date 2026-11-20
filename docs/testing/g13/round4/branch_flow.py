"""G13 round four: follow two routes through the same published fixture in Chrome."""
import json
import re
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
from verify import ModelGateway, ROOT, TITLE, local_app, seed_fixture, start_reading

ENDINGS = {2: ("到芦苇旁", "白鹭在芦苇旁接住了纸船。"), 3: ("去石桥边", "青蛙在石桥边收好了纸船。")}
SUGGESTION = "可以和孩子再看一页已读内容，由孩子自愿选择是否聊一聊。"


def api(page, url, payload=None):
    status, body = page.evaluate("""async ([url, data]) => {
        const options = data === null ? {} : {method: 'POST',
            headers: {'Content-Type':'application/json', 'X-CSRF-Token':decodeURIComponent(
                document.cookie.split('; ').find(c => c.startsWith('zhiya_csrf=')).slice('zhiya_csrf='.length))},
            body:JSON.stringify(data)};
        const r = await fetch(url, options);
        return [r.status, await r.json()];
    }""", [url, payload])
    assert "data" in body, (status, body.get("error"))
    return status, body["data"]


def reload_reader(page, url):
    with page.expect_response(lambda r: r.url.endswith(url) and r.request.method == "GET") as restored:
        page.reload()
    assert restored.value.status == 200
    return restored.value.json()["data"]


def snapshot(path, sid):
    with closing(sqlite3.connect(path)) as db:
        session = db.execute("""SELECT p.guardian_id, s.profile_id, s.version_id, s.mode, s.status,
            s.revision, s.current_page_no, s.visited_pages_json, s.end_reason
            FROM reading_sessions s JOIN child_profiles p ON p.id=s.profile_id WHERE s.id=?""", (sid,)).fetchone()
        events = db.execute("""SELECT id, event_type, page_no, branch_id, revision_after FROM interaction_events
            WHERE session_id=? ORDER BY id""", (sid,)).fetchall()
        writes = db.execute("""SELECT id, client_request_id, status FROM request_executions
            WHERE scope_type='SESSION_EVENT' AND scope_id=? ORDER BY id""", (sid,)).fetchall()
        reports = db.execute("SELECT status,summary,activities_json FROM reading_reports WHERE session_id=?",
                             (sid,)).fetchall()
        return session, events, writes, reports


def test_two_branch_routes_and_reports(tmp_path, monkeypatch):
    path = tmp_path / "fixture.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    seed_fixture(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("UPDATE story_versions SET branching=1 WHERE id=11")
        for dest, (label, text) in ENDINGS.items():
            db.execute("""INSERT INTO story_pages (version_id,page_no,body_text,image_ref)
                VALUES (11,?,?,'fixture-no-image')""", (dest, text))
            db.execute("""INSERT INTO story_branches (id,version_id,from_page_no,next_page_no,choice_label,review_status)
                VALUES (?,11,1,?,?,'APPROVED')""", (500 + dest, dest, label))

    prompts = []

    def provider(request):
        assert str(request.url) == "https://fixture.invalid/model"
        prompts.append(json.loads(request.content)["messages"])
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": '{"suggestionId":"REVISIT_PAGE"}'}}]})

    monkeypatch.setattr("app.main.ModelGateway", partial(ModelGateway, transport=httpx.MockTransport(provider)))
    cases = []
    finished = {}
    with local_app(tmp_path) as (origin, pin), sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", chromium_sandbox=True,
                                             args=["--ignore-certificate-errors-spki-list=" + pin])
        page = browser.new_page(locale="zh-CN")
        sid, start_key = start_reading(page, origin)
        status, book = api(page, "/api/v1/story-versions/11")
        assert status == 200 and book["title"] == TITLE
        options = {b["choiceLabel"]: b for b in book["branches"]}
        texts = {p["pageNumber"]: p["text"] for p in book["pages"]}
        assert set(options) == {label for label, _ in ENDINGS.values()}

        for i, (dest, (label, ending)) in enumerate(ENDINGS.items(), 1):
            if i > 1:
                page.locator("nav.top-nav").get_by_role("button", name="绘本馆").click()
                page.get_by_role("heading", name=TITLE, exact=True).wait_for()
                page.get_by_role("button", name="开始阅读", exact=True).click()
                with page.expect_response(lambda r: r.url.endswith("/events") and r.request.method == "POST") as started:
                    page.get_by_role("button", name="开始本次阅读", exact=True).click()
                assert started.value.status == 201
                sid = int(started.value.url.split("/")[-2])
                start_key = started.value.request.post_data_json["clientEventId"]
            assert sid not in finished
            url = f"/api/v1/reading-sessions/{sid}"
            branch = options[label]
            assert (branch["fromPageNumber"], branch["nextPageNumber"], branch["reviewStatus"]) == (1, dest, "APPROVED")
            body = {"branchId": branch["branchId"], "expectedRevision": 1, "clientEventId": f"g13-route-{dest}"}
            status, chosen = api(page, url + "/branches", body)
            route = [1, dest]
            assert status == 201 and chosen["readingSessionId"] == sid and chosen["branchId"] == branch["branchId"]
            assert (chosen["currentPage"], chosen["visitedPageNumbers"], chosen["revision"]) == (dest, route, 2)
            assert (chosen["nextPage"]["pageNumber"], chosen["nextPage"]["text"]) == (dest, ending)
            before = snapshot(path, sid)
            replay_status, replay = api(page, url + "/branches", body)
            assert replay_status == 200 and replay == chosen and snapshot(path, sid) == before
            session, events, writes, reports = before
            assert session[2:7] == (11, "PARENT_CHILD", "IN_PROGRESS", 2, dest)
            assert json.loads(session[7]) == route and session[8] is None and reports == []
            assert [e[1:] for e in events] == [("STARTED", 1, None, 1), ("BRANCH_CHOSEN", 1, branch["branchId"], 2)]
            assert events[-1][0] == chosen["interactionEventId"]
            assert [w[1:] for w in writes] == [(start_key, "SUCCEEDED"), (body["clientEventId"], "SUCCEEDED")]

            restored = reload_reader(page, url)
            active = restored["session"]
            assert (active["readingSessionId"], active["currentPage"], active["visitedPageNumbers"],
                    active["status"], active["revision"]) == (sid, dest, route, "IN_PROGRESS", 2)
            assert [(e["interactionEventId"], e["eventType"], e["pageNumber"]) for e in restored["events"]] == [
                (e[0], e[1], e[2]) for e in events]
            assert restored["report"] is None
            expect(page.get_by_text(f"第 {dest} 页 · 共 3 页 · 后端会话已同步", exact=True)).to_be_visible()
            page.get_by_text(ending, exact=True).first.wait_for()
            with page.expect_response(lambda r: r.url.endswith(url + "/completions") and r.request.method == "POST") as done:
                page.get_by_role("button", name=re.compile("完成$")).click()
            assert done.value.status == 200
            completion = done.value.json()["data"]
            facts = f"本次共读阅读了第 1、{dest} 页。"
            terminal = dict(active, status="FINISHED", revision=3)
            assert completion["session"] == terminal
            assert completion["report"] == {"readingSessionId": sid, "summary": facts, "activities": [], "status": "PENDING"}
            deadline = time.monotonic() + 10
            while True:
                code, detail = api(page, url)
                assert code == 200
                if detail["report"]["status"] != "PENDING":
                    break
                assert time.monotonic() < deadline, f"Session {sid} report stayed PENDING"
                time.sleep(0.05)
            final_report = detail["report"]
            assert final_report == {"readingSessionId": sid, "status": "READY", "summary": facts + SUGGESTION, "activities": []}
            assert detail == {"session": terminal, "report": final_report, "events": restored["events"]}
            assert reload_reader(page, url) == detail
            page.get_by_text("小结已生成。", exact=True).wait_for()
            page.get_by_text(final_report["summary"], exact=True).wait_for()
            for number, (_, text) in ENDINGS.items():
                if number != dest:
                    expect(page.get_by_text(text, exact=True)).to_have_count(0)
            assert len(prompts) == i
            prompt = "\n".join(m["content"] for m in prompts[-1])
            included = [number for number, text in texts.items() if text in prompt]
            assert sorted(included) == route and facts in prompt
            final_session, final_events, final_writes, final_reports = snapshot(path, sid)
            assert final_session == (*session[:4], "FINISHED", 3, dest, session[7], "USER_FINISH")
            assert final_events == events and final_writes[:-1] == writes
            assert final_writes[-1][1:] == (done.value.request.post_data_json["clientEventId"], "SUCCEEDED")
            assert final_reports == [("READY", final_report["summary"], "[]")]
            finished[sid] = detail
            page.locator("nav.top-nav").get_by_role("button", name="成长记录").click()
            cards = page.locator("article.recent-item").filter(has_text=TITLE).filter(has_text="浏览 2 页 · 已完成")
            expect(cards).to_have_count(i)
            for history_id in finished:
                expect(cards.get_by_text(f"阅读记录 #{history_id} · 浏览 2 页 · 已完成", exact=True)).to_be_visible()
            code, history = api(page, "/api/v1/reading-sessions")
            assert code == 200 and len(history["items"]) == i
            assert {s["readingSessionId"]: s for s in history["items"]} == {k: v["session"] for k, v in finished.items()}
            cases.append({"sessionId": sid, "guardianId": session[0], "profileId": session[1], "storyVersionId": 11,
                          "branchId": branch["branchId"], "label": label, "path": active["visitedPageNumbers"],
                          "branchHttp": [status, replay_status], "branchEventId": chosen["interactionEventId"],
                          "branchEventSourcePage": events[-1][2], "revisionAfterReplay": session[5],
                          "eventsAfterReplay": len(events), "executionsAfterReplay": len(writes),
                          "finishHttp": done.value.status, "endReason": final_session[-1], "finalRevision": terminal["revision"],
                          "reportStates": [completion["report"]["status"], final_report["status"]],
                          "summary": final_report["summary"], "promptContainedPageBodies": included,
                          "dbEventsAfterFinish": len(final_events), "dbReports": len(final_reports),
                          "visibleHistoryRows": cards.count()})

        assert len({(c["guardianId"], c["profileId"]) for c in cases}) == 1
        for sid, expected in finished.items():
            assert api(page, f"/api/v1/reading-sessions/{sid}") == (200, expected)
            assert snapshot(path, sid)[-1] == [("READY", expected["report"]["summary"], "[]")]
        with closing(sqlite3.connect(path)) as db:
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
        commit = subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT),
                                          "rev-parse", "HEAD"], text=True).strip()
        result = {"commit": commit, "atUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "browser": "Chrome " + browser.version, "pytest": version("pytest"), "playwright": version("playwright"),
                  "modelTransport": "httpx.MockTransport", "modelCalls": len(prompts), "routes": cases}
        browser.close()
    output = tmp_path / "result.json"
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"G13 result: {output}")
