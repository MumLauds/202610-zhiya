import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.speech_config import SpeechSettings
from app.main import app, create_app

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema.sql"
client = TestClient(app)


def test_health_checks_database_readiness(tmp_path, monkeypatch):
    path = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    unavailable = client.get("/api/v1/health")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["retryable"] is True
    assert unavailable.json()["requestId"]
    assert unavailable.json()["requestId"] == unavailable.headers["X-Request-ID"]
    assert path.name not in unavailable.text
    assert not path.exists()

    with closing(sqlite3.connect(path)) as db:
        assert client.get("/api/v1/health").status_code == 503
        db.executescript(SCHEMA.read_text(encoding="utf-8"))

    ready = client.get("/api/v1/health")
    assert ready.status_code == 200
    assert ready.json() == {"data": {"status": "ok"}, "requestId": ready.json()["requestId"]}
    assert ready.json()["requestId"] != unavailable.json()["requestId"]
    assert ready.json()["requestId"] == ready.headers["X-Request-ID"]


def test_voice_routes_fail_closed_without_business_authentication():
    assert set(client.get("/openapi.json").json()["paths"]) == {
        "/api/v1/health", "/api/v1/voice/transcriptions", "/api/v1/voice/syntheses", "/api/v1/media/{resource_key}"}
    for method, path in (("POST", "/api/v1/voice/transcriptions"), ("POST", "/api/v1/voice/syntheses"),
                         ("GET", "/api/v1/media/audio-unknown")):
        response = client.request(method, path)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "VOICE_AUTH_NOT_CONFIGURED"


def test_app_manages_model_gateway_lifecycle():
    with patch("app.main.ModelGateway", autospec=True) as gateway_type, patch("app.main.VoiceGateway", autospec=True) as voice_type:
        gateway = gateway_type.return_value
        gateway.__aenter__.return_value = gateway
        voice = voice_type.return_value
        voice.__aenter__.return_value = voice
        application = create_app(Settings(), SpeechSettings())
        with TestClient(application):
            assert application.state.model_gateway is gateway
            assert application.state.voice_gateway is voice
            gateway.__aenter__.assert_awaited_once()
            voice.__aenter__.assert_awaited_once()
            gateway.__aexit__.assert_not_awaited()
            voice.__aexit__.assert_not_awaited()
        gateway.__aexit__.assert_awaited_once()
        voice.__aexit__.assert_awaited_once()
