"""Run browser and voice compatibility checks on isolated loopback synthetic data."""

import argparse
import asyncio
import io
import json
import logging
import os
import re
import runpy
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import wave
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
TITLE = "松鼠的纸风车"
REPLY = "小熊戴着蓝色帽子。"
TRANSCRIPT = "松鼠把什么放进信箱了？"
VERSION = 901_000
ACCOUNT = {"account": "gate-one-browser", "password": "synthetic-browser-login"}


class VoiceFixture:
    def __init__(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 48000)
        self.audio = buffer.getvalue()
        self.speech = []
        self.asr_mode = "ok"
        self.tts_fail = False

    async def transcribe(self, audio, media_type):
        from app.errors import ServiceError
        assert media_type == "audio/wav" and audio.startswith(b"RIFF")
        if self.asr_mode != "ok":
            raise ServiceError("ASR_EMPTY" if self.asr_mode == "empty" else "ASR_PROVIDER_ERROR",
                               "本地合成转写失败", 422 if self.asr_mode == "empty" else 503)
        return SimpleNamespace(text=TRANSCRIPT, reliable=True)

    async def synthesize(self, text, **kwargs):
        from app.errors import ServiceError
        self.speech.append(text)
        if self.tts_fail:
            raise ServiceError("TTS_PROVIDER_ERROR", "本地合成音频失败", 503)
        return self.audio


def post(client, path, data):
    headers = {"X-CSRF-Token": client.cookies.get("zhiya_csrf", "")}
    response = client.post("/api/v1" + path, json=data, headers=headers)
    assert response.status_code == 201, (path, response.status_code)
    return response.json()["data"]


def is_write(fragment, kind=None):
    def check(response):
        return (response.request.method == "POST" and fragment in urlsplit(response.url).path
                and (kind is None or response.request.post_data_json.get("eventType") == kind))
    return check


def require_receipt(response, action):
    data = response.json()["data"]
    assert response.status == 201 and data["agentReceipt"]["version"] == "2.0", response.status
    assert data["agentReceipt"]["action"] == action, data["agentReceipt"]
    return data


def browser_checks(origin, client, profile, voice, wires):
    result = {"desktop": [], "mobile": [], "voice": [], "browser_response_ms_local": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", args=["--disable-background-networking"])
        desktop = browser.new_context(ignore_https_errors=True, locale="zh-CN")
        page = desktop.new_page()
        page.set_default_timeout(9000)
        page.goto(origin + "/#auth")
        page.get_by_placeholder("3–64 个字符").fill(ACCOUNT["account"])
        page.get_by_placeholder("至少 8 位").fill(ACCOUNT["password"])
        with page.expect_response(is_write("/auth/sessions")) as logged:
            page.get_by_role("button", name=re.compile(r"登录(?:知芽|家长空间)")).click()
        assert logged.value.status == 201
        expect(page.get_by_role("heading", name=TITLE, exact=True).first).to_be_visible()
        result["desktop"].append("login_and_synthetic_book")

        with page.expect_response(is_write("/reading-sessions", "STARTED")) as started:
            with page.expect_response(lambda r: r.request.method == "POST" and
                                      urlsplit(r.url).path == "/api/v1/reading-sessions") as created:
                page.get_by_role("button", name="开始阅读", exact=True).first.click()
        sid = created.value.json()["data"]["readingSessionId"]
        route = f"/reading-sessions/{sid}"
        assert started.value.status == 201
        result["desktop"].append("create_start")

        def type_answer(text, action, displayed):
            editor = page.get_by_label("输入孩子的回答")
            expect(editor).to_be_enabled()
            editor.fill(text)
            before = time.perf_counter()
            with page.expect_response(is_write(route + "/events", "ANSWER")) as answered:
                page.get_by_role("button", name="提交回答", exact=True).click()
            receipt = require_receipt(answered.value, action)
            expect(page.locator(".question-reply").filter(has_text=displayed)).to_be_visible()
            result["browser_response_ms_local"].append(round(1000 * (time.perf_counter() - before), 1))
            return receipt

        type_answer("我想给叶子装两扇小翅膀。", "RESPOND", "小翅膀")
        type_answer("这样就可以飞到月亮背面啦。", "RESPOND", "月亮")
        assert "小翅膀" in wires[-1] and "我想给叶子装两扇小翅膀。" not in wires[-1]
        type_answer("你先别问，让我看看它怎么转。", "LISTEN", "好，我陪你看")
        type_answer("我们先休息一下。", "PAUSE", "先歇一会儿")
        type_answer("好了，我想聊聊小熊的帽子。", "RESPOND", REPLY)
        result["desktop"].append("multi_turn_listen_pause_resume")

        detail = client.get(origin + "/api/v1" + route).json()["data"]
        rev = detail["session"]["revision"]
        headers = {"X-CSRF-Token": client.cookies["zhiya_csrf"]}
        transcription_fields = {"readingSessionId": str(sid), "pageNumber": "1", "revision": str(rev)}
        before = len(wires)
        for mode, status in (("error", 503), ("empty", 422), ("ok", 200)):
            voice.asr_mode = mode
            reply = client.post("/api/v1/voice/transcriptions", data=transcription_fields, headers=headers,
                                files={"file": ("speech.wav", voice.audio, "audio/wav")})
            assert reply.status_code == status, (mode, reply.status_code)
        assert len(wires) == before and reply.json()["data"]["text"] == TRANSCRIPT
        type_answer(reply.json()["data"]["text"], "RESPOND", REPLY)
        assert wires[-1].endswith(TRANSCRIPT)
        result["voice"].append("asr_error_empty_and_confirmed_text_into_agent")

        asked = post(client, route + "/questions", {
            "inputText": "小熊的帽子是什么颜色？", "pageNumber": 1, "expectedRevision":
            client.get("/api/v1" + route).json()["data"]["session"]["revision"], "clientEventId": "local-voice-ask",
        })
        assert asked["outputText"] == REPLY
        voice.tts_fail = True
        body = {"interactionEventId": asked["interactionEventId"]}
        synth = client.post("/api/v1/voice/syntheses", json=body, headers=headers)
        assert synth.status_code == 503 and voice.speech[-1] == REPLY, synth.status_code
        saved = client.get("/api/v1" + route).json()["data"]["events"]
        assert any(e.get("outputText") == REPLY for e in saved)
        voice.tts_fail = False
        audio = post(client, "/voice/syntheses", body)
        assert client.get(audio["audioUrl"]).content == voice.audio
        result["voice"].append("ask_reply_tts_text_success_and_failure_recovery")

        page.reload()
        expect(page.get_by_role("button", name="下一页", exact=True)).to_be_enabled()
        with page.expect_response(is_write(route + "/events", "PAGE_CHANGED")) as advanced:
            page.get_by_role("button", name="下一页", exact=True).click()
        assert advanced.value.status == 201
        assert client.get(audio["audioUrl"]).status_code in (404, 409)
        next_story = "小熊把纸风车插进土里。"
        expect(page.get_by_text(next_story, exact=False).first).to_be_visible()
        page.get_by_role("button", name="上一页", exact=True).click()
        expect(page.get_by_text("松鼠把信轻轻放进信箱。", exact=False).first).to_be_visible()
        page.reload()
        expect(page.get_by_text(next_story, exact=False).first).to_be_visible()
        result["desktop"].append("page_forward_review_refresh")

        with page.expect_response(is_write(route + "/completions")) as completed:
            page.get_by_role("button", name="结束本次阅读").click()
        assert completed.value.status == 200
        expect(page.get_by_role("heading", name="本次共读小结")).to_be_visible()
        result["desktop"].append("finish_and_summary")

        mobile_sid = post(client, "/reading-sessions",
                          {"childProfileId": profile, "storyVersionId": VERSION, "mode": "PARENT_CHILD"})["readingSessionId"]
        mobile_route = f"/reading-sessions/{mobile_sid}"
        phone = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844},
                                    is_mobile=True, locale="zh-CN", storage_state=desktop.storage_state())
        handset = phone.new_page()
        handset.set_default_timeout(9000)
        with handset.expect_response(is_write(mobile_route + "/events", "STARTED")) as mobile_start:
            handset.goto(f"{origin}/mobile.html#reader/{mobile_sid}")
        assert mobile_start.value.status == 201

        def answer_mobile(text, action):
            handset.get_by_role("button", name="孩子回答", exact=True).click()
            dialog = handset.get_by_role("dialog")
            dialog.get_by_role("button", name="改用文字输入").click()
            dialog.locator("textarea").fill(text)
            with handset.expect_response(is_write(mobile_route + "/events", "ANSWER")) as response:
                dialog.get_by_role("button", name="确认回答").click()
            require_receipt(response.value, action)

        answer_mobile("你先别问，让我看看它怎么转。", "LISTEN")
        expect(handset.get_by_text("好，我陪你看。", exact=False).first).to_be_visible()
        answer_mobile("我们先休息一下。", "PAUSE")
        answer_mobile("好了，我想聊聊小熊的帽子。", "RESPOND")
        handset.reload()
        expect(handset.get_by_text(REPLY, exact=False).first).to_be_visible()
        result["mobile"].append("reader_interaction_listen_pause_resume_refresh")
        next_button = handset.get_by_role("button", name=re.compile("下一页"))
        phone.set_offline(True)
        next_button.click()
        handset.wait_for_timeout(400)
        phone.set_offline(False)
        retry = handset.get_by_role("button", name="重新核对进度")
        if retry.is_visible():
            retry.click()
        else:
            next_button.click()
        expect(handset.get_by_text(next_story, exact=False).first).to_be_visible()
        result["mobile"].append("offline_page_write_recovery")
        result["browser"] = {"engine": "Chromium", "version": browser.version, "mobile": "viewport emulation"}
        browser.close()
    return result


def launch(certificate_tool):
    with tempfile.TemporaryDirectory(prefix="gate1-synthetic-") as temp:
        path = Path(temp)
        db_path = path / "staging.sqlite3"
        os.environ.update(ZHIYA_DB_PATH=str(db_path), YAYA_AGENT2_STAGE="staging", YAYA_AGENT2_MODE="off")
        from app.config import Settings
        from app.db import yaya_agent as ledger
        from app.main import create_app
        from app.model_gateway import ModelGateway
        from app.services.yaya_agent import YayaAgent
        from app.services.yaya_agent_config import MODEL, YayaSettings
        from app.speech_config import SpeechSettings

        with closing(sqlite3.connect(db_path)) as db:
            db.executescript((ROOT / "database" / "schema.sql").read_text(encoding="utf-8"))
        seed = runpy.run_path(str(ROOT / "infra" / "staging" / "seed.py"))["seed"]
        seed.__globals__["DB"] = db_path
        seed()
        ledger.install()
        certificate, key = path / "localhost.pem", path / "localhost.key"
        subprocess.run([certificate_tool, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
                        "-keyout", str(key), "-out", str(certificate)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        app = create_app(Settings(), SpeechSettings(api_key="synthetic-voice-placeholder",
                         tts_api_url="https://mock.invalid/speech"), yaya_settings=YayaSettings())
        app.mount("/", StaticFiles(directory=ROOT / "frontend" / "dist", html=True))
        logging.getLogger("zhiya").setLevel(logging.WARNING)
        wires = []
        voice = VoiceFixture()

        def provider(request):
            content = json.loads(request.content)["messages"][-1]["content"]
            wires.append(content)
            message = content.split("用户输入：\n")[-1]
            result = ("叶子添上小翅膀就像一架小小飞船。" if "两扇小翅膀" in message else
                      "它可以带着你飞到月亮背面。" if "月亮背面" in message else REPLY)
            return httpx.Response(200, json={"model": MODEL, "choices": [{"finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps({"reply": result, "next_action": "RESPOND"})}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 10}})

        model = Settings(model_api_url="https://ws-local-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
                         model_api_key="synthetic-local-model-placeholder", model_name=MODEL)
        gateway = ModelGateway(model, transport=httpx.MockTransport(provider))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            origin = f"https://127.0.0.1:{listener.getsockname()[1]}"
            server = uvicorn.Server(uvicorn.Config(app, ssl_keyfile=str(key), ssl_certfile=str(certificate),
                                                   access_log=False, log_level="warning"))
            worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            worker.start()
            try:
                until = time.monotonic() + 10
                while not server.started and worker.is_alive() and time.monotonic() < until:
                    time.sleep(.03)
                assert server.started
                with httpx.Client(base_url=origin, verify=str(certificate), trust_env=False) as client:
                    post(client, "/auth/registrations", ACCOUNT)
                    guardian = post(client, "/auth/sessions", ACCOUNT)["accountId"]
                    profile = post(client, "/child-profiles", {"nickname": "合成小芽", "ageBand": "AGE_3_4",
                                                                "preferredMinutes": 10})["childProfileId"]
                    settings = YayaSettings(mode="allowlist", guardians=frozenset({guardian}), model=model,
                        model_input_profiles=frozenset({(guardian, profile)}), ledger_secret="isolated-" * 5)
                    app.state.yaya_agent = YayaAgent(settings, gateway)
                    app.state.voice_gateway = voice
                    result = browser_checks(origin, client, profile, voice, wires)
            finally:
                server.should_exit = True
                worker.join(timeout=10)
                asyncio.run(gateway.__aexit__(None, None, None))
        result["scope"] = "local_https_browser_synthetic_model_and_voice_transport"
        result["real_provider_calls"] = 0
        result["synthetic_model_calls"] = len(wires)
        result["source_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openssl", default="openssl")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    measured = launch(args.openssl)
    args.output.write_text(json.dumps(measured, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(measured, ensure_ascii=False))
