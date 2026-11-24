import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1.auth import _login_attempts
from app.main import app

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema.sql"


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
    _login_attempts.clear()
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client, path
    _login_attempts.clear()


def register_and_login(client, account="guardian"):
    credentials = {"account": account, "password": "strong-pass-123"}
    assert client.post("/api/v1/auth/registrations", json=credentials).status_code == 201
    response = client.post("/api/v1/auth/sessions", json=credentials)
    assert response.status_code == 201
    cookies = response.headers.get_list("set-cookie")
    assert any("zhiya_session=" in item and "Secure" in item and "HttpOnly" in item
               and "SameSite=lax" in item for item in cookies)
    return {"X-CSRF-Token": client.cookies["zhiya_csrf"]}


def test_registration_login_csrf_and_logout(client):
    client, path = client
    headers = register_and_login(client)
    duplicate = client.post("/api/v1/auth/registrations", json={"account": "guardian", "password": "strong-pass-123"})
    assert duplicate.status_code == 409
    with closing(sqlite3.connect(path)) as db:
        session_id, revoked = db.execute("SELECT id, revoked_at FROM auth_sessions").fetchone()
        assert revoked is None
        stored = db.execute("SELECT password_hash FROM accounts").fetchone()[0]
        assert stored.startswith("scrypt$") and "strong-pass-123" not in stored
    assert client.post("/api/v1/child-profiles", json={"ageBand": "AGE_4_5", "preferredMinutes": 10}).status_code == 403
    response = client.post("/api/v1/child-profiles", headers=headers, json={
        "nickname": " 小芽 ", "ageBand": "AGE_4_5", "preferredMinutes": 10
    })
    assert response.status_code == 201
    profile = response.json()["data"]
    assert profile["nickname"] == "小芽" and profile["interests"] == []
    assert client.delete("/api/v1/auth/sessions/current").status_code == 403
    assert client.delete("/api/v1/auth/sessions/current", headers=headers).status_code == 204
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT revoked_at FROM auth_sessions WHERE id = ?", (session_id,)).fetchone()[0] is not None
    assert client.get("/api/v1/child-profiles").status_code == 401


def test_current_identity_restores_admin_without_granting_guardian_access(client):
    client, path = client
    url = "/api/v1/auth/sessions/current"
    assert client.get(url).status_code == 401
    headers = register_and_login(client)
    guardian = client.get(url)
    assert guardian.json()["data"]["role"] == "GUARDIAN"
    account_id = guardian.json()["data"]["accountId"]
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE accounts SET role = 'ADMIN' WHERE id = ?", (account_id,))
    response = client.get(url)
    assert response.json()["data"] == {"role": "ADMIN", "accountId": account_id}
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/child-profiles").status_code == 403
    assert client.delete(url).status_code == 403
    assert client.delete(url, headers=headers).status_code == 204
    assert client.get(url).status_code == 401


def test_expired_revoked_and_invalid_sessions_are_rejected(client):
    client, path = client
    headers = register_and_login(client)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE auth_sessions SET expires_at = 0")
    assert client.get("/api/v1/child-profiles").status_code == 401
    assert client.delete("/api/v1/auth/sessions/current", headers=headers).status_code == 401


def test_non_ascii_csrf_rejected_without_changing_profile_or_session(client):
    client, _ = client
    headers = register_and_login(client)
    payload = {"ageBand": "AGE_4_5", "preferredMinutes": 10}
    bad_headers = {"X-CSRF-Token": b"\xff"}
    profile = client.post("/api/v1/child-profiles", headers=headers, json=payload).json()["data"]
    for response in (
        client.post("/api/v1/child-profiles", headers=bad_headers, json=payload),
        client.patch(f"/api/v1/child-profiles/{profile['childProfileId']}", headers=bad_headers,
                     json={"preferredMinutes": 20}),
        client.delete("/api/v1/auth/sessions/current", headers=bad_headers),
    ):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CSRF_INVALID"
    assert client.get("/api/v1/child-profiles").json()["data"]["items"] == [profile]
    assert client.delete("/api/v1/auth/sessions/current", headers=headers).status_code == 204


def test_profile_ownership_and_partial_updates(client):
    first, _ = client
    headers = register_and_login(first)
    create = first.post("/api/v1/child-profiles", headers=headers, json={
        "ageBand": "AGE_3_4", "preferredMinutes": 12, "interests": [" 动物 "]
    })
    profile_id = create.json()["data"]["childProfileId"]
    update = first.patch(f"/api/v1/child-profiles/{profile_id}", headers=headers,
                         json={"interests": [], "nickname": None})
    assert update.status_code == 200
    assert update.json()["data"]["ageBand"] == "AGE_3_4"
    assert update.json()["data"]["interests"] == []
    assert first.patch(f"/api/v1/child-profiles/{profile_id}", headers=headers, json={}).status_code == 422
    assert first.patch(f"/api/v1/child-profiles/{profile_id}", headers=headers, json={"ageBand": None}).status_code == 422
    with TestClient(app, base_url="https://testserver") as second:
        other_headers = register_and_login(second, "second-guardian")
        assert second.get("/api/v1/child-profiles").json()["data"]["items"] == []
        assert second.patch(f"/api/v1/child-profiles/{profile_id}", headers=other_headers,
                            json={"preferredMinutes": 9}).status_code == 404


def test_database_unavailable_and_login_limit(client, monkeypatch, tmp_path):
    client, _ = client
    for _ in range(10):
        assert client.post("/api/v1/auth/sessions", json={"account": "unknown", "password": "bad-pass-123"}).status_code == 401
    response = client.post("/api/v1/auth/sessions", json={"account": "unknown", "password": "bad-pass-123"})
    assert response.status_code == 429 and response.headers["Retry-After"]
    monkeypatch.setenv("ZHIYA_DB_PATH", str(tmp_path / "missing.sqlite3"))
    response = client.post("/api/v1/auth/registrations", json={"account": "guardian", "password": "strong-pass-123"})
    assert response.status_code == 503
    assert not (tmp_path / "missing.sqlite3").exists()


def test_concurrent_distinct_profile_updates_are_preserved(client):
    client, _ = client
    headers = register_and_login(client)
    profile_id = client.post("/api/v1/child-profiles", headers=headers, json={
        "ageBand": "AGE_4_5", "preferredMinutes": 10
    }).json()["data"]["childProfileId"]
    def update(payload):
        return client.patch(f"/api/v1/child-profiles/{profile_id}", headers=headers, json=payload).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, [{"nickname": "小芽"}, {"preferredMinutes": 20}]))
    assert results == [200, 200]
    profile = client.get("/api/v1/child-profiles").json()["data"]["items"][0]
    assert profile["nickname"] == "小芽" and profile["preferredMinutes"] == 20
