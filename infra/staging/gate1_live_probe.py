"""Opt-in Gate 1 capture using the real Beijing gateway with a fresh synthetic database."""

import argparse
import json
import os
import runpy
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
VERSION = 901_000
PLAN = (
    ("A", 1, "我觉得这片叶子是一床小被子。"),
    ("B1", 1, "我想给叶子装两扇小翅膀。"),
    ("B2", 1, "这样就可以飞到月亮背面啦。"),
    ("B3", 1, "还可以给云朵挠痒痒。"),
    ("C", 1, "那个小被子是我说的还是书里的女孩说的？"),
    ("D", 1, "拿起黄色叶子的小兔和戴蓝帽子的小熊是一个人吗？"),
    ("E", 1, "松鼠是怕信掉了才轻轻放进去的吗？"),
    ("extra-attribute", 1, "书里小熊戴的帽子是什么颜色？"),
    ("extra-object", 1, "松鼠把什么放进了信箱？"),
    ("F-listen", 2, "你先别问，让我看看它怎么转。"),
    ("F-hold", 2, "还在看，不用等我说答案。"),
    ("F-resume", 2, "好了，我想聊聊它转圈的样子。"),
    ("extra-action", 2, "小熊把纸风车放到哪儿了？"),
    ("extra-state", 2, "这页写的是纸风车还没动，还是已经转起来了？"),
)


def digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


class CapturedStream(httpx.AsyncByteStream):
    def __init__(self, upstream, row, limit):
        self.upstream, self.row, self.limit = upstream, row, limit

    async def __aiter__(self):
        body = bytearray()
        try:
            async for chunk in self.upstream:
                body.extend(chunk[:max(0, self.limit - len(body))])
                yield chunk
        finally:
            self.row["raw_response"] = body.decode("utf-8", errors="replace")
            self.row["api_ms"] = round(1000 * (time.perf_counter() - self.row.pop("_start")), 2)

    async def aclose(self):
        await self.upstream.aclose()


class Capture(httpx.AsyncHTTPTransport):
    def __init__(self, endpoint, limit):
        super().__init__(retries=0, trust_env=False)
        self.endpoint, self.limit, self.calls = endpoint, limit, []

    async def handle_async_request(self, request):
        if request.method != "POST" or str(request.url) != self.endpoint:
            raise RuntimeError("Unexpected Staging model route")
        message = json.loads(request.content)
        content = message["messages"][-1]["content"]
        context = content.partition("\n")[2].partition("\n\n用户输入：\n")[0]
        started = time.perf_counter()
        row = {"messages": message["messages"], "model_requested": message["model"],
               "prompt_sha256": digest(message["messages"][0]["content"]), "context_sha256": digest(context)}
        self.calls.append(row)
        try:
            response = await super().handle_async_request(request)
        except BaseException as exc:
            row.update(error=type(exc).__name__, api_ms=round(1000 * (time.perf_counter() - started), 2))
            raise
        row.update(http_status=response.status_code, _start=started,
                   provider_request_id=response.headers.get("x-request-id") or response.headers.get("x-dashscope-request-id"))
        if response.status_code in {401, 403}:
            # The gateway returns on HTTP errors without reading their bodies.
            # Keep a bounded provider diagnostic for this opted-in private capture.
            body = bytearray()
            try:
                async for chunk in response.stream:
                    body.extend(chunk[:4096 - len(body)])
                    if len(body) >= 4096:
                        break
            except httpx.TransportError:
                pass
            finally:
                await response.aclose()
            row["raw_response"] = body.decode("utf-8", errors="replace")
            return httpx.Response(response.status_code, headers=response.headers)
        response.stream = CapturedStream(response.stream, row, self.limit)
        return response


def capture(output, model_key):
    from app.config import Settings
    from app.db import yaya_agent as ledger
    from app.main import create_app
    from app.model_gateway import MAX_RESPONSE_BYTES, ModelGateway
    from app.services.yaya_agent import YayaAgent
    from app.services.yaya_agent_config import YayaSettings
    from app.speech_config import SpeechSettings

    with tempfile.TemporaryDirectory(prefix="gate1-live-synthetic-") as temp:
        path = Path(temp) / "stage.sqlite3"
        os.environ.update(ZHIYA_DB_PATH=str(path), YAYA_AGENT2_MODE="off", YAYA_AGENT2_STAGE="staging")
        with closing(sqlite3.connect(path)) as db:
            db.executescript((ROOT / "database" / "schema.sql").read_text(encoding="utf-8"))
        seed = runpy.run_path(str(ROOT / "infra" / "staging" / "seed.py"))["seed"]
        seed.__globals__["DB"] = path
        seed()
        ledger.install()
        app = create_app(Settings(), SpeechSettings(), yaya_settings=YayaSettings())
        revision = subprocess.check_output(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
        with TestClient(app, base_url="https://testserver") as client, output.open("x", encoding="utf-8") as out:
            def write(data):
                blob = json.dumps(data, ensure_ascii=False)
                if model_key in blob:
                    raise RuntimeError("Credential appeared in capture. This row was not written.")
                out.write(blob + "\n")
                out.flush()

            def post(route, data):
                response = client.post("/api/v1" + route, json=data,
                                       headers={"X-CSRF-Token": client.cookies.get("zhiya_csrf", "")})
                if response.status_code != 201:
                    raise RuntimeError(f"{route} failed with HTTP {response.status_code}")
                return response.json()

            account = {"account": "gate1-synthetic-live", "password": "synthetic-gate1-login"}
            post("/auth/registrations", account)
            guardian = post("/auth/sessions", account)["data"]["accountId"]
            profile = post("/child-profiles", {"ageBand": "AGE_3_4", "preferredMinutes": 10})["data"]["childProfileId"]
            ledger.grant_input_consent(guardian, profile, int(time.time() * 1000) + 3_600_000)
            os.environ.update(YAYA_AGENT2_MODE="allowlist", YAYA_AGENT2_ALLOWLIST=str(guardian),
                              YAYA_AGENT2_INPUT_ALLOWLIST=f"{guardian}:{profile}")
            settings = YayaSettings.from_env()
            transport = Capture(settings.model.model_api_url, MAX_RESPONSE_BYTES)
            gateway = ModelGateway(settings.model, transport=transport)
            app.state.yaya_agent = YayaAgent(settings, gateway)
            write({"revision": revision, "scope": "real_beijing_provider_synthetic_fixture_local_asgi",
                   "workspace_api_host": urlsplit(settings.model.model_api_url).hostname})
            authorization_failed = False
            try:
                sid = post("/reading-sessions", {"childProfileId": profile, "storyVersionId": VERSION,
                                                   "mode": "PARENT_CHILD"})["data"]["readingSessionId"]
                route = f"/reading-sessions/{sid}/events"
                rev, current = 0, 1
                rev = post(route, {"eventType": "STARTED", "pageNumber": 1, "expectedRevision": rev,
                                   "clientEventId": "start"})["data"]["revision"]
                for case, page, text in PLAN:
                    if case == "extra-action":
                        # Keep the 12 genuine attempts within the sample Staging per-Session cap of 10.
                        sid = post("/reading-sessions", {"childProfileId": profile, "storyVersionId": VERSION,
                                                         "mode": "PARENT_CHILD"})["data"]["readingSessionId"]
                        route = f"/reading-sessions/{sid}/events"
                        rev = post(route, {"eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0,
                                           "clientEventId": "start"})["data"]["revision"]
                        current = 1
                    if page != current:
                        rev = post(route, {"eventType": "PAGE_CHANGED", "pageNumber": page, "expectedRevision": rev,
                                           "clientEventId": f"page-{page}"})["data"]["revision"]
                        current = page
                    before = len(transport.calls)
                    start = time.perf_counter()
                    response = post(route, {"eventType": "ANSWER", "inputText": text, "pageNumber": page,
                                            "expectedRevision": rev, "clientEventId": case})
                    data = response["data"]
                    rev = data["revision"]
                    with closing(sqlite3.connect(path)) as db:
                        amount = db.execute(
                            "SELECT ceiling_micros, settled_micros FROM yaya_agent_calls WHERE call_id=?",
                            (response["requestId"],),
                        ).fetchone()
                    calls = transport.calls[before:]
                    for row in calls:
                        if "_start" in row:
                            row["api_ms"] = round(1000 * (time.perf_counter() - row.pop("_start")), 2)
                        try:
                            envelope = json.loads(row.get("raw_response", ""))
                        except ValueError:
                            envelope = {}
                        if isinstance(envelope, dict):
                            row["model_returned"] = envelope.get("model")
                            row["usage"] = envelope.get("usage")
                            row["provider_request_id"] = row.get("provider_request_id") or envelope.get("request_id")
                    write({"case": case, "input": text, "request_id": response["requestId"],
                           "provider_attempts": calls, "backend_ms": round(1000 * (time.perf_counter() - start), 2),
                           "displayed_reply": data.get("replyText"), "receipt": data["agentReceipt"],
                           "fallback_reason": data["agentReceipt"].get("reason"), "reservation_and_settlement": amount})
                    if any(call.get("http_status") in {401, 403} for call in calls):
                        authorization_failed = True
                        break
                verified = [row for row in transport.calls if row.get("http_status") == 200 and row.get("provider_request_id")
                            and row.get("model_returned") == settings.model.model_name]
                write({"provider_attempt_count": len(transport.calls), "verified_provider_count": len(verified),
                       "stop_reason": "provider_authorization_denied" if authorization_failed else None,
                       "quality_review": "PENDING", "supplier_invoice": "PENDING"})
                if authorization_failed:
                    raise RuntimeError("Beijing returned HTTP 401/403. Check workspace and key model permissions before retrying.")
                if len(verified) < 12:
                    raise RuntimeError("Fewer than 12 verified provider responses. Check the private evidence.")
            finally:
                client.portal.call(gateway.__aexit__, None, None, None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-synthetic-staging", action="store_true")
    parser.add_argument("--evidence", type=Path, required=True, help="Private new JSONL file outside the checkout")
    args = parser.parse_args()
    target = args.evidence.resolve()
    key = os.getenv("YAYA_AGENT2_API_KEY", "")
    endpoint = os.getenv("YAYA_AGENT2_API_URL", "")
    if (not args.approved_synthetic_staging or not key or not endpoint or
            not os.getenv("YAYA_AGENT2_LEDGER_SECRET")):
        parser.exit(3, "BLOCKED: approved dedicated Staging credentials were not supplied.\n")
    host = urlsplit(endpoint).hostname or ""
    if (not host.endswith(".cn-beijing.maas.aliyuncs.com") or key.startswith("synthetic-")
            or key in (os.getenv("MODEL_API_KEY"), os.getenv("DASHSCOPE_API_KEY"))):
        parser.error("This probe requires a dedicated Beijing Staging workspace and key")
    if target.is_relative_to(ROOT) or target.exists():
        parser.error("Keep the raw evidence in a private, unused path outside this checkout")
    capture(target, key)
