"""Isolated, allowlisted synthetic-only transport to Alibaba Cloud Model Studio.

No production imports, prompt discovery, .env loading, local credential discovery,
automatic retries, redirects, proxies, or fallback endpoint. The authorised runner
alone builds the allowlist from its locked synthetic fixtures and actual synthetic
history. This transport checks the precise bytes represented by approved messages,
not whether arbitrary strings are synthetic. Never expose this as an API route.
"""
from dataclasses import dataclass, field
from hashlib import sha256
import json
import logging
import math
import os
import re
from time import monotonic_ns

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

PRIMARY_MODEL = "qwen3.8-flash"
SECONDARY_MODEL = "qwen3.7-flash"
MODELS = frozenset((PRIMARY_MODEL, SECONDARY_MODEL))
_BASE = re.compile(
    r"https://(?:llm|ws)-[a-z0-9](?:[a-z0-9-]{0,57}[a-z0-9])?\.(?P<region>cn-beijing|cn-hongkong)\.maas\.aliyuncs\.com/compatible-mode/v1",
    flags=re.ASCII,
)
_ID = re.compile(r"[A-Za-z0-9._:/-]{1,200}", flags=re.ASCII)
_MAX_BODY = 1 << 20


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    base_url: str
    timeout_s: float = 60.0
    primary_model: str = PRIMARY_MODEL
    secondary_model: str = SECONDARY_MODEL

    def __post_init__(self):
        if not isinstance(self.base_url, str) or not _BASE.fullmatch(self.base_url):
            raise ValueError("Use the exact HTTPS Beijing or Hong Kong Model Studio workspace API base URL")
        if not isinstance(self.api_key, str) or not self.api_key or not re.fullmatch(r"[!-~]+", self.api_key, re.ASCII):
            raise ValueError("Missing or invalid isolated DASHSCOPE_API_KEY")
        if type(self.timeout_s) not in (int, float) or not math.isfinite(self.timeout_s) or not 0 < self.timeout_s <= 300:
            raise ValueError("YAYA_QWEN_TIMEOUT must be greater than 0 and at most 300 seconds")
        if (self.primary_model, self.secondary_model) != (PRIMARY_MODEL, SECONDARY_MODEL):
            raise ValueError("Model pair must match the approved experiment")

    @property
    def region(self):
        return _BASE.fullmatch(self.base_url)["region"]

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        try:
            timeout = float(env.get("YAYA_QWEN_TIMEOUT", "60"))
        except (TypeError, ValueError):
            raise ValueError("Invalid YAYA_QWEN_TIMEOUT") from None
        if env.get("YAYA_QWEN_ENABLE_THINKING", "false").lower() != "false":
            raise ValueError("This experiment pins YAYA_QWEN_ENABLE_THINKING=false")
        return cls(api_key=env.get("DASHSCOPE_API_KEY"), base_url=env.get("DASHSCOPE_BASE_URL"),
                   timeout_s=timeout, primary_model=env.get("YAYA_QWEN_PRIMARY_MODEL",PRIMARY_MODEL),
                   secondary_model=env.get("YAYA_QWEN_SECONDARY_MODEL",SECONDARY_MODEL))


def _messages(messages):
    if not isinstance(messages, (tuple, list)) or len(messages) != 2:
        raise ValueError("Expected a synthetic system/user message pair")
    out = []
    total = 0
    for role, message in zip(("system", "user"), messages):
        if type(message) is not dict or set(message) != {"role", "content"} or message["role"] != role:
            raise ValueError("Only ordered synthetic system/user text messages are accepted")
        value = message["content"]
        if type(value) is not str or not value.strip():
            raise ValueError("Synthetic message content must be nonempty text")
        try:
            total += len(value.encode("utf-8"))
        except UnicodeError:
            raise ValueError("Invalid UTF-8 in synthetic request") from None
        out.append({"role": role, "content": value})
    if total > 49152:
        raise ValueError("Synthetic request exceeds the protocol size cap")
    return out


def message_digest(messages):
    return sha256(json.dumps(_messages(messages), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _ms(begin, end=None):
    return round(((monotonic_ns() if end is None else end) - begin) / 1e6, 3)


def _strict_json(body):
    def unique(entries):
        obj = dict(entries)
        if len(obj) != len(entries):
            raise ValueError("duplicate member")
        return obj

    def invalid(_):
        raise ValueError("nonstandard JSON constant")

    return json.loads(body, object_pairs_hook=unique, parse_constant=invalid)


def _contains_text(obj, needle):
    if isinstance(obj, str):
        return needle in obj
    if isinstance(obj, dict):
        return any(_contains_text(k, needle) or _contains_text(v, needle) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_contains_text(v, needle) for v in obj)
    return False


class BailianProvider:
    """OpenAI official Python SDK on the documented Alibaba-compatible route."""

    def __init__(self, settings, *, allow_message=None, _transport=None):
        self.settings = settings
        self.allow_message = allow_message
        target = httpx.URL(settings.base_url + "/chat/completions")

        def validate_route(request):
            if request.method != "POST" or request.url != target:
                raise ValueError("Unexpected Hong Kong inference request route")
            for name in ("openai-organization", "openai-project"):
                if name in request.headers:
                    del request.headers[name]

        self._http = httpx.Client(
            trust_env=False, follow_redirects=False, verify=True,
            timeout=settings.timeout_s, transport=_transport,
            event_hooks={"request": [validate_route]},
        )
        self._client = OpenAI(
            api_key=settings.api_key, base_url=settings.base_url,
            # Empty overrides OPENAI_ORG_ID, OPENAI_PROJECT_ID and OPENAI_WEBHOOK_SECRET.
            organization="", project="", webhook_secret="",
            http_client=self._http, timeout=settings.timeout_s, max_retries=0,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _safe_id(self, value):
        if isinstance(value, str) and _ID.fullmatch(value) and self.settings.api_key not in value:
            return value
        return None

    def complete(self, messages, model, *, timeout_s=None, max_tokens=512):
        if type(model) is not str or model not in MODELS:
            raise ValueError("Requested model is not part of the approved experiment")
        messages = _messages(messages)
        if self.allow_message is None or self.allow_message(message_digest(messages)) is not True:
            raise ValueError("Request was not authorised by the pinned synthetic runner")
        if any(self.settings.api_key in prompt["content"] for prompt in messages):
            raise ValueError("Credential text cannot be relayed as a prompt")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 1024:
            raise ValueError("Invalid synthetic output token budget")
        if timeout_s is not None and (type(timeout_s) not in (int, float)
                or not math.isfinite(timeout_s) or timeout_s <= 0):
            raise ValueError("Invalid request timeout")
        if any(logging.getLogger(logger).isEnabledFor(logging.DEBUG)
               for logger in ("openai", "openai._base_client", "httpx", "httpcore", "httpcore.http11")):
            raise ValueError("Disable HTTP/SDK debug logging before a synthetic run")
        timeout = self.settings.timeout_s if timeout_s is None else min(timeout_s, self.settings.timeout_s)
        start = monotonic_ns()
        body_params = {
            "model":model,"messages":messages,"max_completion_tokens":max_tokens,
            "temperature":0.6,"response_format":{"type":"json_object"},
            "stream":False,"enable_thinking":False,"preserve_thinking":False,
        }
        result = dict(provider="bailian", request_body=body_params, status=None, error_type=None, text=None, raw_body=None, usage=None,
                      model_requested=model, model_returned=None, request_id=None,
                      response_id=None, http_status=None, status_code=None, stop_type=None,
                      headers_ms=None, body_ms=None, parse_ms=None, call_ms=None, wall_ms=None)

        def finish(status, error_type=None):
            result.update(status=status, error_type=error_type, call_ms=_ms(start))
            result["wall_ms"] = result["call_ms"]
            return result

        try:
            with self._client.chat.completions.with_streaming_response.create(
                model=model, messages=messages, max_completion_tokens=max_tokens, temperature=0.6,
                response_format={"type":"json_object"}, stream=False, timeout=timeout,
                extra_body={"enable_thinking":False,"preserve_thinking":False},
            ) as response:
                headers_at = monotonic_ns()
                result.update(http_status=response.status_code, status_code=response.status_code,
                              headers_ms=_ms(start, headers_at),
                              request_id=self._safe_id(response.headers.get("x-request-id")
                                                       or response.headers.get("x-dashscope-request-id")))
                body = bytearray()
                for chunk in response.iter_bytes():
                    if _ms(start) >= timeout * 1000:
                        return finish("TIMEOUT", "BODY_DEADLINE")
                    if len(body) + len(chunk) > _MAX_BODY:
                        return finish("PARSE_ERROR", "RESPONSE_TOO_LARGE")
                    body.extend(chunk)
                result["body_ms"] = _ms(headers_at)
            if _ms(start) >= timeout * 1000:
                return finish("TIMEOUT", "RESPONSE_DEADLINE")
        except (APITimeoutError, httpx.TimeoutException):
            return finish("TIMEOUT", "NETWORK_TIMEOUT")
        except APIStatusError as exc:
            result.update(http_status=exc.status_code, status_code=exc.status_code,
                          request_id=self._safe_id(exc.response.headers.get("x-request-id")
                                                   or exc.response.headers.get("x-dashscope-request-id")))
            # Never preserve or echo error bodies, SDK exceptions, URLs, or auth headers.
            return finish("API_ERROR", "HTTP_STATUS")
        except (APIConnectionError, httpx.HTTPError):
            return finish("API_ERROR", "CONNECTION")
        except Exception:
            return finish("API_ERROR", "SDK_FAILURE")

        parse_at = monotonic_ns()
        try:
            raw = body.decode("utf-8")
            if self.settings.api_key in raw:
                result["parse_ms"] = _ms(parse_at)
                return finish("PARSE_ERROR", "CREDENTIAL_ECHO_BLOCKED")
            result["raw_body_sha256"] = sha256(bytes(body)).hexdigest()
            obj = _strict_json(raw)
            # Escaped JSON can conceal a credential substring in the wire bytes.
            if _contains_text(obj, self.settings.api_key):
                result["parse_ms"] = _ms(parse_at)
                return finish("PARSE_ERROR", "CREDENTIAL_ECHO_BLOCKED")
            # The OpenAI envelope contains a second JSON document as text.
            # Check it before retaining the envelope or exposing a parsed draft.
            choices = obj.get("choices") if type(obj) is dict else None
            if type(choices) is list:
                for choice in choices:
                    message = choice.get("message") if type(choice) is dict else None
                    content = message.get("content") if type(message) is dict else None
                    if not isinstance(content, str): continue
                    try: inner = _strict_json(content)
                    except (ValueError, RecursionError): continue
                    if _contains_text(inner, self.settings.api_key):
                        result["parse_ms"] = _ms(parse_at)
                        return finish("PARSE_ERROR", "CREDENTIAL_ECHO_BLOCKED")
            result["raw_body"] = raw
            if type(choices) is not list or len(choices) != 1:
                raise ValueError("expected exactly one completion choice")
            choice = choices[0]
            content = choice["message"]["content"]
            if type(content) is not str or not content.strip():
                raise ValueError("no usable completion text")
            usage = obj.get("usage")
            if usage is not None and type(usage) is not dict:
                raise ValueError("invalid completion usage")
            result.update(text=content, usage=usage, request_id=result["request_id"] or self._safe_id(obj.get("request_id")),
                          response_id=self._safe_id(obj.get("id")), model_returned=self._safe_id(obj.get("model")),
                          stop_type=self._safe_id(choice.get("finish_reason")))
        except (UnicodeError, TypeError, ValueError, KeyError, RecursionError):
            result["parse_ms"] = _ms(parse_at)
            return finish("PARSE_ERROR", "INVALID_ENVELOPE")
        result["parse_ms"] = _ms(parse_at)
        return finish("RETURNED")
