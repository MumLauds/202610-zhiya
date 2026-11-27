import asyncio
import json
import sqlite3
from contextlib import closing

import httpx

import pytest

from app.config import Settings
from app.db import yaya_agent as yaya_db
from app.model_gateway import ModelGateway, ModelReply
from app.services.yaya_agent import Context, YayaAgent, local_turn, project, reading_context, until_disconnect
from app.services.yaya_agent_config import MODEL, YayaSettings


def test_default_enrollment_needs_release_approval_and_separate_credentials(monkeypatch):
    monkeypatch.setenv("YAYA_AGENT2_MODE", "default")
    monkeypatch.delenv("YAYA_AGENT2_RELEASE_APPROVED", raising=False)
    with pytest.raises(ValueError, match="approved release"):
        YayaSettings.from_env()
    monkeypatch.setenv("YAYA_AGENT2_RELEASE_APPROVED", "true")
    monkeypatch.setenv("YAYA_AGENT2_STAGE", "staging")
    with pytest.raises(ValueError, match="allowlist"):
        YayaSettings.from_env()
    monkeypatch.delenv("YAYA_AGENT2_STAGE")
    monkeypatch.setenv("MODEL_API_KEY", "legacy-only")
    monkeypatch.setenv("YAYA_AGENT2_API_KEY", "legacy-only")
    with pytest.raises(ValueError, match="dedicated credential"):
        YayaSettings.from_env()
    monkeypatch.setenv("YAYA_AGENT2_API_KEY", "synthetic-agent2")
    monkeypatch.setenv("YAYA_AGENT2_API_URL",
                       "https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions")
    monkeypatch.setenv("YAYA_AGENT2_LEDGER_SECRET", "x" * 32)
    settings = YayaSettings.from_env()
    assert settings.model.model_name == MODEL == "qwen3.8-flash"
    assert settings.enrolls(123)


@pytest.mark.parametrize(("text", "state", "action", "reply"), [
    ("我觉得这片叶子是一床小被子。", None, "RESPOND", "这个想法真有意思呀。"),
    ("你先别问，让我看看它怎么转。", None, "LISTEN", "好，我陪你看。"),
    ("我还在看。", "LISTEN", "LISTEN", "嗯。"),
    ("松鼠是怕信掉了才轻轻放进去的吗？", None, "RESPOND", "有可能哦。"),
])
def test_local_positive_interaction(text, state, action, reply):
    result = local_turn(Context(text, (), {}, state))
    assert result.action == action and result.reply.startswith(reply) and result.followup is None


def test_protocol_only_projects_complete_safe_response():
    reply = "小兔把雨伞打开了。"
    draft = {"reply": reply, "next_action": "ASK_FOLLOWUP", "followup_question": "伞是什么颜色？"}
    def render():
        return ModelReply(json.dumps(draft, ensure_ascii=False))

    valid = project(render(), ())
    assert valid.followup == "伞是什么颜色？"
    only = project(render(), ("伞是什么颜色？",))
    assert (only.reply, only.followup, only.decision) == (reply, None, "REPLY_ONLY")
    assert "reply" not in only.public() and "upstream" not in only.public()
    draft["reply"] = "书上说。"
    assert project(render(), ()).reason == "MODEL_OUTPUT_INVALID"
    draft["reply"] = "你猜伞是什么颜色？"
    assert project(render(), ()).reason == "QUESTION_FORMAT"
    assert project(ModelReply('{"reply":"书上说。"}'), ()).reason == "MODEL_OUTPUT_INVALID"


@pytest.mark.parametrize(("fault", "reason"), [
    (None, None),
    ("connection", "API_CONNECTION_ERROR"),
    ("timeout", "API_TIMEOUT"),
    (401, "API_HTTP_ERROR"),
    (429, "API_HTTP_ERROR"),
    (500, "API_HTTP_ERROR"),
    ("empty", "MODEL_OUTPUT_INVALID"),
    ("invalid_json", "MODEL_OUTPUT_INVALID"),
    ("truncated", "MODEL_OUTPUT_INVALID"),
    ("wrong_model", "MODEL_OUTPUT_INVALID"),
])
def test_gateway_projection_and_daily_reservation(tmp_path, monkeypatch, fault, reason):
    db_path = tmp_path / "usage.sqlite3"
    db_path.touch()
    monkeypatch.setenv("ZHIYA_DB_PATH", str(db_path))
    yaya_db.install()
    config = Settings(model_api_url="https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
                      model_api_key="synthetic-key", model_name=MODEL)
    settings = YayaSettings(mode="allowlist", guardians=frozenset({1}), model=config,
                            ledger_secret="x" * 32, guardian_calls=1, session_calls=1)
    calls = []

    def provider(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert "synthetic-child-secret" not in request.content.decode()
        assert payload["model"] == MODEL
        assert payload["response_format"] == {"type": "json_object"}
        if fault == "connection":
            raise httpx.ConnectError("synthetic", request=request)
        if fault == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        if isinstance(fault, int):
            return httpx.Response(fault)
        content = json.dumps({"reply": "松鼠把信放进信箱。", "next_action": "RESPOND"})
        return httpx.Response(200, json={
            "model": "unexpected-model" if fault == "wrong_model" else MODEL,
            "choices": [{"finish_reason": "length" if fault == "truncated" else "stop", "message": {
                "role": "assistant", "content": {"empty": "", "invalid_json": "{"}.get(fault, content),
            }}],
            "usage": {"prompt_tokens": 56, "completion_tokens": 13},
        })

    async def connected():
        return False

    async def run():
        async with ModelGateway(config, transport=httpx.MockTransport(provider)) as gateway:
            agent = YayaAgent(settings, gateway)
            context = Context("synthetic-child-secret", (), {"currentQuotes": ["松鼠把信放进信箱。"]},
                              allow_provider=True)
            first = await agent.turn(context, request_id="first", guardian=1, session=2, disconnected=connected)
            second = await agent.turn(context, request_id="second", guardian=1, session=2, disconnected=connected)
            return first, second

    first, second = asyncio.run(run())
    assert first.reason == reason
    assert second.reason == "BUDGET_LIMIT" and len(calls) == 1
    if reason is None:
        assert first.reply == "松鼠把信放进信箱。"
        assert (first.upstream.prompt_tokens, first.upstream.completion_tokens) == (56, 13)
    with closing(sqlite3.connect(db_path)) as db:
        tag, held = db.execute("SELECT guardian_tag, lease_until FROM yaya_agent_calls").fetchone()
        assert len(tag) == 64 and held == 0


def test_disconnect_cancels_inflight_generation():
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def generation():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def disconnected():
            return started.is_set()

        assert await until_disconnect(generation, disconnected) is None
        assert cancelled.is_set()

    asyncio.run(run())


def test_evidence_is_bound_to_current_book_visited_prefix_and_session():
    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE story_pages (version_id INTEGER, page_no INTEGER, body_text TEXT);
            CREATE TABLE interaction_events (id INTEGER, session_id INTEGER, version_id INTEGER, page_no INTEGER,
                                             event_type TEXT, result_status TEXT, response_json TEXT);
            INSERT INTO story_pages VALUES (11,1,'松鼠打开信箱。'), (11,2,'松鼠轻轻放好信。'),
                                           (11,3,'纸风车开始转动。'), (12,1,'其他绘本的猫。');
            INSERT INTO interaction_events VALUES
                (1, 1, 11, 1, 'STARTED', 'SUCCESS', '{}'),
                (2, 2, 11, 3, 'PAGE_CHANGED', 'SUCCESS', '{}'),
                (3, 1, 12, 1, 'PAGE_CHANGED', 'SUCCESS', '{}'),
                (4, 1, 11, 3, 'PAGE_CHANGED', 'SUCCESS', '{}'),
                (5, 1, 11, 2, 'PAGE_CHANGED', 'SUCCESS', '{}'),
                (6, 2, 11, 2, 'ANSWER', 'SUCCESS', '{"data":{"agentReceipt":{"version":"2.0","state":"PAUSE","action":"PAUSE"}}}'),
                (7, 1, 11, 2, 'ANSWER', 'SUCCESS', '{"data":{"agentReceipt":{"version":"2.0","state":"LISTEN","action":"LISTEN"}}}');
        """)
        row = {"id": 1, "version_id": 11, "current_page_no": 2, "carried_prefix_page_count": 0, "source_type": "LIBRARY"}
        result = reading_context(db, row, "这是谁？", 5, ())
    material = json.dumps(result.material, ensure_ascii=False)
    assert "松鼠打开信箱" in material and "松鼠轻轻放好信" in material
    assert "纸风车" not in material and "其他绘本" not in material and "PAUSE" not in material
    assert result.last_state == "LISTEN"
