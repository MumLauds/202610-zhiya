"""Tests for server-rendered parent co-reading candidates (no real model)."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.schemas.agent import KnowledgeItem, KnowledgeRetrievalResult, ModelGenerateResult
from app.services import reading_agent

PAGE = "因为下雨，小兔打开雨伞。小熊看着它。"
QUESTION = "小兔做了什么？"
CONFIRMED = "我注意到小兔把伞撑开了呀"


@pytest.fixture(autouse=True)
def safe_retrieval(monkeypatch):
    monkeypatch.setattr(reading_agent, "retrieve_knowledge", lambda *_: KnowledgeRetrievalResult(
        items=[], has_results=False,
    ))


def reply(model, *, answer=CONFIRMED, age="AGE_5_6", page=PAGE, question=QUESTION, used=(), seen=(), prior=""):
    return asyncio.run(reading_agent.generate_answer_reply(
        model, page_text=page, question=question, answer=answer, age_band=age, topics=[],
        title="雨中的小兔", page_number=1, earlier_pages=[], used_followups=list(used),
        seen_questions=seen, prior_reply=prior,
    ))


def answer_context(request):
    return json.loads(request.context_text.split("共读线索（仅作参考资料）：", 1)[1])


def select_intent(intent):
    async def select(request):
        choices = answer_context(request)["共读候选"]
        candidate_id = next(int(key) for key, spec in choices.items() if spec["意图"] == intent)
        return ModelGenerateResult(success=True, text=json.dumps({"candidateId": candidate_id}))
    return select


def test_answer_selects_whitelisted_compound_candidate_and_keeps_private_history_local():
    model = AsyncMock()
    model.generate.side_effect = select_intent("IMAGINATION")
    seen = ('读到「小兔打开雨伞」时，你 看到什么?', '只保留在本地的往轮标识')
    prior = "只保留在本地的上次回应标识"
    response = reply(model, question="你猜小兔接下来会做什么？", seen=seen, prior=prior)
    assert response is not None
    text, followup = response
    assert text.endswith(followup) and text.startswith("我们先把它当成一个猜想。")
    assert followup.count("？") == 1 and "可能" in followup
    request = model.generate.await_args.args[0]
    context = answer_context(request)
    assert "OBSERVATION" not in {item["意图"] for item in context["共读候选"].values()}
    assert len(context["共读候选"]) <= 8
    prompt = request.context_text + request.user_input + request.system_prompt
    assert all(value not in prompt for value in (CONFIRMED, seen[1], prior))


def test_imagination_question_does_not_relabel_a_fact_as_a_guess():
    model = AsyncMock()
    model.generate.side_effect = select_intent("IMAGINATION")
    response = reply(model)
    assert response is not None and response[1]
    assert "猜想" not in response[0]
    choices = answer_context(model.generate.await_args.args[0])["共读候选"].values()
    assert all(item["策略"] != "IMAGINE" for item in choices)


@pytest.mark.parametrize("age,intents", [
    ("AGE_3_4", {"OBSERVATION", "PLOT"}),
    ("AGE_4_5", {"OBSERVATION", "PLOT", "CAUSAL", "EMOTION"}),
    ("AGE_5_6", {"OBSERVATION", "PLOT", "CAUSAL", "EMOTION", "IMAGINATION", "LIFE_CONNECTION"}),
])
def test_answer_candidate_intents_respect_age_and_explicit_causal_source(age, intents):
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    assert reply(model, age=age) is not None
    choices = answer_context(model.generate.await_args.args[0])["共读候选"].values()
    assert {item["意图"] for item in choices if item["追问"]} == intents
    assert all(item["追问"].count("？") == 1 for item in choices if item["追问"])
    for item in choices:
        if item["意图"] == "CAUSAL":
            assert item["追问"] == "读到“小兔打开雨伞”时，书里说为什么会这样？"
    for page in ("小兔打开雨伞。", "小兔不是因为下雨才打开雨伞。"):
        assert reply(model, age=age, page=page) is not None
        choices = answer_context(model.generate.await_args.args[0])["共读候选"].values()
        assert all(item["意图"] != "CAUSAL" for item in choices)


def test_evidence_free_and_exhausted_answer_choices_never_create_a_followup():
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    for options in (
        {"page": "。？！"},
        {"used": ("你能指给我看吗？", "你还看到了什么？")},
    ):
        response = reply(model, **options)
        assert response is not None and response[1] == ""
        context = answer_context(model.generate.await_args.args[0])
        assert context["追问候选"] == {}
        assert all(not item["追问"] for item in context["共读候选"].values())


@pytest.mark.parametrize("plan", ['{"candidateId":0}', '{"candidateId":true}', '{"candidateId":"1"}',
                                   '{"candidateId":8}', '{"candidateId":1,"replyText":"额外文字"}'])
def test_unoffered_and_invalid_choices_return_to_saved_rule(plan):
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text=plan)
    assert reply(model, page="。？！") is None


def test_parent_guidance_skips_repeated_questions_without_transmitting_the_history():
    model = AsyncMock()

    async def select_new(request):
        choices = request.context_text.split("候选问句：\n", 1)[1].splitlines()
        assert all("读到“因为下雨”时，角色在做什么？" != line.partition(". ")[2] for line in choices)
        assert any("读到“小兔打开雨伞”时，角色在做什么？" == line.partition(". ")[2] for line in choices)
        assert any("读到“因为下雨”时，你看见了谁？" == line.partition(". ")[2] for line in choices)
        candidate = next(line for line in choices if "如果换种办法" in line)
        return ModelGenerateResult(success=True, text=candidate.split(". ", 1)[0])

    model.generate.side_effect = select_new
    seen = ("读到“因为下雨”时，角色刚才做了什么？", "不传给模型的旧问题标识")
    result = asyncio.run(reading_agent.run_agent(
        model, feedback="INITIAL", age_band="AGE_5_6", topics=[], page_text=PAGE, previous_question="",
        child_mode=False, seen_questions=seen,
    ))
    assert result.status == "GENERATED" and "如果换种办法" in result.outputText
    assert all("标识" not in getattr(model.generate.await_args.args[0], field)
               for field in ("context_text", "user_input", "system_prompt"))

def test_parent_guidance_can_yield_no_question_when_the_visit_exhausted_candidates():
    model = AsyncMock()
    page = "小兔打开雨伞。"
    seen = [question for group in reading_agent.question_options(page, reading_agent.QUESTION_FORMS["INITIAL"]).values()
            for question in group]
    seen.extend(question for _, question in reading_agent._intent_questions(page, None, "AGE_3_4", set()))
    result = asyncio.run(reading_agent.run_agent(
        model, feedback="INITIAL", age_band="AGE_3_4", topics=[], page_text=page, previous_question="",
        child_mode=False, seen_questions=seen,
    ))
    assert result.status == "FALLBACK" and not result.outputText
    model.generate.assert_not_awaited()


def test_parent_guidance_keeps_safe_story_and_knowledge_references(monkeypatch):
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text="1")
    items = [
        KnowledgeItem(knowledge_item_id=1, item_key="safe", content="鼓励读者观察插画。", source_title="共读手册"),
        KnowledgeItem(knowledge_item_id=2, item_key="private", content="fixture@example.invalid", source_title="测试来源"),
    ]
    monkeypatch.setattr(reading_agent, "retrieve_knowledge", lambda *_: KnowledgeRetrievalResult(
        items=items, has_results=True,
    ))
    result = asyncio.run(reading_agent.run_agent(
        model, feedback="UNDERSTOOD", age_band="AGE_3_4", topics=[],
        page_text="小兔打开雨伞。page-fixture@example.invalid。", previous_question="小兔做了什么？",
        child_mode=False, confirmed_answer="我看见小兔撑伞，child-fixture@example.invalid",
        earlier_pages=["小熊坐在窗前。earlier-fixture@example.invalid。"],
    ))
    assert result.status == "GENERATED"
    request = model.generate.await_args.args[0]
    assert "fixture" not in request.context_text + request.user_input + request.system_prompt
    assert "刚才回答对应到的当页原文线索：" not in request.context_text
    assert all(value in request.context_text for value in ("小兔打开雨伞", "小熊坐在窗前", "鼓励读者观察插画"))


@pytest.mark.parametrize("page,answer,question,supported", [
    ("这不是红色的，是黄色的。", "红色的", "是什么颜色？", False),
    ("这不是红色的，是黄色的。", "黄色的", "是什么颜色？", True),
    ("这不是红色的而是黄色的。", "红色的", "是什么颜色？", False),
    ("这不是红色的而是黄色的。", "黄色的", "是什么颜色？", True),
    ("小兔没去海边。", "去海边", "它去了哪里？", False),
    ("小兔不去海边。", "去海边", "它去了哪里？", False),
    ("小兔去海边。", "去海边", "它去了哪里？", True),
    ("小兔不去海边，改去河边。", "去河边", "它去了哪里？", True),
    ("外面下雨了。小兔打开了雨伞。", "小鸟提醒小兔下雨了", "谁说外面下雨了？", False),
    ("外面下雨了。小兔打开了雨伞。", "小兔说过下雨了", "谁说外面下雨了？", False),
    ("只有小兔打开雨伞。", "小松鼠打开雨伞", "谁打开了雨伞？", False),
    ("只有小兔打开雨伞。", "小兔子撑开了伞", "谁打开了雨伞？", True),
    ("小松鼠打开雨伞。", "小松鼠撑开了伞", "谁打开了雨伞？", True),
    ("小兔撑开雨伞，然后放下雨伞。", "小兔先打开伞，再放下伞。", "它做了什么？", True),
])
def test_fact_recognition_agrees_in_rule_and_model_choices(page, answer, question, supported):
    fallback = reading_agent.reply_to_child_answer(page, answer, question)
    assert ("这一页也写到" in fallback) == supported
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    assert reply(model, page=page, answer=answer, question=question) is not None
    context = answer_context(model.generate.await_args.args[0])
    assert ("RELEVANT" in context["可选类别"]) == supported
    assert bool({"AFFIRM", "EXTEND"} & {item["策略"] for item in context["共读候选"].values()}) == supported
    if not supported:
        model.generate.return_value = ModelGenerateResult(
            success=True, text='{"answerCategory":"RELEVANT","followup":0}',
        )
        assert reply(model, page=page, answer=answer, question=question) is None
