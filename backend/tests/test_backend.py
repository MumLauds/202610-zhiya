import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ServiceError
from app.main import create_app
from app.model_gateway import MAX_RESPONSE_BYTES, ModelGateway


API_KEY = "fake-secret-api-key"
INPUT_TEXT = "private-child-input"
SYSTEM_PROMPT = "private-system-prompt"
UPSTREAM_BODY = "private-upstream-body"
MODEL_SETTINGS = Settings(
    model_api_url="https://model.example.test/v1/chat/completions",
    model_api_key=API_KEY,
    model_name="test-model",
)


def model_response(content="  示例回答  ", *, finish_reason="stop", **message_fields):
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content, **message_fields},
            },
        ],
    }


class ApiTests(unittest.TestCase):
    def test_unconfigured_health_matches_api_032(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "zhiya.sqlite3"
            schema = Path(__file__).resolve().parents[2] / "database" / "schema.sql"
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript(schema.read_text(encoding="utf-8"))
            with patch.dict("os.environ", {"ZHIYA_DB_PATH": str(database)}):
                with TestClient(create_app(Settings())) as client:
                    first = client.get("/api/v1/health")
                    second = client.get("/api/v1/health")

        self.assertEqual(first.status_code, 200)
        request_id = first.headers["X-Request-ID"]
        self.assertTrue(request_id)
        self.assertEqual(first.json(), {"data": {"status": "ok"}, "requestId": request_id})
        self.assertNotEqual(second.json()["requestId"], request_id)

    def test_errors_use_contract_envelope_without_sensitive_details(self):
        app = create_app(MODEL_SETTINGS)

        @app.get("/test-validation")
        async def validate(value: int):
            return value

        @app.get("/test-exception")
        async def fail():
            raise RuntimeError(UPSTREAM_BODY + API_KEY)

        with TestClient(app) as client, self.assertLogs("zhiya.http", "INFO") as logs:
            cases = [
                (client.get, "/missing", 404, "NOT_FOUND"),
                (client.post, "/api/v1/health", 405, "METHOD_NOT_ALLOWED"),
                (client.get, f"/test-validation?value={INPUT_TEXT}", 422, "VALIDATION_ERROR"),
                (client.get, "/test-exception", 500, "INTERNAL_ERROR"),
            ]
            for request, path, status, code in cases:
                with self.subTest(code=code):
                    response = request(path)
                    self.assertEqual(response.status_code, status)
                    body = response.json()
                    self.assertEqual(set(body), {"error", "requestId"})
                    self.assertEqual(set(body["error"]), {"code", "message", "retryable"})
                    self.assertEqual(body["error"]["code"], code)
                    self.assertTrue(body["error"]["message"])
                    self.assertIs(body["error"]["retryable"], False)
                    self.assertEqual(body["requestId"], response.headers["X-Request-ID"])
                    for sensitive in (API_KEY, INPUT_TEXT, UPSTREAM_BODY):
                        self.assertNotIn(sensitive, response.text)
        for sensitive in (API_KEY, INPUT_TEXT, UPSTREAM_BODY):
            self.assertNotIn(sensitive, "\n".join(logs.output))


class SettingsTests(unittest.TestCase):
    def test_validates_provider_url_and_timeout(self):
        self.assertTrue(MODEL_SETTINGS.model_configured)
        self.assertEqual(MODEL_SETTINGS.model_timeout_seconds, 20)
        self.assertNotIn(API_KEY, repr(MODEL_SETTINGS))

        for url in (
            "http://example.test/v1/chat/completions",
            "https:///v1/chat/completions",
            "https://user:password@example.test/v1/chat/completions",
            "https://example.test/v1/chat/completions?key=secret",
            "https://example.test/v1/chat/completions#fragment",
            "https://example.test/v1/chat/completions?",
            "https://example.test/v1/chat/completions#",
            "https://example.test:99999/v1/chat/completions",
            "https://🧪.example/test",
            "https://example.test/path\x7f",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "MODEL_API_URL"):
                replace(MODEL_SETTINGS, model_api_url=url)

        for timeout in (0, -1, 20.01, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "MODEL_TIMEOUT_SECONDS"):
                replace(MODEL_SETTINGS, model_timeout_seconds=timeout)

        with self.assertRaisesRegex(ValueError, "MODEL_NAME"):
            replace(MODEL_SETTINGS, model_name="invalid-\ud800")


class ModelGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def assert_service_error(self, gateway, code, status, *, input_text=INPUT_TEXT):
        with self.assertRaises(ServiceError) as caught:
            await gateway.generate(input_text=input_text, system_prompt=SYSTEM_PROMPT)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.status_code, status)
        self.assertIs(caught.exception.retryable, False)
        for sensitive in (API_KEY, INPUT_TEXT, SYSTEM_PROMPT, UPSTREAM_BODY):
            self.assertNotIn(sensitive, str(caught.exception))

    async def test_invalid_or_unconfigured_calls_do_not_reach_provider(self):
        def fail_if_called(_request):
            self.fail("Invalid or unconfigured input reached the provider")

        transport = httpx.MockTransport(fail_if_called)
        async with ModelGateway(Settings(), transport=transport) as gateway:
            await self.assert_service_error(gateway, "MODEL_NOT_CONFIGURED", 503)
        async with ModelGateway(MODEL_SETTINGS, transport=transport) as gateway:
            for input_text in ("", " \t", "字" * 201, "invalid-\ud800"):
                with self.subTest(length=len(input_text)):
                    await self.assert_service_error(gateway, "INVALID_INPUT", 422, input_text=input_text)

            with self.assertRaises(ServiceError) as caught:
                await gateway.generate(input_text=INPUT_TEXT, system_prompt="invalid-\ud800")
            self.assertEqual(caught.exception.code, "INVALID_INPUT")
            self.assertEqual(caught.exception.status_code, 422)

    async def test_sends_one_chat_completion_and_logs_metadata_only(self):
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(200, json=model_response(UPSTREAM_BODY))

        with self.assertLogs("zhiya.model", "INFO") as logs:
            async with ModelGateway(MODEL_SETTINGS, transport=httpx.MockTransport(provider)) as gateway:
                output = await gateway.generate(input_text=f"  {INPUT_TEXT} ", system_prompt=SYSTEM_PROMPT)

        self.assertEqual(output, UPSTREAM_BODY)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), MODEL_SETTINGS.model_api_url)
        self.assertEqual(request.headers["Authorization"], f"Bearer {API_KEY}")
        self.assertEqual(request.headers["Accept-Encoding"], "identity")
        self.assertEqual(json.loads(request.content), {
            "model": MODEL_SETTINGS.model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": INPUT_TEXT},
            ],
            "max_tokens": MODEL_SETTINGS.model_max_tokens,
            "stream": False,
            "thinking": {"type": "disabled"},
        })
        self.assertIn("outcome=OK", logs.output[0])
        for sensitive in (API_KEY, INPUT_TEXT, SYSTEM_PROMPT, UPSTREAM_BODY):
            self.assertNotIn(sensitive, "\n".join(logs.output))

    async def test_total_deadline_cancels_provider_without_retry(self):
        calls = []
        stream_started = asyncio.Event()
        stream_closed = asyncio.Event()

        class SlowStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                stream_started.set()
                yield b'{"choices":'
                await asyncio.sleep(10)

            async def aclose(self):
                stream_closed.set()

        def provider(request):
            calls.append(request)
            return httpx.Response(200, stream=SlowStream())

        class TrackedTransport(httpx.MockTransport):
            closed = False

            async def aclose(self):
                self.closed = True
                await super().aclose()

        settings = replace(MODEL_SETTINGS, model_timeout_seconds=0.05)
        transport = TrackedTransport(provider)
        async with ModelGateway(settings, transport=transport) as gateway:
            async with asyncio.timeout(1):
                await self.assert_service_error(gateway, "MODEL_TIMEOUT", 504)
            self.assertTrue(stream_started.is_set())
            self.assertTrue(stream_closed.is_set())
            self.assertFalse(transport.closed)
        self.assertTrue(transport.closed)
        self.assertEqual(len(calls), 1)

    async def test_provider_failures_and_redirects_are_safe_single_attempts(self):
        cases = {
            302: "MODEL_PROVIDER_ERROR",
            401: "MODEL_AUTH_FAILED",
            429: "MODEL_RATE_LIMITED",
            502: "MODEL_UNAVAILABLE",
            None: "MODEL_UNAVAILABLE",
        }
        for status, code in cases.items():
            calls = []

            def provider(request):
                calls.append(request)
                if status is None:
                    raise httpx.ConnectError(UPSTREAM_BODY, request=request)
                return httpx.Response(
                    status,
                    headers={"Location": "https://other.example.test/"},
                    text=UPSTREAM_BODY,
                )

            with self.subTest(status=status), self.assertLogs("zhiya.model", "INFO") as logs:
                async with ModelGateway(MODEL_SETTINGS, transport=httpx.MockTransport(provider)) as gateway:
                    await self.assert_service_error(gateway, code, 503)
                self.assertEqual(len(calls), 1)
                self.assertIn(f"outcome={code}", logs.output[0])
                for sensitive in (API_KEY, INPUT_TEXT, SYSTEM_PROMPT, UPSTREAM_BODY):
                    self.assertNotIn(sensitive, "\n".join(logs.output))

    async def test_incomplete_or_invalid_model_responses_are_rejected(self):
        cases = {
            "malformed JSON": UPSTREAM_BODY.encode(),
            "excessive JSON nesting": b"[" * 10_000 + b"]" * 10_000,
            "response over the byte limit": json.dumps(model_response()).encode().ljust(MAX_RESPONSE_BYTES + 1),
            "missing choices": b"{}",
            "empty text": json.dumps(model_response(" \t")).encode(),
            "invalid Unicode text": json.dumps(model_response("invalid-\ud800")).encode(),
            "truncated": json.dumps(model_response(finish_reason="length")).encode(),
            "refusal": json.dumps(model_response(refusal="refused")).encode(),
            "tool call": json.dumps(model_response(tool_calls=[{"id": "tool-1"}])).encode(),
        }
        for case, body in cases.items():
            def provider(_request):
                return httpx.Response(200, content=body)

            with self.subTest(case=case):
                async with ModelGateway(MODEL_SETTINGS, transport=httpx.MockTransport(provider)) as gateway:
                    await self.assert_service_error(gateway, "MODEL_INVALID_RESPONSE", 503)

    async def test_compressed_response_is_rejected_before_reading(self):
        # Even a malformed compressed body must be rejected before HTTPX decodes it.
        class CompressedStream(httpx.AsyncByteStream):
            read = False
            closed = False

            async def __aiter__(self):
                self.read = True
                yield b"not-a-valid-compressed-body"

            async def aclose(self):
                self.closed = True

        for encoding in ("gzip", "deflate", "br", "identity, gzip"):
            stream = CompressedStream()
            calls = []

            def provider(request):
                calls.append(request)
                return httpx.Response(200, headers={"Content-Encoding": encoding}, stream=stream)

            with self.subTest(encoding=encoding):
                async with ModelGateway(MODEL_SETTINGS, transport=httpx.MockTransport(provider)) as gateway:
                    await self.assert_service_error(gateway, "MODEL_INVALID_RESPONSE", 503)
                self.assertFalse(stream.read)
                self.assertTrue(stream.closed)
                self.assertEqual(len(calls), 1)
