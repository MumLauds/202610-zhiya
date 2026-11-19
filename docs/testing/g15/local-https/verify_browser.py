"""Drive a local HTTPS deployment. Restart the stack at the terminal prompt."""
import argparse
import base64
import hashlib
import re
import secrets
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from browser_evidence import EDITED, TRANSCRIPT
from evidence_io import publish_json


TITLE = "纸船停在哪里"
PAGE_TEXT = "小园把一只纸船放进水里。"
SUMMARY = "本次共读阅读了第 1 页。"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def match_response(path, method="GET"):
    return lambda r: urlsplit(r.url).path == "/api/v1" + path and r.request.method == method


def checked_data(response, status=200):
    require(response.status == status,
            f"{response.request.method} {response.url} returned {response.status}, expected {status}")
    return response.json()["data"]


def login(page, origin, credentials, register=False):
    page.goto(origin + "/#auth")
    require(page.evaluate("window.isSecureContext && location.protocol === 'https:'"),
            "Page is not a secure HTTPS context")
    if register:
        status = page.evaluate("""async data => (await fetch('/api/v1/auth/registrations', {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)
        })).status""", credentials)
        require(status == 201, "Guardian registration failed")
    page.get_by_placeholder("3–64 个字符").fill(credentials["account"])
    page.get_by_placeholder("至少 8 位").fill(credentials["password"])
    with page.expect_response(match_response("/auth/sessions", "POST")) as logged_in:
        page.get_by_role("button", name="登录家长空间").click()
    checked_data(logged_in.value, 201)
    expect(page.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
    cookie = next(c for c in page.context.cookies() if c["name"] == "zhiya_session")
    require(cookie["secure"] and cookie["httpOnly"], "Invalid session cookie flags")
    asset = page.locator('script[type="module"][src]').get_attribute("src")
    require(asset.startswith("/assets/"), "Missing bundled frontend asset")
    asset_hash = page.evaluate("""async path => {
        const res = await fetch(path);
        if (!res.ok) throw new Error('Cannot read frontend asset: ' + res.status);
        const digest = await crypto.subtle.digest('SHA-256', await res.arrayBuffer());
        return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
    }""", asset)
    return {"loginHttp": 201, "secureSessionCookie": True, "httpOnlySessionCookie": True,
            "frontendAsset": asset, "frontendSha256": asset_hash}


def choose_profile(page):
    page.get_by_role("button", name="开始阅读", exact=True).click()
    ids = {}
    for name in ("预演小芽", "预演小禾"):
        page.get_by_role("button", name="＋ 添加孩子").click()
        page.get_by_label("昵称").fill(name)
        with page.expect_response(match_response("/child-profiles", "POST")) as saved:
            page.get_by_role("button", name="保存档案").click()
        ids[name] = checked_data(saved.value, 201)["childProfileId"]
        expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
    target = page.locator("article.child-card").filter(has_text="预演小芽")
    expect(target).not_to_have_class(re.compile(r"\bactive\b"))
    target.get_by_role("button", name="切换").click()
    expect(target).to_have_class(re.compile(r"\bactive\b"))
    return ids["预演小芽"]


def record_and_confirm(page):
    page.get_by_role("button", name=re.compile("语音识别")).click()
    dialog = page.get_by_role("dialog", name="本页问题")
    dialog.get_by_role("button", name="开始录音").click()
    expect(dialog.get_by_role("heading", name="正在录音，点停止后识别")).to_be_visible()
    time.sleep(1.5)  # Capture frames from Chrome's test microphone with the actual MediaRecorder.
    with page.expect_response(match_response("/voice/transcriptions", "POST")) as uploaded:
        dialog.get_by_role("button", name="停止并识别").click()
    data = checked_data(uploaded.value)
    require(data["text"] == TRANSCRIPT and data["reliable"] is False, "Unexpected voice transcription")
    editor = dialog.get_by_label("待确认的转写")
    expect(editor).to_have_value(TRANSCRIPT)
    editor.fill(EDITED)
    dialog.get_by_role("button", name="确认转写").click()
    expect(dialog.get_by_role("status")).to_have_text("已确认的转写")
    expect(dialog.get_by_text(EDITED, exact=True)).to_be_visible()
    dialog.get_by_role("button", name="关闭", exact=True).click()
    expect(dialog).not_to_be_visible()
    return {"transcriptionHttp": uploaded.value.status, "recognized": data["text"],
            "confirmedInDialog": EDITED}


def read_history(page, origin, sid, reader_hash):
    page.locator("nav.top-nav").get_by_role("button", name="成长记录").click()
    label = f"阅读记录 #{sid} · 浏览 1 页 · 已完成"
    card = page.locator("article.recent-item").filter(has_text=TITLE)
    expect(card).to_have_count(1)
    expect(card.get_by_text(label, exact=True)).to_be_visible()
    # The history card has no detail link. Reopen the exact address captured at session creation.
    page.goto(origin + "/" + reader_hash)
    expect(page.get_by_role("heading", name="本次共读小结")).to_be_visible()
    expect(page.get_by_text(SUMMARY, exact=True)).to_be_visible()
    # Read the persisted detail from the same authenticated page after its route has settled.
    code, body = page.evaluate("""async path => {
        const res = await fetch(path, {cache: 'no-store'});
        return [res.status, await res.json()];
    }""", f"/api/v1/reading-sessions/{sid}")
    require(code == 200, "Could not read session detail")
    detail = body["data"]
    require(detail["session"]["readingSessionId"] == sid and detail["session"]["status"] == "FINISHED",
            "Expected the completed session")
    require(detail["session"]["visitedPageNumbers"] == [1] and detail["session"]["revision"] == 2,
            "Unexpected reading revision or pages")
    require([(event["eventType"], event["pageNumber"]) for event in detail["events"]] == [("STARTED", 1)],
            "Unexpected reading events")
    require(detail["report"] == {"readingSessionId": sid, "status": "READY", "summary": SUMMARY, "activities": []},
            "Unexpected reading report")
    return label, detail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://127.0.0.1:18443")
    parser.add_argument("--spki", required=True, help="Temporary HTTPS certificate public key pin")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    target = urlsplit(args.origin)
    if target.scheme != "https" or target.hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("--origin must be a local HTTPS loopback origin")
    origin = args.origin.rstrip("/")
    credentials = {"account": "g15-" + secrets.token_hex(6), "password": secrets.token_urlsafe(18)}
    flags = ["--ignore-certificate-errors-spki-list=" + args.spki, "--use-fake-device-for-media-stream",
             "--disable-background-networking"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", chromium_sandbox=True, args=flags)
        browser_version = browser.version
        context = browser.new_context(locale="zh-CN")
        context.grant_permissions(["microphone"], origin=origin)
        page = context.new_page()
        before = login(page, origin, credentials, register=True)
        profile_id = choose_profile(page)
        page.locator("nav.top-nav").get_by_role("button", name="绘本馆").click()
        with page.expect_response(match_response("/reading-sessions", "POST")) as created:
            page.get_by_role("button", name="开始阅读", exact=True).click()
        sid = checked_data(created.value, 201)["readingSessionId"]
        require(created.value.request.post_data_json["childProfileId"] == profile_id,
                "Selected profile differs from created session")
        session_path = f"/reading-sessions/{sid}"
        with page.expect_response(match_response(session_path + "/events", "POST")) as started:
            page.get_by_role("button", name="开始本次阅读", exact=True).click()
        require(checked_data(started.value, 201)["revision"] == 1, "Unexpected start revision")
        reader_hash = urlsplit(page.url).fragment
        require(reader_hash == f"reader/{sid}", "Reader address differs from the created session")
        reader_hash = "#" + reader_hash
        with page.expect_response(match_response(session_path)) as restored:
            page.reload()
        active = checked_data(restored.value)["session"]
        require((active["status"], active["revision"], active["visitedPageNumbers"]) == ("IN_PROGRESS", 1, [1]),
                "Reload did not restore the active session")
        expect(page.get_by_text("第 1 页 · 共 1 页 · 后端会话已同步", exact=True)).to_be_visible()
        expect(page.get_by_text(PAGE_TEXT, exact=True).first).to_be_visible()
        voice = record_and_confirm(page)
        with page.expect_response(match_response(session_path + "/completions", "POST")) as ended:
            page.get_by_role("button", name=re.compile(r"完成$")).click()
        require(checked_data(ended.value)["session"]["status"] == "FINISHED",
                "Completion did not finish the session")
        # The server finalizes READY asynchronously after its completion transaction.
        page.wait_for_function("""async path => {
            const res = await fetch(path);
            return res.ok && (await res.json()).data.report?.status === 'READY';
        }""", arg="/api/v1" + session_path, polling=250, timeout=15000)
        label, original = read_history(page, origin, sid, reader_hash)
        browser.close()
        print(f"READY_FOR_RESTART session={sid} profile={profile_id} report=READY", flush=True)
        input("Restart the deployment and wait for health, then press Enter: ")
        # Fresh process and context ensure that the service has to authenticate and supply the data again.
        browser = pw.chromium.launch(channel="chrome", chromium_sandbox=True, args=flags)
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        require(not context.cookies(), "Fresh browser already has cookies")
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        with page.expect_response(match_response("/auth/sessions", "POST")) as login_response:
            after = login(page, origin, credentials)
        new_label, reloaded = read_history(page, origin, sid, reader_hash)
        require(label == new_label and original == reloaded and before == after, "Readback changed after restart")
        # CDP returns the DER certificate chain for the origin Chrome actually visited.
        certificates = cdp.send("Network.getCertificate", {"origin": origin})["tableNames"]
        if not certificates:
            raise ValueError("Chrome did not report the local HTTPS certificate")
        certificate_sha256 = hashlib.sha256(base64.b64decode(certificates[0], validate=True)).hexdigest()
        login_http_date = login_response.value.header_value("date")
        browser.close()
    result = {"atUtc": datetime.now(timezone.utc).isoformat(),
              "spkiSha256": args.spki, "certificateSha256": certificate_sha256,
              "origin": origin, "browser": "Chrome " + browser_version, "playwright": version("playwright"),
              "profileId": profile_id, "sessionId": sid, "readerHash": reader_hash, "readerRefresh": active,
              "voice": voice, "beforeRestart": {**before, "history": label, "detail": original},
              "afterRestart": {**after, "history": new_label, "detail": reloaded, "freshLogin": True,
                               "loginHttpDate": login_http_date}}
    publish_json(args.output, result, replace=True)
    print(f"Verified persisted session {sid}. Result: {args.output}", flush=True)


if __name__ == "__main__":
    main()
