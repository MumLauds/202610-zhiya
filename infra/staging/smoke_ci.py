"""Exercise the isolated staging containers with synthetic accounts over verified TLS."""

import json
import ssl
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

PILOT = "synthetic-yaya-pilot"
CONTROL = "synthetic-yaya-control"
LOCAL_ONLY = "synthetic-yaya-local-only"
PASSWORD = "synthetic-pass-2026"
VERSION = 901_000


class Reader:
    def __init__(self, origin, cert, account=None, *, register=False):
        self.origin = origin + "/api/v1"
        self.cookies = CookieJar()
        self.client = build_opener(HTTPSHandler(context=ssl.create_default_context(cafile=cert)),
                                   HTTPCookieProcessor(self.cookies))
        if account:
            credentials = {"account": account, "password": PASSWORD}
            if register:
                self.request("/auth/registrations", credentials, expected=201)
            self.account_id = self.request("/auth/sessions", credentials, expected=201)["accountId"]

    def request(self, path, body=None, *, expected=200):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json"}
        csrf = next((item.value for item in self.cookies if item.name == "zhiya_csrf"), None)
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        try:
            with self.client.open(Request(self.origin + path, data=data, headers=headers), timeout=8) as response:
                assert response.status == expected, (path, response.status)
                assert response.headers["Cache-Control"] == "private, no-store", path
                return json.load(response)["data"]
        except HTTPError as exc:
            raise AssertionError(f"{path} failed with HTTP {exc.code}") from None

    def start(self):
        profile = self.request("/child-profiles", {"ageBand": "AGE_3_4", "preferredMinutes": 10},
                               expected=201)["childProfileId"]
        session = self.request("/reading-sessions", {"childProfileId": profile, "storyVersionId": VERSION,
                                                      "mode": "PARENT_CHILD"}, expected=201)["readingSessionId"]
        self.request(f"/reading-sessions/{session}/events", {"eventType": "STARTED", "pageNumber": 1,
                                                              "expectedRevision": 0, "clientEventId": "start"},
                     expected=201)
        return session

    def answer(self, session, revision, text, key):
        return self.request(f"/reading-sessions/{session}/events", {
            "eventType": "ANSWER", "pageNumber": 1, "expectedRevision": revision,
            "clientEventId": key, "inputText": text,
        }, expected=201)


def main(origin, cert, state_file, mode):
    path = Path(state_file)
    if mode == "setup":
        assert Reader(origin, cert).request("/health")["status"] == "ok"
        pilot = Reader(origin, cert, PILOT, register=True)
        Reader(origin, cert, CONTROL, register=True)
        local_only = Reader(origin, cert, LOCAL_ONLY, register=True)
        path.write_text(json.dumps({"enrolled": [pilot.account_id, local_only.account_id]}), encoding="utf-8")
        return

    state = json.loads(path.read_text(encoding="utf-8"))
    pilot = Reader(origin, cert, PILOT)
    if mode == "allowlist":
        session = pilot.start()
        listen = pilot.answer(session, 1, "你先别问，让我看看它怎么转。", "listen")
        assert (listen["agentReceipt"]["version"], listen["agentReceipt"]["action"]) == ("2.0", "LISTEN")
        saved = pilot.request(f"/reading-sessions/{session}")["events"][-1]
        assert saved["agentReceipt"] == listen["agentReceipt"]
        control = Reader(origin, cert, CONTROL)
        assert "agentReceipt" not in control.answer(control.start(), 1, "我看到松鼠了。", "legacy")
        local_only = Reader(origin, cert, LOCAL_ONLY)
        local = local_only.answer(local_only.start(), 1, "我让雨点住进帐篷。", "without-input-consent")
        assert local["agentReceipt"]["reason"] == "DEPENDENCY_UNAVAILABLE"
        state.update(session=session, revision=listen["revision"])
        path.write_text(json.dumps(state), encoding="utf-8")
    else:
        pinned = pilot.answer(state["session"], state["revision"], "我把小船改成一座车站。", "off-pinned")
        assert (pinned["agentReceipt"]["version"], pinned["agentReceipt"]["reason"]) == ("2.0", "DEPENDENCY_UNAVAILABLE")
        assert "agentReceipt" not in pilot.answer(pilot.start(), 1, "我看到松鼠了。", "off-legacy")


if __name__ == "__main__":
    main(*sys.argv[1:])
