"""DRAFT / 待团队评审 / 未实现. MOCK 样本一致性校验。

2026-11-27, main 48844b2ee267bc539ed0580a358470beca13e449.
DCL-025 / D-HW-03. H00 已合并，设备授权待团队批准。
本工具只读取测试资料，无 HTTP、数据库、固件或供应商调用。
"""

import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MAX_ID = 9007199254740991
PREFIX = "/api/v1/devices"
PAGE_TEXT_MAX = 768
MEDIA_KEY_MAX = 256
WRITE_JSON_MAX = 2048
RESPONSE_JSON_MAX = 32768
JSON_DEPTH_MAX = 12
JSON_TOKENS_MAX = 2048
CONTAINER_ITEMS_MAX = 128
URL_PATH_MAX = 383
MEDIA_SEGMENT = rf"[A-Za-z0-9_-]{{1,{MEDIA_KEY_MAX}}}"
PAID_OPERATIONS = {"ask": "questions", "asr": "asr", "tts": "tts"}
ROUTES = {
    "issue": ("POST", "/pairing-sessions", "WEB_GUARDIAN_CSRF", 201),
    "lookup": ("GET", "/pairing-sessions", "WEB_GUARDIAN_COOKIE", 200),
    "claim": ("POST", "/pairings/claim", "PAIRING_PROOF", 201),
    "assignment": ("GET", "/me/assignment", "DEVICE_BEARER", 200),
    "events": ("POST", "/me/events", "DEVICE_BEARER", 201),
    "questions": ("POST", "/me/questions", "DEVICE_BEARER", 201),
    "asr": ("POST", "/me/voice/transcriptions", "DEVICE_BEARER", 200),
    "tts": ("POST", "/me/voice/syntheses", "DEVICE_BEARER", 201),
    "media": ("GET", rf"/me/media/{MEDIA_SEGMENT}", "DEVICE_BEARER", 200),
    "receipt": ("GET", r"/me/paid-operations/(?:ask|asr|tts)", "DEVICE_BEARER", 200),
    "revoke": ("DELETE", "/pairing-sessions/{pairing_id}", "WEB_GUARDIAN_CSRF_RECENT_LOGIN", 204),
}
ERROR_STATUS = {
    "AUTH_REQUIRED": 401,
    "RESOURCE_NOT_FOUND": 404,
    "SESSION_CONFLICT": 409,
    "PAIRING_ALREADY_ISSUED": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "REQUEST_IN_PROGRESS": 409,
    "PAID_RESULT_UNAVAILABLE": 409,
    "PAID_SOURCE_UNRESOLVED": 409,
    "REQUEST_TOO_LARGE": 413,
    "DEVICE_CONTENT_UNSUPPORTED": 422,
    "PAID_BUDGET_EXHAUSTED": 429,
    "DEPENDENCY_UNAVAILABLE": 503,
    "VALIDATION_ERROR": 422,
    "RATE_LIMITED": 429,
    "REQUEST_OUTCOME_UNKNOWN": 504,
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def exact(value, *fields):
    require(isinstance(value, dict) and set(value) == set(fields), f"expected fields {fields}")


def integer(value, minimum=1):
    return type(value) is int and minimum <= value <= MAX_ID


def text(value, maximum=64, *, allow_empty=False):
    if not isinstance(value, str) or "\x00" in value or (not value and not allow_empty):
        return False
    try:
        return len(value.encode("utf-8")) <= maximum
    except UnicodeEncodeError:
        return False

def json_tokens(value, depth=0):
    if isinstance(value, (dict, list)):
        require(depth < JSON_DEPTH_MAX and len(value) <= CONTAINER_ITEMS_MAX, "JSON container limit exceeded")
        if isinstance(value, dict):
            return 2 + sum(json_tokens(key, depth + 1) + json_tokens(item, depth + 1)
                           for key, item in value.items())
        return 2 + sum(json_tokens(item, depth + 1) for item in value)
    require(not isinstance(value, str) or text(value, RESPONSE_JSON_MAX, allow_empty=True),
            "JSON contains invalid Unicode")
    return 1


def unique_object(pairs):
    result = dict(pairs)
    require(len(result) == len(pairs), "duplicate JSON key")
    return result


def reject_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def validate_wire(payload, maximum):
    require(0 < len(payload) <= maximum, "JSON wire byte limit exceeded")
    decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
    require(isinstance(decoded, dict) and json_tokens(decoded) <= JSON_TOKENS_MAX,
            "invalid JSON root or token count")


def wire_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")



def request_key(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value))


def placeholder(value, name):
    return value == f"MOCK[{name}]"


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r"2099-\d\d-\d\dT\d\d:\d\d:\d\dZ", value),
            "expected synthetic UTC timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def media_key(value):
    return isinstance(value, str) and bool(re.fullmatch(MEDIA_SEGMENT, value))


def media_path(path):
    return text(path, URL_PATH_MAX) and bool(re.fullmatch(PREFIX + ROUTES["media"][1], path))


def validate_request(operation, request, actor):
    fields = ["method", "path", "auth"]
    if operation == "asr":
        fields.extend(("multipart", "headers"))
    elif operation in ("issue", "claim", "events", "questions", "tts"):
        fields.append("json")
        if operation == "tts":
            fields.append("headers")
    elif operation == "revoke":
        fields.append("params")
    elif operation == "lookup":
        fields.append("query")
    elif operation == "receipt":
        fields.append("headers")
    exact(request, *fields)

    method, path, _, _ = ROUTES[operation]
    path_matches = re.fullmatch(PREFIX + path, request["path"])
    require(request["method"] == method and path_matches, "wrong method or path")
    auth = request["auth"]
    require(auth == actor or (operation == "assignment" and auth == "WEB_COOKIE_ONLY"), "wrong auth label")

    body = request.get("json")
    if body is not None:
        validate_wire(wire_bytes(body), WRITE_JSON_MAX)
    if operation == "lookup":
        exact(request["query"], "readingSessionId")
        require(integer(request["query"]["readingSessionId"]), "invalid pairing lookup query")
    if operation == "issue":
        exact(body, "readingSessionId", "deviceId", "deviceCommitment", "clientRequestId")
        require(integer(body["readingSessionId"]) and placeholder(body["deviceId"], "DEVICE_ID")
                and placeholder(body["deviceCommitment"], "DEVICE_COMMITMENT") and request_key(body["clientRequestId"]),
                "invalid issue fixture")
    elif operation == "claim":
        exact(body, "deviceId", "pairingCode", "deviceProof")
        require(placeholder(body["deviceId"], "DEVICE_ID") and placeholder(body["deviceProof"], "DEVICE_PROOF")
                and body["pairingCode"] in {"MOCK[PAIRING_CODE]", "MOCK[WRONG_CODE]"}, "invalid claim fixture")
    elif operation in ("events", "questions"):
        extra = "eventType" if operation == "events" else "inputText"
        exact(body, "pageNumber", "expectedRevision", "clientEventId", extra)
        require(integer(body["pageNumber"]) and integer(body["expectedRevision"], 0)
                and request_key(body["clientEventId"]), "invalid reading event fields")
        require(body[extra] in (("PAGE_CHANGED",) if operation == "events" else ("这一页发生了什么？", "故事里有谁？")),
                "operation outside device scope")
    elif operation == "revoke":
        params = request["params"]
        exact(params, "pairing_id")
        require(placeholder(params["pairing_id"], "PAIRING_ID"), "invalid revoke fixture")

    if operation in ("asr", "tts", "receipt"):
        headers = request["headers"]
        exact(headers, "Idempotency-Key")
        require(request_key(headers["Idempotency-Key"]), "invalid voice idempotency key")
    if operation == "asr":
        form = request["multipart"]
        exact(form, "file", "readingSessionId", "pageNumber", "revision")
        require(placeholder(form["file"], "ADULT_WAV"), "expected MOCK adult audio reference")
        for field in ("readingSessionId", "pageNumber", "revision"):
            minimum = 0 if field == "revision" else 1
            require(isinstance(form[field], str) and re.fullmatch(r"[0-9]{1,16}", form[field])
                    and integer(int(form[field]), minimum), f"invalid multipart {field}")
    if operation == "tts":
        if set(body) == {"interactionEventId"}:
            require(integer(body["interactionEventId"]), "invalid event source")
        else:
            exact(body, "readingSessionId", "pageNumber", "revision")
            require(integer(body["readingSessionId"]) and integer(body["pageNumber"])
                    and integer(body["revision"], 0), "invalid page source")


def validate_assignment(data):
    exact(data, "deviceId", "readingSessionId", "storybookId", "storyVersionId", "versionNumber", "mode", "status",
          "expiresAt", "currentPage", "revision", "page", "lastDeviceEvent")
    require(placeholder(data["deviceId"], "DEVICE_ID") and data["mode"] == "PARENT_CHILD"
            and data["status"] == "IN_PROGRESS", "assignment outside first-phase scope")
    for field in ("readingSessionId", "storybookId", "storyVersionId", "versionNumber", "currentPage", "revision"):
        require(integer(data[field], 0 if field == "revision" else 1), f"invalid assignment {field}")
    timestamp(data["expiresAt"])
    page = data["page"]
    exact(page, "text", "imageRef", "defaultNextPageNumber")
    require(text(page["text"], PAGE_TEXT_MAX, allow_empty=True), "invalid page text")
    require(page["imageRef"] is None or media_key(page["imageRef"]), "invalid page image reference")
    next_page = page["defaultNextPageNumber"]
    require(next_page is None or (integer(next_page) and next_page != data["currentPage"]), "invalid next page")
    receipt = data["lastDeviceEvent"]
    if receipt is not None:
        exact(receipt, "clientEventId", "interactionEventId", "revision")
        require(request_key(receipt["clientEventId"]) and integer(receipt["interactionEventId"])
                and integer(receipt["revision"], 0) and receipt["revision"] <= data["revision"], "invalid event receipt")


def validate_output(operation, data):
    if operation in ("events", "questions"):
        extra = ("status", "outputText") if operation == "questions" else ()
        exact(data, "interactionEventId", "revision", *extra)
        require(integer(data["interactionEventId"]) and integer(data["revision"], 0), "invalid event response")
        if extra:
            require(data["status"] in {"GENERATED", "FALLBACK", "BLOCKED"}
                    and text(data["outputText"], PAGE_TEXT_MAX, allow_empty=True), "invalid question response")
    elif operation == "asr":
        exact(data, "text", "status", "reliable")
        require(text(data["text"], PAGE_TEXT_MAX, allow_empty=True) and data["status"] == "RECOGNIZED"
                and type(data["reliable"]) is bool, "invalid transcription response")
    elif operation == "tts":
        exact(data, "audioUrl", "expiresAt")
        require(media_path(data["audioUrl"]), "expected same-service device media URL")
        timestamp(data["expiresAt"])


def validate_pairing(data):
    exact(data, "pairing")
    pairing = data["pairing"]
    if pairing is not None:
        exact(pairing, "pairingId", "status", "expiresAt")
        require(placeholder(pairing["pairingId"], "PAIRING_ID")
                and pairing["status"] in {"PENDING", "ACTIVE", "EXPIRED", "REVOKED"}, "invalid pairing metadata")
        timestamp(pairing["expiresAt"])


def validate_receipt(action, data):
    require(data["state"] in {"SUCCEEDED", "FAILED"}, "invalid terminal receipt state")
    if data["state"] == "FAILED":
        exact(data, "state", "error")
        exact(data["error"], "code", "message")
        require(text(data["error"]["code"], 64) and text(data["error"]["message"], 192),
                "invalid receipt error")
        return
    exact(data, "state", "result")
    if data["result"] is None:
        require(action in {"asr", "tts"}, "successful ASK has a persisted event")
    else:
        validate_output(PAID_OPERATIONS[action], data["result"])


def validate_success(operation, response, path):
    if operation == "revoke":
        exact(response, "status")
        return
    if operation == "media":
        exact(response, "status", "headers")
        exact(response["headers"], "Content-Type", "Cache-Control")
        require(response["headers"]["Content-Type"] in {"image/jpeg", "image/png", "image/webp", "audio/wav"},
                "invalid media MIME")
    else:
        exact(response, "status", "headers", "json")
        exact(response["headers"], "Cache-Control")
        exact(response["json"], "data", "requestId")
    require(response["headers"]["Cache-Control"] == "private, no-store", "private response could be cached")
    if operation == "media":
        return

    data = response["json"]["data"]
    if operation == "issue":
        exact(data, "pairingId", "pairingCode", "expiresAt")
        require(placeholder(data["pairingId"], "PAIRING_ID") and placeholder(data["pairingCode"], "PAIRING_CODE"),
                "expected non-operational pairing placeholders")
        timestamp(data["expiresAt"])
    elif operation == "claim":
        exact(data, "deviceId", "accessToken", "expiresAt")
        require(placeholder(data["deviceId"], "DEVICE_ID") and placeholder(data["accessToken"], "ACCESS_TOKEN"),
                "expected non-operational token placeholder")
        timestamp(data["expiresAt"])
    elif operation == "assignment":
        validate_assignment(data)
    elif operation == "lookup":
        validate_pairing(data)
    elif operation == "receipt":
        validate_receipt(path.rsplit("/", 1)[-1], data)
    else:
        validate_output(operation, data)


def validate_case(case, matrix_ids):
    fields = ("id", "matrix", "operation", "request", "response")
    invalid = case.get("invalidRequest")
    exact(case, *fields, *(("invalidRequest",) if invalid is True else ()))
    require(case["matrix"] in matrix_ids, "unknown acceptance matrix ID")
    operation = case["operation"]
    _, _, actor, success = ROUTES[operation]
    request, response = case["request"], case["response"]
    status = response["status"]

    # The negative example is a valid page-source request with an extra text field.
    if invalid:
        require(operation == "tts" and "text" in request["json"] and status == 422, "invalid negative control")
        sanitized = {**request, "json": {key: value for key, value in request["json"].items() if key != "text"}}
        validate_request(operation, sanitized, actor)
        try:
            validate_request(operation, request, actor)
        except ValueError:
            pass
        else:
            raise ValueError("TTS accepted an arbitrary text field")
    else:
        validate_request(operation, request, actor)
    if request["auth"] == "WEB_COOKIE_ONLY":
        require(status == 401, "Web cookie must not be a device principal in the examples")
    if status == success:
        validate_success(operation, response, request["path"])
    else:
        code = response["json"]["error"]["code"]
        must_retry_after = code in {"RATE_LIMITED", "REQUEST_IN_PROGRESS"}
        transient_read = status == 503 and request["method"] == "GET"
        retryable = must_retry_after or transient_read
        has_retry_after = must_retry_after or (transient_read and "headers" in response)
        exact(response, "status", "json", *(("headers",) if has_retry_after else ()))
        exact(response["json"], "error", "requestId")
        error = response["json"]["error"]
        exact(error, "code", "message", "retryable")
        require(ERROR_STATUS.get(code) == status and text(code, 64) and text(error["message"], 192)
                and error["retryable"] is retryable, "error envelope mismatch")
        if has_retry_after:
            exact(response["headers"], "Retry-After")
            require(re.fullmatch(r"[1-9][0-9]*", response["headers"]["Retry-After"]), "invalid Retry-After")
    if "json" in response:
        validate_wire(wire_bytes(response["json"]), RESPONSE_JSON_MAX)
        request_id = response["json"]["requestId"]
        require(text(request_id, 80) and request_id == f"mock-request-{case['id']}", "invalid requestId")


def validate_timeline(cases):
    def data(case_id):
        return cases[case_id]["response"]["json"]["data"]

    def request(case_id):
        return cases[case_id]["request"]

    issue, claim = data("issue"), data("claim")
    before, after = data("assignment"), data("assignment-recovery")
    require(issue["pairingCode"] == request("claim")["json"]["pairingCode"]
            and issue["pairingId"] == request("revoke")["params"]["pairing_id"], "pairing examples disagree")
    require(claim["deviceId"] == request("issue")["json"]["deviceId"] == before["deviceId"] == after["deviceId"]
            and before["readingSessionId"] == after["readingSessionId"] == request("issue")["json"]["readingSessionId"]
            and before["storyVersionId"] == after["storyVersionId"], "approved assignment changed identity")
    require(timestamp(issue["expiresAt"]) < timestamp(claim["expiresAt"])
            and timestamp(data("tts")["expiresAt"]) <= timestamp(claim["expiresAt"])
            and before["expiresAt"] == after["expiresAt"] == claim["expiresAt"], "expiry mismatch")
    require(request("image")["path"] == f"{PREFIX}/me/media/{before['page']['imageRef']}"
            and request("audio-expired")["path"] == data("tts")["audioUrl"], "media reference mismatch")

    advance, result = request("advance")["json"], data("advance")
    require(request("advance-replay")["json"] == request("uncertain")["json"] == advance
            and data("advance-replay") == result, "same-key recovery does not match the original request")
    require(before["page"]["defaultNextPageNumber"] == advance["pageNumber"] == after["currentPage"]
            and before["revision"] == advance["expectedRevision"]
            and after["revision"] == result["revision"] == before["revision"] + 1
            and after["lastDeviceEvent"] == {**result, "clientEventId": advance["clientEventId"]},
            "page progression or recovery receipt mismatch")
    require(request("ask")["json"]["pageNumber"] == after["currentPage"]
            and request("ask")["json"]["expectedRevision"] == after["revision"]
            and request("asr")["multipart"]["revision"] == str(data("ask")["revision"])
            and request("tts")["json"]["interactionEventId"] == data("ask")["interactionEventId"],
            "voice examples are not scoped to the current page and event")

def validate_review_timeline(cases):
    def request(case_id):
        return cases[case_id]["request"]

    def response(case_id):
        return cases[case_id]["response"]

    def data(case_id):
        return response(case_id)["json"]["data"]

    issue, claim = data("issue"), data("claim")
    for suffix in ("pending", "active", "expired", "revoked"):
        case_id = f"pairing-{suffix}"
        pairing = data(case_id)["pairing"]
        require(pairing["pairingId"] == issue["pairingId"] and pairing["status"] == suffix.upper(),
                "lookup changed the pairing generation")
        original = issue if suffix in {"pending", "expired"} else claim
        require(pairing["expiresAt"] == original["expiresAt"]
                and request(case_id)["query"]["readingSessionId"] == request("issue")["json"]["readingSessionId"],
                "pairing status expiry or ownership context changed")
    require(data("pairing-none")["pairing"] is None
            and request("issue-replay") == request("issue")
            and response("issue-replay")["status"] == 409
            and request("revoke-replay") == request("revoke")
            and response("revoke-replay")["status"] == 204, "pairing recovery fixture mismatch")

    device = data("assignment-recovery")
    guardian = data("poll-guardian-advance")
    require(guardian["revision"] > device["revision"]
            and guardian["currentPage"] == device["page"]["defaultNextPageNumber"]
            and guardian["lastDeviceEvent"] == device["lastDeviceEvent"], "guardian poll lost progress or receipt")
    backward = request("backward-rejected")["json"]
    require(backward["expectedRevision"] == device["revision"] and backward["pageNumber"] < device["currentPage"]
            and response("backward-rejected")["status"] == 409
            and data("poll-after-rejected-backward") == device, "invalid backward event changed progress")

    for receipt_id, original_id, action in (
        ("paid-ask-unknown", "ask", "ask"), ("paid-ask-running", "ask", "ask"),
        ("paid-ask-success", "ask", "ask"), ("paid-asr-success", "asr", "asr"),
        ("paid-asr-before-dispatch-failed", "asr", "asr"), ("paid-tts-cache-expired", "tts", "tts"),
    ):
        original = request(original_id)
        key = original["json"]["clientEventId"] if action == "ask" else original["headers"]["Idempotency-Key"]
        receipt = request(receipt_id)
        require(receipt["path"] == f"{PREFIX}/me/paid-operations/{action}"
                and receipt["headers"]["Idempotency-Key"] == key, "receipt does not recover the original key")
    for name, status, code in (
        ("paid-ask-running", 409, "REQUEST_IN_PROGRESS"),
        ("paid-ask-unknown", 504, "REQUEST_OUTCOME_UNKNOWN"),
        ("paid-ask-same-key-changed", 409, "IDEMPOTENCY_CONFLICT"),
        ("paid-source-unresolved", 409, "PAID_SOURCE_UNRESOLVED"),
        ("paid-new-key-rate-limited", 429, "RATE_LIMITED"),
        ("paid-budget-exhausted", 429, "PAID_BUDGET_EXHAUSTED"),
    ):
        value = response(name)
        require(value["status"] == status and value["json"]["error"]["code"] == code, "wrong paid recovery state")
    require(data("paid-ask-success")["result"] == data("ask")
            and data("paid-asr-success")["result"] == data("asr")
            and data("paid-tts-cache-expired")["result"] is None
            and data("paid-asr-before-dispatch-failed")["state"] == "FAILED", "paid receipt lost saved state")


def supports_wire(payload, maximum):
    try:
        validate_wire(payload, maximum)
    except ValueError:
        return False
    return True


def validate_dto_boundaries(document):
    limits = document["draftLimits"]
    expected = {
        "pageTextBytes": PAGE_TEXT_MAX, "mediaKeyBytes": MEDIA_KEY_MAX, "writeJsonBytes": WRITE_JSON_MAX,
        "responseJsonBytes": RESPONSE_JSON_MAX, "jsonDepth": JSON_DEPTH_MAX, "jsonTokens": JSON_TOKENS_MAX,
        "containerItems": CONTAINER_ITEMS_MAX, "pollMs": 3000, "requestDeadlineMs": 3000,
        "revokeTargetMs": 7000, "privateVisibilityMs": 8000,
    }
    require(limits == expected, "draft limits differ from the checker")
    checks = {
        "pageTextBytes": lambda value: text(value, PAGE_TEXT_MAX, allow_empty=True),
        "mediaKeyBytes": media_key,
        "writeJsonBytes": lambda value: supports_wire(b"{}" + value.encode("utf-8"), WRITE_JSON_MAX),
        "responseJsonBytes": lambda value: supports_wire(b"{}" + value.encode("utf-8"), RESPONSE_JSON_MAX),
    }
    boundaries = document["dtoBoundaryExamples"]
    for case in boundaries:
        exact(case, "field", "unit", "repeat", "suffix", "accepted")
        require(integer(case["repeat"], 0) and case["repeat"] <= RESPONSE_JSON_MAX, "invalid fixture repetition")
        value = case["unit"] * case["repeat"] + case["suffix"]
        require(bool(checks[case["field"]](value)) is case["accepted"], f"wrong boundary result for {case['field']}")

    at_depth_limit = b'{"x":' + b"[" * 11 + b"0" + b"]" * 11 + b"}"
    at_item_limit = wire_bytes({"x": [0] * CONTAINER_ITEMS_MAX})
    at_token_limit = wire_bytes({"x": [[0] * 128] * 15 + [[0] * 91]})
    require(all(supports_wire(payload, RESPONSE_JSON_MAX) for payload in
                (at_depth_limit, at_item_limit, at_token_limit)), "valid structure boundary rejected")
    invalid = (b'{"key":"\xff"}', br'{"key":"\u0000"}', br'{"key":"\ud800"}',
               b'{"key":1,"key":2}', b'{"key":NaN}',
               b'{"x":' + b"[" * 12 + b"0" + b"]" * 12 + b"}",
               wire_bytes({"x": [0] * (CONTAINER_ITEMS_MAX + 1)}),
               wire_bytes({"x": [[0] * 128] * 15 + [[0] * 92]}))
    require(not any(supports_wire(payload, RESPONSE_JSON_MAX) for payload in invalid),
            "invalid wire control was accepted")


def validate_visibility(examples, matrix_ids, duration):
    against_deadline = {"WAITING", "OTHER_SUCCESS", "STALE_RESPONSE"}
    early_stop = {"SERVICE_FAILURE", "BACKGROUND", "REVOKED"}
    for case in examples:
        exact(case, "id", "matrix", "lastAcceptedStartMs", "tokenUntilMs", "observedAtMs", "event", "visible")
        require(case["matrix"] in matrix_ids and case["event"] in against_deadline | early_stop,
                "unknown visibility fixture")
        start, expiry, observed = case["lastAcceptedStartMs"], case["tokenUntilMs"], case["observedAtMs"]
        require(all(integer(value, 0) for value in (start, expiry, observed)) and start <= observed,
                "invalid fixture times")
        visible = case["event"] in against_deadline and observed < min(start + duration, expiry)
        require(case["visible"] is visible, f"incorrect draft visibility example: {case['id']}")



def main():
    document = json.loads((ROOT / "examples.json").read_text(encoding="utf-8"))
    require(document["namespace"] == "draft.device-authorization" and document["sampleData"] == "MOCK"
            and document["status"] == "DRAFT / 待团队评审 / 未实现"
            and document["designChange"] == "DCL-025 / D-HW-03", "missing draft metadata")
    matrix = (ROOT / "acceptance.md").read_text(encoding="utf-8")
    require(document["baselineMain"] in matrix and document["date"] in matrix, "baseline or date mismatch")
    validate_dto_boundaries(document)
    require(document["reviewBaseline"].startswith("b336503") and document["h08DtoBaseline"] == "0a3ec8404ba80bd41894874ee24b21941989fe2d",
            "missing second-review baselines")
    matrix_ids = set(re.findall(r"^\| ([A-Z]\d{2}) ", matrix, flags=re.MULTILINE))
    cases = {}
    for case in document["examples"]:
        case_id = case["id"]
        require(case_id not in cases, f"duplicate case ID {case_id}")
        try:
            validate_case(case, matrix_ids)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{case_id}: {exc}") from exc
        cases[case_id] = case
    require({case["operation"] for case in cases.values() if case["response"]["status"] < 400} == set(ROUTES),
            "missing a success example for a draft route")
    validate_timeline(cases)
    validate_review_timeline(cases)
    visibility = document["visibilityExamples"]
    validate_visibility(visibility, matrix_ids, document["draftLimits"]["privateVisibilityMs"])
    boundaries = document["dtoBoundaryExamples"]
    print(f"DRAFT only: checked {len(cases)} HTTP examples, {len(ROUTES)} routes, {len(matrix_ids)} acceptance IDs, "
          f"{len(visibility)} visibility examples and {len(boundaries)} DTO boundary examples.")


if __name__ == "__main__":
    main()
