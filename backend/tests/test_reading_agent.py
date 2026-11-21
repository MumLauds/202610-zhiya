import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.schemas.agent import ModelGenerateResult
from app.services import reading_agent
from app.services.reading_agent import matching_answer_excerpt


def test_matching_answer_excerpt_includes_detail_late_in_clause():
    page = "在夜色渐浓的森林深处小兔沿着弯弯曲曲的小路举起提灯照亮脚下的石头。"
    excerpt = matching_answer_excerpt(page, "我看见了提灯")

    assert excerpt is not None
    quoted_text = excerpt.strip("…")
    assert "提灯" in quoted_text
    assert len(quoted_text) <= 18
    assert quoted_text in page


def test_matching_answer_excerpt_rejects_shared_boilerplate():
    page = "小兔觉得风越来越大，赶紧把窗户关上。"

    assert matching_answer_excerpt(page, "我觉得他会回家") is None


def test_matching_answer_excerpt_accepts_two_character_story_detail():
    page = "小兔觉得风越来越大，赶紧把窗户关上。"

    assert matching_answer_excerpt(page, "小兔") == "小兔觉得风越来越大"


@pytest.mark.parametrize("answer,actions,negated,evidence,wrong_order", [
    ("它先把伞撑起来啦，然后把它搁下", ["打开", "放下"], [False, False], ["有依据", "有依据"], False),
    ("它先搁下伞，然后才撑伞", ["放下", "打开"], [False, False], ["有依据", "有依据"], True),
    ("它放下雨伞之前先撑伞", ["打开", "放下"], [False, False], ["有依据", "有依据"], False),
    ("它没把伞撑起来，后来把伞放下啦", ["打开", "放下"], [True, False], ["冲突", "有依据"], False),
    ("它没有撑伞或拿起书包", ["打开", "拿起"], [True, True], ["冲突", "未证实"], False),
    ("它把伞收起来啦，然后放下雨伞", ["收起", "放下"], [False, False], ["冲突", "有依据"], False),
    ("小熊撑伞", ["打开"], [False], ["未证实"], False),
    ("小兔打开电脑", ["打开"], [False], ["未证实"], False),
    ("它关上窗户", ["收起"], [False], ["未证实"], False),
    ("它可能撑伞", ["打开"], [False], ["未证实"], False),
])
def test_scoped_actions_in_child_speech(answer, actions, negated, evidence, wrong_order):
    signals = reading_agent._answer_signals(answer, "小兔先打开雨伞，然后放下雨伞。", "小兔做了什么？")
    assert [item.verb for item in signals.actions] == actions
    assert [item.negated for item in signals.actions] == negated
    assert signals.evidence == evidence
    assert signals.order_conflict is wrong_order
    assert signals.supports_page_reply is (not wrong_order and all(item == "有依据" for item in evidence))


@pytest.mark.parametrize("page,answer,grounded", [
    ("因为天在下雨，小兔打开雨伞。", "因为外面下雨呀，所以它把伞撑起来啦", True),
    ("因为天在下雨，小兔打开雨伞。", "因为下雨呀", True),
    ("外面下雨，所以小兔打开雨伞。", "它撑开伞是因为下雨啦", True),
    ("因为天在下雨，小兔打开雨伞。", "它撑开伞是因为天太热", False),
    ("天在下雨。小兔打开雨伞。", "因为下雨所以它撑开雨伞", False),
    ("因为天在下雨，小兔打开雨伞。", "因为我猜会下雨，所以它撑开伞", False),
])
def test_cause_requires_the_same_relation_in_page_text(page, answer, grounded):
    signals = reading_agent._answer_signals(answer, page, "小兔为什么打开雨伞？")
    assert signals.causal == ("有当页因果依据" if grounded else "待核对")
    assert signals.supports_page_reply is grounded


def test_model_receives_only_bounded_signals_and_cannot_endorse_unsupported_cause():
    answer = "它先把伞撑起来，然后放下了，因为QZ_PRIVATE_CAUSE"
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(
        success=True, text='{"answerCategory":"RELEVANT","followup":0}',
    )
    result = asyncio.run(reading_agent.generate_answer_reply(
        model, page_text="小兔打开雨伞，然后放下雨伞。", question="小兔为什么放下雨伞？",
        answer=answer, age_band="AGE_3_4", topics=[], title="雨中的小兔", page_number=1,
        earlier_pages=[], used_followups=[],
    ))
    request = model.generate.await_args.args[0]
    context = json.loads(request.context_text.split("共读线索（仅作参考资料）：", 1)[1])
    assert "QZ_PRIVATE_CAUSE" not in request.context_text + request.system_prompt + request.user_input
    assert [item["动作"] for item in context["本地语义线索"]["动作顺序"]] == ["打开", "放下"]
    assert context["本地语义线索"]["因果"] == "待核对"
    assert context["可选类别"] == ["INSUFFICIENT_EVIDENCE"]
    assert result is None


@pytest.mark.parametrize("answer", ["小兔飞上天", "小兔打开雨伞，又飞上天"])
def test_shared_subject_or_one_supported_action_does_not_verify_unknown_detail(answer):
    page = "小兔打开雨伞。"
    signals = reading_agent._answer_signals(answer, page, "小兔做了什么？")
    assert matching_answer_excerpt(page, answer)
    assert signals.unverified_detail and not signals.supports_page_reply


@pytest.mark.parametrize("page,answer,actions,supported", [
    ("小兔本来想打开伞，但是没有打开。", "小兔本来想打开伞，但是没有打开。",
     [("打开", False, True), ("打开", True, False)], False),
    ("小兔没有打开伞，而是跑开了。", "小兔没有先打开伞，而是先跑开了。",
     [("打开", True, False), ("跑开", False, False)], True),
    ("小兔撑开了雨伞，没放下。", "小兔打开伞后没有放下。",
     [("打开", False, False), ("放下", True, False)], True),
    ("小兔收起了雨伞。", "我觉得小兔可能想把伞收起来。",
     [("收起", False, True)], False),
    ("小兔没收起雨伞，而是把伞递给小熊。", "小兔不是收起伞，而是把伞递给小熊。",
     [("收起", True, False), ("递给", False, False)], True),
    ("外面下雨了。", "下雨是因为小兔在天上倒了一盆水。", [("下雨", False, False)], False),
    ("小兔打开了雨伞。", "小兔打开雨伞飞上了天。", [("打开", False, False)], False),
    ("小兔打开了雨伞。", "小兔飞上天又打开雨伞。", [("打开", False, False)], False),
])
def test_complex_chinese_claims_preserve_action_scope_without_endorsing_guesses(page, answer, actions, supported):
    signals = reading_agent._answer_signals(answer, page, "这一页发生了什么？")
    assert [(action.verb, action.negated, action.hypothetical) for action in signals.actions] == actions
    assert signals.supports_page_reply is supported
    if not supported:
        assert "这一页也写到" not in reading_agent.reply_to_child_answer(page, answer)


def test_reply_templates_vary_by_age_without_adding_story_facts_or_questions():
    model = AsyncMock()
    model.generate.return_value = ModelGenerateResult(
        success=True, text='{"answerCategory":"RELEVANT","followup":0}',
    )
    replies = [
        asyncio.run(reading_agent.generate_answer_reply(
            model, page_text="小兔打开雨伞。", question="小兔做了什么？", answer="它撑开伞啦",
            age_band=age, topics=[], title="小兔", page_number=1, earlier_pages=[], used_followups=[],
        ))
        for age in ("AGE_3_4", "AGE_4_5", "AGE_5_6")
    ]
    assert len({text for text, _ in replies}) == 3
    assert all("“小兔打开雨伞”" in text and "？" not in text and not followup for text, followup in replies)
