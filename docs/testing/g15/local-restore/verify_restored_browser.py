"""Run against the source stack, switch to its restored volume at the prompt, then log in again."""
import argparse
import json
import re
import secrets
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-https"))
from verify_browser import TITLE, checked_data, choose_profile, login, match_response, read_history


ORIGIN = "https://127.0.0.1:18443"
IMAGE_PATH = "/api/v1/media/asset-11-page-1"


def page_illustration(page, fixture):
    image = page.locator(f'.flipbook-book img[src="{IMAGE_PATH}"]:visible').first
    expect(image).to_be_visible()
    assert image.get_attribute("alt") == f"{TITLE} · 第 1 页"
    proof = image.evaluate("""async img => {
        await img.decode();
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
        const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        const res = await fetch(img.currentSrc, {cache: 'no-store', credentials: 'same-origin'});
        const raw = await res.arrayBuffer();
        async function digest(data) {
            const hash = await crypto.subtle.digest('SHA-256', data);
            return Array.from(new Uint8Array(hash), x => x.toString(16).padStart(2, '0')).join('');
        }
        return {path: new URL(img.currentSrc).pathname, http: res.status, mime: res.headers.get('Content-Type'),
                naturalWidth: canvas.width, naturalHeight: canvas.height, bytes: raw.byteLength,
                pngSha256: await digest(raw), rgbaSha256: await digest(pixels)};
    }""")
    assert proof["http"] == 200 and proof["path"] == IMAGE_PATH
    assert proof["mime"].split(";")[0] == "image/png" and proof["bytes"] == fixture["bytes"]
    assert (proof["naturalWidth"], proof["naturalHeight"]) == (fixture["width"], fixture["height"])
    assert proof["pngSha256"] == fixture["sha256"], "Page PNG differs from the fixture"
    assert proof["rgbaSha256"] == fixture["rgbaSha256"], "Decoded pixels differ from the fixture"
    return proof


def restored_volume(inspection_path, source):
    if not inspection_path.is_file():
        raise ValueError("Restored backend inspection is missing; run start-b before the second login")
    target = json.loads(inspection_path.read_bytes())
    expected = source["volume"].removesuffix("-b-source-data") + "-b-restored-data"
    if target["volume"] != expected or target["image"] != source["image"]:
        raise ValueError("Restored backend volume or image differs from this rehearsal")
    return target["volume"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spki", required=True, help="Public key pin for the loopback HTTPS certificate")
    parser.add_argument("--image-fixture", required=True, type=Path, help="This run's evidence/b-image.json")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    fixture = json.loads(args.image_fixture.read_bytes())
    source_backend = json.loads(args.image_fixture.with_name("b-source-service-backend.json").read_bytes())
    restored_backend_path = args.image_fixture.with_name("b-restored-service-backend.json")
    if not source_backend["volume"].endswith("-b-source-data") or restored_backend_path.exists():
        parser.error("Use the current source deployment evidence before starting the restored stack")
    credentials = {"account": "restore-" + secrets.token_hex(6), "password": secrets.token_urlsafe(18)}
    flags = ["--ignore-certificate-errors-spki-list=" + args.spki, "--disable-background-networking"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", chromium_sandbox=True, args=flags)
        chrome_version = browser.version
        page = browser.new_page(locale="zh-CN", viewport={"width": 1440, "height": 960})
        before_login = login(page, ORIGIN, credentials, register=True)
        profile_id = choose_profile(page)
        page.locator("nav.top-nav").get_by_role("button", name="绘本馆").click()
        with page.expect_response(match_response("/reading-sessions", "POST")) as created:
            page.get_by_role("button", name="开始阅读", exact=True).click()
        sid = checked_data(created.value, 201)["readingSessionId"]
        assert created.value.request.post_data_json["childProfileId"] == profile_id
        session_path = f"/reading-sessions/{sid}"
        with page.expect_response(match_response(session_path + "/events", "POST")) as started:
            page.get_by_role("button", name="开始本次阅读", exact=True).click()
        assert checked_data(started.value, 201)["revision"] == 1
        reader_hash = page.evaluate("location.hash")
        assert reader_hash == f"#reader/{sid}"
        original_image = page_illustration(page, fixture)
        with page.expect_response(match_response(session_path + "/completions", "POST")) as finished:
            page.get_by_role("button", name=re.compile("完成$")).click()
        assert checked_data(finished.value)["session"]["status"] == "FINISHED"
        page.wait_for_function("""async path => {
            const res = await fetch(path, {cache: 'no-store'});
            return res.ok && (await res.json()).data.report?.status === 'READY';
        }""", arg="/api/v1" + session_path, polling=250, timeout=15000)
        label, original_record = read_history(page, ORIGIN, sid, reader_hash)
        assert page_illustration(page, fixture) == original_image
        browser.close()

        print(f"READY_FOR_RESTORE session={sid} profile={profile_id} report=READY", flush=True)
        input("Switch to the newly restored volume and wait for health; press Enter to verify: ")

        target_volume = restored_volume(restored_backend_path, source_backend)
        browser = pw.chromium.launch(channel="chrome", chromium_sandbox=True, args=flags)
        page = browser.new_page(locale="zh-CN", viewport={"width": 1440, "height": 960})
        assert not page.context.cookies()
        page.goto(ORIGIN + "/#auth")
        anonymous = page.evaluate("""async path => (await fetch(path, {cache:'no-store'})).status""", IMAGE_PATH)
        assert anonymous == 401
        after_login = login(page, ORIGIN, credentials)
        restored_label, restored_record = read_history(page, ORIGIN, sid, reader_hash)
        restored_image = page_illustration(page, fixture)
        assert before_login == after_login and label == restored_label
        assert original_record == restored_record and original_image == restored_image
        browser.close()

    result = {
        "verifiedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": ORIGIN, "browser": "Chrome " + chrome_version, "playwright": version("playwright"),
        "sessionId": sid, "profileId": profile_id, "readerHash": reader_hash,
        "source": {"login": before_login, "historyCard": label, "detail": original_record, "pageImage": original_image},
        "restored": {"newBrowser": True, "backendDataVolume": target_volume,
                     "anonymousMediaHttp": anonymous, "login": after_login,
                     "historyCard": restored_label, "detail": restored_record, "pageImage": restored_image},
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified restored session {sid} and page PNG. Output: {args.output}", flush=True)


if __name__ == "__main__":
    main()
