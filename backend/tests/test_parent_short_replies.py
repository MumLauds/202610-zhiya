"""Safe short answers in the actual parent co-reading path (no live model)."""

import asyncio
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock

import pytest

from app.schemas.agent import KnowledgeRetrievalResult, ModelGenerateResult
from app.services import reading_agent as agent
from test_interactions import assert_saved_answer, env, guidance, guidance_knowledge, guidance_model, started_session

EXACT_ROLE_QUESTION = "读到“妈妈说”时，你能指一指画面里的角色吗？"
ROLE_QUESTION = "读到“小兔打开雨伞”时，角色在做什么？"
PAGE = "小兔打开雨伞。小熊看着它。"


def generate(model, *, page=PAGE, question=ROLE_QUESTION, answer="猫猫", recent=()):
    return asyncio.run(agent.generate_answer_reply(
        model, page_text=page, question=question, answer=answer, age_band="AGE_3_4", topics=[],
        title="合成绘本", page_number=1, earlier_pages=[], used_followups=[], recent_turns=recent,
    ))


@pytest.mark.parametrize("question,answer,echo,guide", [
    (ROLE_QUESTION, "猫猫", "小猫", "角色"),
    (EXACT_ROLE_QUESTION, "猫猫。", "小猫", "角色"),
    ("小兔把伞给了谁？", "给小熊", "小熊", "谁"),
    ("小兔为什么打开伞？", "因为下雨", "下雨", "原因"),
    (ROLE_QUESTION, "可能吧", "没关系", "线索"),
    (ROLE_QUESTION, "我想让雨伞变成小船", "想象", "故事"),
])
def test_safe_short_reply_respects_context_without_fact_or_model_call(question, answer, echo, guide):
    model = AsyncMock()
    local = agent.reply_to_child_answer(PAGE, answer, question, age_band="AGE_3_4")
    assert echo in local and guide in local
    assert all(word not in local for word in ("这一页也写到", "答对", "我看到", "图里有", "？"))
    assert generate(model, question=question, answer=answer) == (local, "")
    model.generate.assert_not_awaited()


@pytest.mark.parametrize("answer,opening,forbidden", [
    ("它想帮助……", "你提到了帮忙", "你猜"),
    ("它想帮助小熊。", "你说它想帮小熊", "你猜"),
    ("我觉得它想帮助小熊。", "你觉得它想帮小熊", "你猜"),
    ("我猜它可能想帮助小熊。", "你猜它可能想帮小熊", "书里写着"),
    ("如果它帮助小熊，会怎么样？", "你问的是如果它帮小熊", "已经"),
    ("它想帮助小熊？", "你在问它想不想帮小熊", "你猜"),
    ("帮帮小熊", "你在提议帮小熊", "你猜"),
])
def test_short_help_ack_does_not_invent_or_overwrite_modality(answer, opening, forbidden):
    question = "它为什么这样做？"
    model = AsyncMock()
    local = agent.reply_to_child_answer(PAGE, answer, question)
    assert local.startswith(opening) and forbidden not in local
    assert "这一页也写到" not in local
    assert generate(model, question=question, answer=answer) == (local, "")
    model.generate.assert_not_awaited()


@pytest.mark.parametrize("question,answer,cue", [
    (EXACT_ROLE_QUESTION, "小猫咪", "小猫"),
    (EXACT_ROLE_QUESTION, "毛茸茸的", "毛茸茸"),
    (EXACT_ROLE_QUESTION, "那只橘色的", "橘色"),
    (ROLE_QUESTION, "它在找朋友", "找朋友"),
    ("小狐狸现在是什么心情？", "小狐狸害怕", "害怕"),
])
def test_oral_cues_are_locally_acknowledged_without_inventing_observations(question, answer, cue):
    model = AsyncMock()
    local = agent.reply_to_child_answer(PAGE, answer, question)
    assert cue in local
    assert all(word not in local for word in ("它就在", "答对", "我看到", "图里有", "这一页也写到"))
    assert generate(model, question=question, answer=answer) == (local, "")
    model.generate.assert_not_awaited()


def test_negated_story_mention_is_not_promoted_to_evidence_for_oral_alias(monkeypatch):
    monkeypatch.setattr(agent, "retrieve_knowledge", lambda *_: KnowledgeRetrievalResult(items=[], has_results=False))
    page = "妈妈说这里没有小猫咪。"
    answer = "小猫咪"
    local = agent.reply_to_child_answer(page, answer, EXACT_ROLE_QUESTION)
    assert "小猫" in local and "这一页也写到" not in local
    model = AsyncMock()
    assert generate(model, page=page, question=EXACT_ROLE_QUESTION, answer=answer) == (local, "")
    model.generate.assert_not_awaited()

    model.generate.return_value = ModelGenerateResult(success=True, text="1")
    asyncio.run(agent.run_agent(
        model, feedback="UNDERSTOOD", age_band="AGE_3_4", topics=[], page_text=page,
        previous_question=EXACT_ROLE_QUESTION, child_mode=False, confirmed_answer=answer,
        recent_turns=[agent.RecentReadingTurn(EXACT_ROLE_QUESTION, answer, local)],
    ))
    request = model.generate.await_args.args[0]
    assert "刚才回答对应到的当页原文线索" not in request.context_text and local not in request.context_text


def test_unsafe_oral_input_uses_handoff_before_local_acknowledgement():
    answer = "小猫咪，我的手机号是13800000000"
    model = AsyncMock()
    assert agent.reply_to_child_answer(PAGE, answer, EXACT_ROLE_QUESTION) == agent.HANDOFF
    assert generate(model, question=EXACT_ROLE_QUESTION, answer=answer) is None
    model.generate.assert_not_awaited()


@pytest.mark.parametrize("answer,label,can_plan", [
    ("雨伞", "雨伞", True), ("小熊", "小熊", True), ("熊熊呀。", "小熊", False), ("伞伞呀。", "雨伞", False),
])
def test_noun_is_acknowledged_without_affirming_the_action(monkeypatch, answer, label, can_plan):
    monkeypatch.setattr(agent, "retrieve_knowledge", lambda *_: KnowledgeRetrievalResult(items=[], has_results=False))
    local = agent.reply_to_child_answer(PAGE, answer, ROLE_QUESTION)
    assert label in local and "角色做了什么" in local and "这一页也写到" not in local
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"answerCategory":"PARTIAL","followup":0}')
    result = generate(model, answer=answer)
    assert result is not None and result[0] == local and not result[1]
    if not can_plan:
        model.generate.assert_not_awaited()
        return
    request = model.generate.await_args.args[0]
    assert local not in request.context_text and '"RELEVANT"' not in request.context_text
    if answer not in PAGE:
        assert answer not in request.context_text


def test_explicitly_supported_short_recipient_still_keeps_its_evidence():
    text = agent.reply_to_child_answer("小兔把雨伞递给小熊。", "给小熊", "小兔把伞给了谁？")
    assert "小兔把雨伞递给小熊" in text and "再看看书里有没有说交给了谁" not in text


@pytest.mark.parametrize("outcome", [
    ModelGenerateResult(success=False, error_code="TIMEOUT"),
    ModelGenerateResult(success=True, text="{"),
])
def test_unsupported_free_text_is_not_echoed_and_normal_model_fallback_is_preserved(monkeypatch, outcome):
    monkeypatch.setattr(agent, "retrieve_knowledge", lambda *_: KnowledgeRetrievalResult(items=[], has_results=False))
    unknown = "穿梭舰QZ标记"
    local = agent.reply_to_child_answer(PAGE, unknown, ROLE_QUESTION)
    assert unknown not in local and "这一页也写到" not in local
    model = AsyncMock()
    model.generate.return_value = outcome
    assert generate(model, answer=unknown) is None  # API-015 preserves its already-saved local reply.
    assert unknown not in model.generate.await_args.args[0].context_text
    model.generate.assert_awaited_once()


@pytest.mark.parametrize("question,answer,ack", [
    (ROLE_QUESTION, "猫猫", "小猫"),
    (EXACT_ROLE_QUESTION, "猫猫", "小猫"),
    (EXACT_ROLE_QUESTION, "毛茸茸的", "毛茸茸"),
    ("它为什么这样做？", "我觉得它想帮助小熊。", "你觉得它想帮小熊"),
])
def test_parent_api_short_reply_is_saved_replayed_and_not_passed_as_guidance_evidence(
    started_session, guidance_model, question, answer, ack,
):
    client, headers, path, sid = started_session
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE story_pages SET body_text=? WHERE version_id=11 AND page_no=1", (PAGE,))
        db.execute("""INSERT INTO interaction_events
            (session_id, version_id, request_key, request_digest, response_json, page_no, event_type,
             output_text, result_status, revision_after, created_at)
            VALUES (?, 11, 'short-answer-initial', 'd', '{}', 1, 'QUESTION', ?, 'SUCCESS', 1, 1)""",
                   (sid, question))
    url = f"/api/v1/reading-sessions/{sid}/events"
    payload = dict(eventType="ANSWER", pageNumber=1, expectedRevision=1, clientEventId="cat-short", inputText=answer)
    first = client.post(url, headers=headers, json=payload)
    assert first.status_code == 201
    receipt = first.json()["data"]
    assert ack in receipt["replyText"] and "图里有" not in receipt["replyText"]
    assert receipt["revision"] == 2 and "followupQuestion" not in receipt
    assert_saved_answer(client, path, sid, "cat-short", receipt)
    assert client.post(url, headers=headers, json=payload).json()["data"] == receipt
    stale = client.post(url, headers=headers, json={**payload, "clientEventId": "stale-other-answer"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "SESSION_CONFLICT"
    guidance_model.assert_not_awaited()

    guidance_model.return_value = ModelGenerateResult(success=True, text="1")
    result = guidance(client, headers, sid, "after-short", feedback="UNDERSTOOD", revision=2)
    assert result.status_code == 201
    sent = guidance_model.await_args.args[0].context_text
    assert all(text not in sent for text in (answer, ack, receipt["replyText"], "刚才回答对应到的当页原文线索"))
    guidance_model.assert_awaited_once()


def test_locally_acknowledged_short_answer_does_not_seed_future_model_prompt(monkeypatch):
    monkeypatch.setattr(agent, "retrieve_knowledge", lambda *_: KnowledgeRetrievalResult(items=[], has_results=False))
    prior_reply = agent.reply_to_child_answer(PAGE, "猫猫", ROLE_QUESTION)
    recent = (agent.RecentReadingTurn(ROLE_QUESTION, "猫猫", prior_reply),)
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    assert generate(model, answer="我看到小兔撑开了雨伞", recent=recent) is not None
    sent = model.generate.await_args.args[0].context_text
    assert "猫猫" not in sent and "小猫" not in sent and prior_reply not in sent


def test_api_uncertain_answer_keeps_gentle_prompt_even_for_numbered_model_plan(started_session, guidance_model):
    client, headers, path, sid = started_session
    question = guidance(client, headers, sid, "short-unsure-question").json()["data"]
    assert question["outputText"]
    guidance_model.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    reply = client.post(f"/api/v1/reading-sessions/{sid}/events", headers=headers, json={
        "eventType": "ANSWER", "pageNumber": 1, "expectedRevision": question["revision"],
        "clientEventId": "short-unsure", "inputText": "不知道",
    })
    assert reply.status_code == 201 and "一起找找" in reply.json()["data"]["replyText"]
    assert "不知道" not in guidance_model.await_args.args[0].context_text
