import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.db import yaya_agent as ledger
from app.model_gateway import ModelGateway
from app.services.yaya_agent import Context, YayaAgent, question_result
from app.services.yaya_agent_config import MODEL, YayaSettings, rate_micros


@pytest.fixture
def usage_db(tmp_path, monkeypatch):
    path = tmp_path / "usage.sqlite3"
    path.touch()
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    ledger.install()
    return path


@pytest.mark.parametrize(("value", "micros"), [("0.8", 800_000), ("2.7", 2_700_000), ("1.250001", 1_250_001)])
def test_rate_unit_and_rounding(value, micros):
    assert rate_micros(value) == micros
    assert YayaSettings().cost_micros(1, 1) == 4


@pytest.mark.parametrize("value", ["", "0", "-1", "NaN", "0.0000001", "1001", "1e-3"])
def test_invalid_rates_are_rejected(value):
    with pytest.raises(ValueError):
        rate_micros(value)


@pytest.mark.parametrize(("fault", "expected_reason", "expected_actual"), [
    ("ok", None, 116),
    ("timeout", "API_TIMEOUT", None),
    ("no_usage", None, None),
    ("partial_usage", None, None),
    ("bad_body", "MODEL_OUTPUT_INVALID", 116),
    ("wrong_model", "MODEL_OUTPUT_INVALID", None),
    ("overrun", None, 25_000),
])
def test_provider_reserves_then_settles_from_usage(usage_db, fault, expected_reason, expected_actual):
    model = Settings(model_api_url="https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
                     model_api_key="synthetic-key", model_name=MODEL, model_max_tokens=512)
    settings = YayaSettings(
        mode="allowlist", guardians=frozenset({1}), model=model, ledger_secret="x" * 32,
        model_input_profiles=frozenset({(1, 3)}), daily_micros=15_000,
        input_micros_per_million=1_250_000, output_micros_per_million=3_500_000,
    )
    reservations = []

    def provider(request):
        wire = json.loads(request.content)
        bound = sum(len(message["content"].encode("utf-8")) for message in wire["messages"]) + 256
        assert wire["max_completion_tokens"] == 256
        with closing(sqlite3.connect(usage_db)) as db:
            ceiling, settled, lease = db.execute(
                "SELECT ceiling_micros, settled_micros, lease_until FROM yaya_agent_calls WHERE call_id='call'"
            ).fetchone()
        expected = (bound * 1_250_000 + 256 * 3_500_000 + 999_999) // 1_000_000
        assert (ceiling, settled) == (expected, None) and lease > 0
        reservations.append(ceiling)
        if fault == "timeout":
            raise httpx.ReadTimeout("synthetic deadline", request=request)
        result = {"model": "another-model" if fault == "wrong_model" else MODEL, "choices": [{
            "finish_reason": "length" if fault == "bad_body" else "stop",
            "message": {"role": "assistant", "content": '{"reply":"松鼠把信放进了信箱。","next_action":"RESPOND"}'},
        }]}
        if fault != "no_usage":
            result["usage"] = {"prompt_tokens": 20_000 if fault == "overrun" else 56}
            if fault != "partial_usage":
                result["usage"]["completion_tokens"] = 0 if fault == "overrun" else 13
        return httpx.Response(200, json=result)

    async def connected():
        return False

    async def run():
        async with ModelGateway(model, transport=httpx.MockTransport(provider)) as gateway:
            agent = YayaAgent(settings, gateway)
            context = Context("松鼠把什么放进去了？", (), {"currentQuotes": ["松鼠把信放进信箱。"]},
                              allow_provider=True, child_profile_id=3)
            result = await agent.turn(context, request_id="call", guardian=1, session=2, disconnected=connected)
            if fault == "overrun":
                denied = await agent.turn(context, request_id="next", guardian=1, session=2, disconnected=connected)
                assert denied.reason == "BUDGET_LIMIT"
            return result

    assert asyncio.run(run()).reason == expected_reason
    assert len(reservations) == 1
    with closing(sqlite3.connect(usage_db)) as db:
        assert db.execute("SELECT ceiling_micros, settled_micros, lease_until FROM yaya_agent_calls").fetchall() == [
            (reservations[0], expected_actual, 0),
        ]


@pytest.mark.parametrize(("reason", "prior_call"), [
    ("BUDGET_LIMIT", None),
    ("CONTEXT_LIMIT", None),
    ("SESSION_CALL_LIMIT", "session"),
    ("GUARDIAN_CALL_LIMIT", "guardian"),
    ("AGENT_BUSY", "session-in-flight"),
    ("AGENT_BUSY", "other-in-flight"),
])
def test_denied_reservations_keep_their_reason_and_do_not_dispatch(usage_db, reason, prior_call):
    model = Settings(model_api_url="https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
                     model_api_key="synthetic-key", model_name=MODEL)
    settings = YayaSettings(
        mode="allowlist", guardians=frozenset({1}), model=model, ledger_secret="x" * 32,
        model_input_profiles=frozenset({(1, 3)}), daily_micros=1 if reason == "BUDGET_LIMIT" else 1_000_000,
        guardian_calls=1 if prior_call == "guardian" else 40,
        session_calls=1 if prior_call in {"session", "guardian"} else 20,
        concurrency=1 if prior_call == "other-in-flight" else 6,
    )
    if prior_call:
        guardian, session = {
            "session": (1, 2), "guardian": (1, 3),
            "session-in-flight": (1, 2), "other-in-flight": (2, 3),
        }[prior_call]
        assert ledger.reserve(settings, "prior", guardian, session, 1)
        if prior_call in {"session", "guardian"}:
            ledger.settle("prior", 1)

    async def connected():
        return False

    async def run():
        async with ModelGateway(model, transport=httpx.MockTransport(
            lambda _: pytest.fail("provider dispatched without an approved reservation")
        )) as gateway:
            material = {"currentQuotes": ["字" * 9000 if reason == "CONTEXT_LIMIT" else "小熊戴着蓝色帽子。"]}
            return await YayaAgent(settings, gateway).turn(
                Context("帽子是什么颜色？", (), material, allow_provider=True, child_profile_id=3),
                request_id="denied", guardian=1, session=2, disconnected=connected,
            )

    outcome = asyncio.run(run())
    assert outcome.public()["reason"] == question_result(outcome).strategyType == reason
    with closing(sqlite3.connect(usage_db)) as db:
        assert db.execute("SELECT call_id FROM yaya_agent_calls").fetchall() == ([("prior",)] if prior_call else [])


@pytest.mark.parametrize(("daily", "concurrency"), [(100, 6), (1000, 1)])
def test_parallel_reservation_is_atomic(usage_db, daily, concurrency):
    settings = YayaSettings(daily_micros=daily, concurrency=concurrency)
    barrier = Barrier(4)

    def book(i):
        barrier.wait(timeout=3)
        return ledger.reserve(settings, f"call-{i}", i, i, 60)

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(book, range(1, 5))) == 1
    with closing(sqlite3.connect(usage_db)) as db:
        assert db.execute("SELECT COUNT(*), SUM(ceiling_micros) FROM yaya_agent_calls").fetchone() == (1, 60)


def test_settlement_changes_available_budget_but_keeps_call_counts(usage_db):
    settings = YayaSettings(daily_micros=100, guardian_calls=2, session_calls=1)
    assert ledger.reserve(settings, "first", 1, 1, 80)
    assert not ledger.reserve(settings, "before", 2, 2, 70)
    ledger.settle("first", 20)
    ledger.settle("first", 90)
    assert not ledger.reserve(settings, "same-session", 1, 1, 70)
    assert ledger.reserve(settings, "after", 1, 2, 70)
    ledger.settle("after", None)
    assert not ledger.reserve(settings, "over-guardian-calls", 1, 3, 1)
    with closing(sqlite3.connect(usage_db)) as db:
        assert db.execute("SELECT settled_micros FROM yaya_agent_calls WHERE call_id='first'").fetchone() == (20,)


def test_utc_budget_resets_and_late_usage_stays_on_original_day(usage_db, monkeypatch):
    settings = YayaSettings(daily_micros=100, guardian_calls=1, session_calls=1)
    now = [datetime(2026, 11, 27, 23, 59, 58, tzinfo=timezone.utc).timestamp()]
    monkeypatch.setattr(ledger, "time", SimpleNamespace(time=lambda: now[0]))
    assert ledger.reserve(settings, "previous", 1, 1, 100)
    assert not ledger.reserve(settings, "same-day", 2, 2, 1)
    now[0] += 3
    assert ledger.reserve(settings, "new", 2, 2, 90)
    ledger.settle("previous", 50)
    assert ledger.reserve(settings, "remaining", 1, 1, 10)
    with closing(sqlite3.connect(usage_db)) as db:
        assert db.execute("SELECT day, settled_micros FROM yaya_agent_calls WHERE call_id='previous'").fetchone() == (
            "2026-11-27", 50,
        )


def test_budget_warning_reaches_log_receiver_once_per_utc_day(usage_db, monkeypatch, caplog):
    settings = YayaSettings(daily_micros=100)
    now = [datetime(2026, 11, 27, 10, tzinfo=timezone.utc).timestamp()]
    monkeypatch.setattr(ledger, "time", SimpleNamespace(time=lambda: now[0]))
    assert ledger.reserve(settings, "below", 1, 1, 79)
    ledger.settle("below", 79)
    assert not caplog.records
    assert ledger.reserve(settings, "threshold", 1, 1, 1)
    ledger.settle("threshold", None)
    assert ledger.reserve(settings, "cap", 1, 1, 20)
    assert not ledger.reserve(settings, "blocked", 2, 2, 1)
    now[0] += 86400
    assert ledger.reserve(settings, "new-day", 2, 2, 80)
    assert [record.message for record in caplog.records] == [
        "yaya outcome=BUDGET_NEAR_LIMIT day=2026-11-27 committed_micros=80 daily_micros=100",
        "yaya outcome=BUDGET_NEAR_LIMIT day=2026-11-28 committed_micros=80 daily_micros=100",
    ]


def test_old_ledger_migration_preserves_unsettled_reservations(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    monkeypatch.setenv("ZHIYA_DB_PATH", str(path))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE yaya_agent_calls (
            call_id TEXT PRIMARY KEY, day TEXT, guardian_tag TEXT, session_tag TEXT, ceiling_micros INTEGER,
            lease_until INTEGER)""")
        db.execute("INSERT INTO yaya_agent_calls VALUES ('old','2026-11-27','g','s',75,0)")
    ledger.install()
    ledger.install()
    now = [datetime(2026, 11, 27, tzinfo=timezone.utc).timestamp()]
    monkeypatch.setattr(ledger, "time", SimpleNamespace(time=lambda: now[0]))
    assert not ledger.reserve(YayaSettings(daily_micros=100), "new", 2, 2, 1)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT ceiling_micros, settled_micros, priced FROM yaya_agent_calls").fetchone() == (75, None, 0)
    now[0] += 86400
    assert ledger.reserve(YayaSettings(daily_micros=100), "next-day", 2, 2, 100)
