"""Frozen-candidate G13 rehearsal. The admin and family exist only in a new local QA DB."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "database")]
from fastapi.testclient import TestClient
from app.main import create_app
from prepare_demo_data import connect, ensure_account, ensure_database


def prepare(directory):
    directory.mkdir()
    db_path = directory / "rehearsal.sqlite3"
    assert ensure_database(db_path)
    os.environ["ZHIYA_DB_PATH"] = str(db_path)
    password = secrets.token_urlsafe(24)
    with closing(connect(db_path)) as db, db:
        ensure_account(db, "course-qa-editor", password, "ADMIN")
    approved = []
    drafts = json.loads((ROOT / "data/seed/course_knowledge_drafts.json").read_text("utf-8"))
    with TestClient(create_app(), base_url="https://testserver") as client:
        login = client.post("/api/v1/auth/sessions", json={"account": "course-qa-editor", "password": password})
        assert login.status_code == 201, login.status_code
        client.headers["X-CSRF-Token"] = client.cookies["zhiya_csrf"]
        for draft in drafts:
            book = int(draft["itemKey"].split("-")[-1])
            story = client.patch(f"/api/v1/admin/story-versions/{book}", json={"status": "PUBLISHED"})
            assert story.status_code == 200, (book, story.text)
            created = client.post("/api/v1/admin/knowledge-items", json=draft)
            assert created.status_code == 201, created.text
            item = created.json()["data"]["knowledgeItemId"]
            checked = client.patch(f"/api/v1/admin/knowledge-items/{item}", json={"status": "APPROVED"})
            assert checked.status_code in (200, 201), checked.text
            approved.append({"storyVersionId": book, "itemKey": draft["itemKey"],
                             "knowledgeItemId": checked.json()["data"]["knowledgeItemId"]})
    assert len({a["storyVersionId"] for a in approved}) == 8
    model_db = directory / "g14-input.sqlite3"
    with closing(connect(db_path)) as source, closing(sqlite3.connect(model_db)) as snapshot:
        source.backup(snapshot)
    with closing(connect(db_path)) as db:
        published = db.execute("SELECT count(*) FROM story_versions WHERE status='PUBLISHED'").fetchone()[0]
    return {"database": db_path.name, "publishedInFixture": published, "modelDatabase": model_db.name,
            "modelDatabaseSha256": hashlib.sha256(model_db.read_bytes()).hexdigest(),
            "reviewScope": "automated QA in an isolated SQLite database",
            "items": approved}


def exercise(page, origin, browser_version, report):
    page.set_default_timeout(45000)
    page.goto(origin)
    sid = page.evaluate("""async () => {
      async function post(path, data) {
        const csrf = document.cookie.split('; ').find(c => c.startsWith('zhiya_csrf='))?.slice(11);
        const r = await fetch('/api/v1/' + path, {method:'POST', headers:{
          'Content-Type':'application/json','X-CSRF-Token':decodeURIComponent(csrf || '')},
          body:JSON.stringify(data)});
        if (r.status !== 201) throw Error(path + ' HTTP ' + r.status);
        return (await r.json()).data;
      }
      const credentials = {account:'course-' + crypto.randomUUID().slice(0,12),password:crypto.randomUUID()};
      await post('auth/registrations', credentials);
      await post('auth/sessions', credentials);
      const child = await post('child-profiles',{ageBand:'AGE_3_4',preferredMinutes:10});
      return (await post('reading-sessions',{childProfileId:child.childProfileId,
          storyVersionId:2, mode:'PARENT_CHILD'})).readingSessionId;
    }""")
    report.update(browser="Chrome " + browser_version, sessionId=sid)
    path = f"/api/v1/reading-sessions/{sid}"
    page.goto(origin + f"/#reader/{sid}")
    with page.expect_response(lambda r: "/api/v1/storybooks?" in r.url and r.request.method == "GET") as shelf:
        page.reload()
    assert shelf.value.status == 200 and shelf.value.finished() is None
    button = lambda name: page.get_by_role("button", name=name, exact=True)
    with page.expect_response(lambda r: r.url.endswith("/guidance") and r.request.method == "POST") as event:
        button("开始本次阅读").click()
    report["guidanceHttp"] = event.value.status
    assert event.value.status == 201
    button("问问芽芽").click()
    page.locator("#voice-transcript").fill("小狐狸站在哪里？")
    button("确认文字").click()
    with page.expect_response(lambda r: r.url.endswith("/questions") and r.request.method == "POST") as ask:
        button("提交问题").click()
    report["askHttp"] = ask.value.status
    assert ask.value.status == 201
    answer = ask.value.json()["data"]
    report["answer"] = {k: answer[k] for k in ("interactionEventId","status","outputText","sources","revision")}
    assert answer["status"] == "GENERATED" and answer["sources"] and "门口" in answer["outputText"], report["answer"]
    replay = page.evaluate("""async ([path, body]) => {
      const csrf = document.cookie.split('; ').find(c => c.startsWith('zhiya_csrf=')).slice(11);
      const r = await fetch(path + '/questions',{method:'POST',headers:{'Content-Type':'application/json',
        'X-CSRF-Token':decodeURIComponent(csrf)},body:JSON.stringify(body)});
      return [r.status, (await r.json()).data];
    }""", [path, ask.value.request.post_data_json])
    assert replay[0] == 200 and replay[1] == answer
    page.reload()
    page.get_by_text(answer["outputText"], exact=True).first.wait_for()
    detail = page.evaluate("async path => (await (await fetch(path)).json()).data", path)
    asks = [e for e in detail["events"] if e["eventType"] == "ASK"]
    assert len(asks) == 1 and asks[0]["interactionEventId"] == answer["interactionEventId"]
    report.update(replayHttp=replay[0], refreshedAskCount=len(asks))
    with page.expect_response(lambda r: r.url.endswith("/voice/syntheses") and r.request.method == "POST") as tts:
        button("播放故事回答").click()
    report["ttsHttp"] = tts.value.status
    assert tts.value.status == 201, tts.value.text()
    # Send synthetic playback through ASR using the family's authenticated browser.
    asr = page.evaluate("""async ([sid, rev, url]) => {
      const audio = await fetch(url);
      if (!audio.ok) throw Error('audio HTTP ' + audio.status);
      const bytes = await audio.blob();
      if (bytes.size <= 100) throw Error('empty playback');
      const data = new FormData();
      data.append('file',bytes,'synthetic.wav');
      for (const [k,v] of Object.entries({readingSessionId:sid,pageNumber:1,revision:rev})) data.append(k,String(v));
      const csrf = document.cookie.split('; ').find(c => c.startsWith('zhiya_csrf=')).slice(11);
      const r = await fetch('/api/v1/voice/transcriptions',{method:'POST',headers:{
        'X-CSRF-Token':decodeURIComponent(csrf)},body:data});
      return [r.status,await r.json(),bytes.size];
    }""", [sid, answer["revision"], tts.value.json()["data"]["audioUrl"]])
    report.update(audioBytes=asr[2], asrHttp=asr[0])
    assert asr[0] == 200 and asr[1]["data"]["text"].strip(), asr[1]
    report["recognizedText"] = asr[1]["data"]["text"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "browser-live", "model-live"])
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--tested-commit", required=True)
    args = parser.parse_args()
    git = ["git","-c","safe.directory=" + ROOT.as_posix(),"-C",str(ROOT)]
    sha = subprocess.check_output([*git,"rev-parse","HEAD"], text=True).strip()
    if sha != args.tested_commit or subprocess.check_output([*git,"status","--porcelain","--untracked-files=normal"]):
        parser.error("请从无改动的候选提交运行，--tested-commit 填写完整 SHA")
    directory = args.directory.resolve()
    report = {"testedCommit": sha, "phase": args.phase, "passed": False}
    if args.phase == "prepare":
        report.update(prepare(directory), passed=True)
        (directory / "prepare.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        return
    prepared = json.loads((directory / "prepare.json").read_text("utf-8"))
    assert prepared["testedCommit"] == sha and prepared["passed"]
    if args.phase == "model-live":
        model_db = directory / prepared["modelDatabase"]
        assert hashlib.sha256(model_db.read_bytes()).hexdigest() == prepared["modelDatabaseSha256"]
        model_dir = directory / "g14"
        model_dir.mkdir()  # A second invocation must not spend another 48 calls by accident.
        subprocess.run([sys.executable, str(ROOT / "qa/p6/run_model_compare.py"), "--db", str(model_db),
                        "--outdir", str(model_dir), "--conditions", "baseline,domain", "--max-calls","48","--live"],
                       check=True)
        subprocess.run([sys.executable, str(ROOT / "qa/p6/summarize_model_compare.py"), "--outdir", str(model_dir)],
                       check=True)
        return
    os.environ["ZHIYA_DB_PATH"] = str(directory / prepared["database"])
    sys.path.insert(0, str(ROOT / "docs/testing/g13/round2"))
    from verify import local_app
    from playwright.sync_api import sync_playwright
    target = directory / "browser-live.json"
    with target.open("x", encoding="utf-8") as output:
        try:
            app = create_app()
            with tempfile.TemporaryDirectory(dir=directory) as certs, local_app(Path(certs), application=app) as (origin,pin):
                with sync_playwright() as driver:
                    browser = driver.chromium.launch(channel="chrome", args=["--ignore-certificate-errors-spki-list=" + pin])
                    try:
                        exercise(browser.new_page(locale="zh-CN"), origin, browser.version, report)
                    finally:
                        browser.close()
            report["passed"] = True
        finally:
            json.dump(report, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
