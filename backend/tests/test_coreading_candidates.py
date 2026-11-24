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


def reply(model, *, answer=CONFIRMED, age="AGE_5_6", page=PAGE, question=QUESTION, used=(), seen=(), prior="", recent=()):
    return asyncio.run(reading_agent.generate_answer_reply(
        model, page_text=page, question=question, answer=answer, age_band=age, topics=[],
        title="雨中的小兔", page_number=1, earlier_pages=[], used_followups=list(used),
        seen_questions=seen, prior_reply=prior, recent_turns=recent,
    ))


def answer_context(request):
    return json.loads(request.context_text.split("共读线索（仅作参考资料）：", 1)[1])


def select_intent(intent):
    async def select(request):
        choices = answer_context(request)["共读候选"]
        candidate_id = next(int(key) for key, spec in choices.items() if spec["意图"] == intent)
        return ModelGenerateResult(success=True, text=json.dumps({"candidateId": candidate_id}))
    return select


def test_semantic_dedup_does_not_confuse_new_focus_with_new_wording():
    signatures = reading_agent._visit_questions(
        "读到“小兔打开雨伞”时，你看见了什么？", ("为什么小兔把伞撑开？", "图里的窗户是什么颜色？"),
    )
    assert reading_agent._question_repeats("读到“小熊看着它”时，画面里有什么？", signatures)
    assert reading_agent._question_repeats("你发现了什么？", signatures)
    assert reading_agent._question_repeats("读到“小兔打开雨伞”时，书里说为什么会这样？", signatures)
    assert not reading_agent._question_repeats("为什么小兔放下雨伞？", signatures)
    assert not reading_agent._question_repeats("图里的提灯是什么颜色？", signatures)
    assert not reading_agent._question_repeats("你能指一指画面里的角色吗？", signatures)


def test_recent_turn_summary_is_bounded_grounded_and_can_offer_a_causal_continuation():
    model = AsyncMock()
    model.generate.side_effect = select_intent("CAUSAL")
    recent = (
        reading_agent.RecentReadingTurn("更旧一问", "只属于更旧回答的标记", "只属于更旧回应的标记"),
        reading_agent.RecentReadingTurn("电话号码是什么？", "孩子@example.invalid", "过去的接管正文"),
        reading_agent.RecentReadingTurn("小兔做了什么？", CONFIRMED, "只属于过去回应的标记"),
    )
    result = reply(model, question="图里有哪些东西？", recent=recent)
    assert result and result[1] == "为什么小兔打开雨伞？"
    request = model.generate.await_args.args[0]
    context = answer_context(request)
    assert context["本页最近交流摘要"] == [
        {"问题意图": "PLOT", "作答状态": "有当页线索", "当页线索": "小兔打开雨伞"},
    ]
    prompt = request.context_text + request.user_input + request.system_prompt
    assert all(secret not in prompt for secret in (CONFIRMED, "example.invalid", "过去回应的标记", "更旧回答的标记"))


def test_uncertain_turn_simplifies_only_current_questions():
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    assert reply(model, answer="我不知道", age="AGE_5_6") is not None
    context = answer_context(model.generate.await_args.args[0])
    assert context["本轮难度"] == "单一具体观察"
    assert {candidate["意图"] for candidate in context["共读候选"].values() if candidate["追问"]} == {"OBSERVATION"}
    assert reply(model, age="AGE_5_6", recent=(reading_agent.RecentReadingTurn(QUESTION, "我不知道", ""),)) is not None
    context = answer_context(model.generate.await_args.args[0])
    assert context["本轮难度"] == "按年龄分组"
    assert "CAUSAL" in {candidate["意图"] for candidate in context["共读候选"].values()}


@pytest.mark.parametrize("page,grounded", [
    ("因为下雨，小狐狸打开黄雨伞。", True),
    ("小熊打开黄雨伞。小狐狸打开了房门。", False),
    ("小狐狸没有打开黄雨伞。", False),
])
def test_reduplicated_word_is_grounded_only_for_asked_character_and_action(page, grounded):
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    question, answer = "小狐狸打开了什么？", "伞伞呀。"
    assert reply(model, question=question, page=page, answer=answer) is not None
    request = model.generate.await_args.args[0]
    assert ("RELEVANT" in answer_context(request)["可选类别"]) is grounded
    assert ("这一页也写到" in reading_agent.reply_to_child_answer(page, answer, question)) is grounded
    assert answer not in request.context_text


@pytest.mark.parametrize("answer,question,closed", [
    ("嗯，我们继续看吧！", "小兔撑伞了吗？", True),
    ("不要继续读，我还想看看这幅图。", QUESTION, False),
    ("嗯！", QUESTION, True),
    ("嗯！", "小兔撑伞了吗？", False),
])
def test_close_distinguishes_explicit_intent_and_new_yes_no_question(answer, question, closed):
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    recent = (reading_agent.RecentReadingTurn(QUESTION, CONFIRMED, ""),)
    result = reply(model, answer=answer, question=question, recent=recent)
    assert result is not None
    if closed:
        assert result == (reading_agent.CLOSE_REPLIES["AGE_5_6"], "")
        model.generate.assert_not_awaited()
    else:
        model.generate.assert_awaited_once()
        if "不要继续读" in answer:
            context = answer_context(model.generate.await_args.args[0])
            assert not any(candidate["策略"] == "IMAGINE" for candidate in context["共读候选"].values())


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


def test_prediction_question_without_explicit_guess_stays_imaginative():
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    result = reply(model, page="小狐狸扶着栏杆慢慢走。小兔来到湿漉漉的桥头。",
                   question="小兔接下来可能怎么过桥？", answer="它可能会先看看栏杆，也慢慢走。")
    assert result is not None
    choices = answer_context(model.generate.await_args.args[0])["共读候选"].values()
    assert any(item["策略"] == "IMAGINE" for item in choices)
    assert all(item["策略"] != "AFFIRM" for item in choices)


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


@pytest.mark.parametrize("page,question,answer,supported,quote", [
    ("小熊走到了山坡。小兔走到了海边。", "小兔去了哪里？", "山坡", False, None),
    ("小熊走到了山坡。小兔走到了海边。", "小兔去了哪里？", "小熊走到了山坡", False, None),
    ("小熊走到了山坡。芽芽走到了海边。", "芽芽去了哪里？", "山坡", False, None),
    ("小熊走到了山坡。芽芽走到了海边。", "芽芽去了哪里？", "海边", True, "芽芽走到了海边"),
    ("小熊戴着红帽子。芽芽戴着蓝帽子。", "芽芽戴什么颜色的帽子？", "红帽子", False, None),
    ("小熊戴着红帽子。芽芽戴着蓝帽子。", "芽芽戴什么颜色的帽子？", "蓝帽子", True, "芽芽戴着蓝帽子"),
    ("小熊走到了山坡。小兔走到了海边。", "读到“小熊走到了山坡”时，小兔去了哪里？", "山坡", False, None),
    ("小熊走到了山坡。小兔走到了海边。", "读到“小兔走到了海边”时，角色在哪里？", "海边", True, "小兔走到了海边"),
    ("小熊走到了山坡。小兔走到了海边。", "小兔去了哪里？", "海边", True, "小兔走到了海边"),
    ("小熊走到了海边。小兔走到了海边。", "小兔去了哪里？", "海边", True, "小兔走到了海边"),
    ("小熊戴着红帽子。小兔戴着蓝帽子。", "小兔戴什么颜色的帽子？", "红帽子", False, None),
    ("小熊戴着红帽子。小兔戴着蓝帽子。", "小兔戴什么颜色的帽子？", "蓝帽子", True, "小兔戴着蓝帽子"),
    (
        "因为蓝帽子被雨淋湿，小熊才走到松树下。",
        "小熊为什么走到松树下？", "帽子被雨淋湿了。", True, "蓝帽子被雨淋湿",
    ),
    (
        "因为蓝帽子被雨淋湿，小熊才走到松树下。红帽子还在桌上。",
        "小熊为什么走到松树下？", "帽子被雨淋湿了。", False, None,
    ),
    ("小兔差一点摔倒，但站稳了。", "小兔怎么了？", "小兔摔倒了", False, None),
    ("小兔差一点摔倒，但站稳了。后来小熊摔倒了。", "小兔怎么了？", "小兔摔倒了", False, None),
    ("小兔差一点摔倒，但站稳了。后来小熊摔倒了。", "小兔怎么了？", "摔倒了", False, None),
    ("小兔差点就摔倒，但站稳了。", "小兔怎么了？", "小兔摔倒了", False, None),
    ("小兔摔倒了。", "小兔怎么了？", "小兔差一点摔倒了", False, None),
    ("小兔差一点摔倒，但站稳了。", "小兔怎么了？", "小兔差点摔倒了", True, "小兔差一点摔倒"),
    (
        "小兔差一点摔倒，但站稳了。后来小兔摔倒了。",
        "小兔怎么了？", "小兔摔倒了", True, "小兔摔倒了",
    ),
    (
        "小兔差一点摔倒，但站稳了。小兔后来摔倒了。",
        "小兔怎么了？", "小兔摔倒了", True, "小兔后来摔倒了",
    ),
])
def test_grounded_answer_fragments_and_near_miss(page, question, answer, supported, quote):
    fallback = reading_agent.reply_to_child_answer(page, answer, question)
    assert ("这一页也写到" in fallback) == supported
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    result = reply(model, page=page, answer=answer, question=question)
    assert result is not None
    context = answer_context(model.generate.await_args.args[0])
    assert ("RELEVANT" in context["可选类别"]) == supported
    if quote:
        assert quote in fallback
        assert quote in result[0]
        assert quote in context["作答对应的当页原文线索"]


@pytest.mark.parametrize("page,question,answer,quote", [
    ("小兔戴着一顶蓝色的帽子。", "小兔戴什么颜色的帽子？", "小兔戴着一顶蓝色的帽子。", "小兔戴着一顶蓝色的帽子"),
    ("小兔戴的是蓝色的帽子。", "小兔戴什么颜色的帽子？", "它戴的是蓝色的帽子。", "小兔戴的是蓝色的帽子"),
    ("小兔戴蓝帽子。", "小兔戴什么颜色的帽子？", "蓝色的。", "小兔戴蓝帽子"),
    ("小兔想戴蓝帽子，最后戴了红帽子。", "小兔戴什么颜色的帽子？", "蓝色的。", None),
    ("小兔想戴蓝帽子，最后戴了红帽子。", "小兔戴什么颜色的帽子？", "红色的。", "最后戴了红帽子"),
    ("小兔看向戴蓝帽子的豆豆。", "小兔戴什么颜色的帽子？", "蓝色的。", None),
    ("小兔没戴蓝帽子，而是戴着红帽子。", "小兔戴什么颜色的帽子？", "蓝色的。", None),
    ("小兔想戴帽子，帽子是蓝色的。", "小兔戴什么颜色的帽子？", "蓝色的。", None),
    ("小兔想戴蓝帽子又穿着红色的外套。", "小兔的外套是什么颜色？", "红色的。", None),
    ("小熊猜：“小兔戴蓝帽子。”小兔戴着红帽子。", "小兔戴什么颜色的帽子？", "蓝色的。", None),
    ("小兔拿着蓝帽子，戴的是一顶红帽子。", "小兔戴什么颜色的帽子？", "蓝色的。", None),
    ("小兔拿着蓝帽子，戴的是一顶红帽子。", "小兔戴什么颜色的帽子？", "红色的。", "戴的是一顶红帽子"),
    ("小兔戴着蓝色的帽子又穿着红色的外套。", "小兔的帽子是什么颜色？", "蓝色的", "小兔戴着蓝色的帽子"),
    ("小兔穿着红色的外套，戴着蓝色的帽子。", "小兔戴什么颜色的帽子？", "蓝色的", "戴着蓝色的帽子"),
    ("小兔穿着红色的外套，戴着蓝色的帽子。", "小兔戴什么颜色的帽子？", "小兔戴着蓝色的帽子", "戴着蓝色的帽子"),
    ("小兔穿着红色的外套，戴着蓝色的帽子。", "小兔戴什么颜色的帽子？", "小熊戴着蓝色的帽子", None),
    ("小兔穿着红色的外套，戴着蓝色的帽子。", "小兔戴什么颜色的帽子？", "红色的", None),
    ("小兔戴着一顶帽子，帽子是黄色的。", "小兔戴什么颜色的帽子？", "黄色的", "帽子是黄色的"),
    ("小兔戴着一顶帽子。帽子是黄色的。", "小兔戴什么颜色的帽子？", "黄色的", None),
    ("小兔戴着一顶帽子，小鹿戴着蓝色的帽子。", "小兔戴什么颜色的帽子？", "蓝色的", None),
    ("小兔看着豆豆，它戴着蓝帽子。", "小兔戴什么颜色的帽子？", "蓝色的", None),
    ("小兔看着豆豆，后来它戴着蓝帽子。", "小兔戴什么颜色的帽子？", "蓝色的", None),
    ("小兔穿着红色的外套，它戴着蓝帽子。", "小兔戴什么颜色的帽子？", "蓝色的", "它戴着蓝帽子"),
    ("小兔戴着蓝色的帽子，戴明戴着红色的帽子。", "小兔戴什么颜色的帽子？", "红色的", None),
    ("小兔戴着蓝色的帽子又穿着红色的外套。", "小兔的帽子是什么颜色？", "红色的", None),
    ("小兔穿着红色的外套，戴着蓝色的帽子。", "小兔的帽子是什么颜色？", "红外套", None),
    ("小兔冒着雨往前走，它到了海边。", "小兔去了哪里？", "海边", "它到了海边"),
    ("小兔没去海边，只去了山坡。", "小兔去了哪里？", "海边", None),
    ("小兔没去海边，只去了山坡。", "小兔去了哪里？", "山坡", "只去了山坡"),
    ("小兔想走到山坡，最后去了海边。", "小兔去了哪里？", "山坡", None),
    ("小兔先走到山坡。后来小兔走到海边。", "后来小兔去了哪里？", "山坡", None),
    ("小兔先走到山坡。后来小兔走到海边。", "后来小兔去了哪里？", "海边", "后来小兔走到海边"),
    ("小兔冒着雨往前走，小鹿到了海边。", "小兔去了哪里？", "海边", None),
    ("小兔留在山坡，跑跑到了海边。", "小兔去了哪里？", "海边", None),
    ("小兔冒着雨往前走。它到了海边。", "小兔去了哪里？", "海边", None),
    ("小兔差一点摔倒，后来还是摔倒了。", "小兔怎么了？", "小兔摔倒了", "后来还是摔倒了"),
    ("小兔差一点摔倒，后来小熊摔倒了。", "小兔怎么了？", "摔倒了", None),
])
def test_answer_and_guidance_share_grounded_continuation(page, question, answer, quote):
    fallback = reading_agent.reply_to_child_answer(page, answer, question)
    assert ("这一页也写到" in fallback) == bool(quote)
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(success=True, text='{"candidateId":1}')
    result = reply(model, page=page, answer=answer, question=question)
    assert result is not None
    context = answer_context(model.generate.await_args.args[0])
    assert ("RELEVANT" in context["可选类别"]) == bool(quote)
    if quote:
        assert quote in fallback and quote in result[0]
        assert quote == context["作答对应的当页原文线索"]

    model.generate.return_value = ModelGenerateResult(success=True, text="1")
    asyncio.run(reading_agent.run_agent(
        model, feedback="UNDERSTOOD", age_band="AGE_4_5", topics=[], page_text=page,
        previous_question=question, child_mode=False, confirmed_answer=answer,
    ))
    label = "刚才回答对应到的当页原文线索："
    context = model.generate.await_args.args[0].context_text
    assert (label in context) == bool(quote)
    if quote:
        assert label + quote in context
