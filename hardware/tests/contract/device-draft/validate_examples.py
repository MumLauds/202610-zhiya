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
ROUTES = {
    "issue": ("POST", "/pairing-sessions", "WEB_GUARDIAN_CSRF", 201),
    "claim": ("POST", "/pairings/claim", "PAIRING_PROOF", 201),
    "assignment": ("GET", "/me/assignment", "DEVICE_BEARER", 200),
    "events": ("POST", "/me/events", "DEVICE_BEARER", 201),
    "questions": ("POST", "/me/questions", "DEVICE_BEARER", 201),
    "asr": ("POST", "/me/voice/transcriptions", "DEVICE_BEARER", 200),
    "tts": ("POST", "/me/voice/syntheses", "DEVICE_BEARER", 201),
    "media": ("GET", r"/me/media/[A-Za-z0-9_-]{1,128}", "DEVICE_BEARER", 200),
    "revoke": ("DELETE", "/pairing-sessions/{pairing_id}", "WEB_GUARDIAN_CSRF_RECENT_LOGIN", 204),
}
ERROR_STATUS = {
    "AUTH_REQUIRED": 401,
    "RESOURCE_NOT_FOUND": 404,
    "SESSION_CONFLICT": 409,
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


def text(value, maximum=64):
    return isinstance(value, str) and 0 < len(value) <= maximum


def placeholder(value, name):
    return value == f"MOCK[{name}]"


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r"2099-\d\d-\d\dT\d\d:\d\d:\d\dZ", value),
            "expected synthetic UTC timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def media_path(path):
    return isinstance(path, str) and re.fullmatch(PREFIX + ROUTES["media"][1], path)


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
    exact(request, *fields)

    method, path, _, _ = ROUTES[operation]
    path_matches = media_path(request["path"]) if operation == "media" else request["path"] == PREFIX + path
    require(request["method"] == method and path_matches, "wrong method or path")
    auth = request["auth"]
    require(auth == actor or (operation == "assignment" and auth == "WEB_COOKIE_ONLY"), "wrong auth label")

    body = request.get("json")
    if operation == "issue":
        exact(body, "readingSessionId", "deviceId", "deviceCommitment", "clientRequestId")
        require(integer(body["readingSessionId"]) and placeholder(body["deviceId"], "DEVICE_ID")
                and placeholder(body["deviceCommitment"], "DEVICE_COMMITMENT") and text(body["clientRequestId"]),
                "invalid issue fixture")
    elif operation == "claim":
        exact(body, "deviceId", "pairingCode", "deviceProof")
        require(placeholder(body["deviceId"], "DEVICE_ID") and placeholder(body["deviceProof"], "DEVICE_PROOF")
                and body["pairingCode"] in {"MOCK[PAIRING_CODE]", "MOCK[WRONG_CODE]"}, "invalid claim fixture")
    elif operation in ("events", "questions"):
        extra = "eventType" if operation == "events" else "inputText"
        exact(body, "pageNumber", "expectedRevision", "clientEventId", extra)
        require(integer(body["pageNumber"]) and integer(body["expectedRevision"], 0)
                and text(body["clientEventId"]), "invalid reading event fields")
        require(body[extra] in (("PAGE_CHANGED",) if operation == "events" else ("这一页发生了什么？", "故事里有谁？")),
                "operation outside device scope")
    elif operation == "revoke":
        params = request["params"]
        exact(params, "pairing_id")
        require(placeholder(params["pairing_id"], "PAIRING_ID"), "invalid revoke fixture")

    if operation in ("asr", "tts"):
        headers = request["headers"]
        exact(headers, "Idempotency-Key")
        require(text(headers["Idempotency-Key"]), "invalid voice idempotency key")
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
    require(isinstance(page["text"], str) and len(page["text"].encode("utf-8")) <= 16 * 1024, "invalid page text")
    require(page["imageRef"] is None or (isinstance(page["imageRef"], str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", page["imageRef"])), "invalid page image reference")
    require(page["defaultNextPageNumber"] is None or integer(page["defaultNextPageNumber"]), "invalid next page")
    receipt = data["lastDeviceEvent"]
    if receipt is not None:
        exact(receipt, "clientEventId", "interactionEventId", "revision")
        require(text(receipt["clientEventId"]) and integer(receipt["interactionEventId"])
                and integer(receipt["revision"], 0) and receipt["revision"] <= data["revision"], "invalid event receipt")


def validate_success(operation, response):
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
    elif operation in ("events", "questions"):
        extra = ("status", "outputText") if operation == "questions" else ()
        exact(data, "interactionEventId", "revision", *extra)
        require(integer(data["interactionEventId"]) and integer(data["revision"], 0), "invalid event response")
        if extra:
            require(data["status"] in {"GENERATED", "FALLBACK", "BLOCKED"} and isinstance(data["outputText"], str),
                    "invalid question response")
    elif operation == "asr":
        exact(data, "text", "status", "reliable")
        require(isinstance(data["text"], str) and data["status"] == "RECOGNIZED"
                and type(data["reliable"]) is bool, "invalid transcription response")
    elif operation == "tts":
        exact(data, "audioUrl", "expiresAt")
        require(media_path(data["audioUrl"]), "expected same-service device media URL")
        timestamp(data["expiresAt"])


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
        validate_success(operation, response)
    else:
        exact(response, "status", "json", *(("headers",) if status == 429 else ()))
        exact(response["json"], "error", "requestId")
        error = response["json"]["error"]
        exact(error, "code", "message", "retryable")
        require(ERROR_STATUS.get(error["code"]) == status and text(error["message"], 256)
                and error["retryable"] is (status == 429), "error envelope mismatch")
        if status == 429:
            exact(response["headers"], "Retry-After")
            require(re.fullmatch(r"[1-9][0-9]*", response["headers"]["Retry-After"]), "invalid Retry-After")
    if "json" in response:
        require(response["json"]["requestId"] == f"mock-request-{case['id']}", "invalid requestId")


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


def main():
    document = json.loads((ROOT / "examples.json").read_text(encoding="utf-8"))
    require(document["namespace"] == "draft.device-authorization" and document["sampleData"] == "MOCK"
            and document["status"] == "DRAFT / 待团队评审 / 未实现"
            and document["designChange"] == "DCL-025 / D-HW-03", "missing draft metadata")
    matrix = (ROOT / "acceptance.md").read_text(encoding="utf-8")
    require(document["baselineMain"] in matrix and document["date"] in matrix, "baseline or date mismatch")
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
    print(f"DRAFT only: checked {len(cases)} examples, {len(ROUTES)} routes and {len(matrix_ids)} acceptance IDs.")


if __name__ == "__main__":
    main()
