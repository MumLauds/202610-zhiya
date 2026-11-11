import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema.sql"
client = TestClient(app)


def test_health_checks_database_readiness(tmp_path, monkeypatch):
    path = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    unavailable = client.get("/api/v1/health")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["retryable"] is True
    assert unavailable.json()["requestId"]
    assert path.name not in unavailable.text
    assert not path.exists()

    with closing(sqlite3.connect(path)) as db:
        assert client.get("/api/v1/health").status_code == 503
        db.executescript(SCHEMA.read_text(encoding="utf-8"))

    ready = client.get("/api/v1/health")
    assert ready.status_code == 200
    assert ready.json() == {"data": {"status": "ok"}, "requestId": ready.json()["requestId"]}
    assert ready.json()["requestId"] != unavailable.json()["requestId"]


def test_no_business_routes_are_exposed():
    assert set(client.get("/openapi.json").json()["paths"]) == {"/api/v1/health"}
