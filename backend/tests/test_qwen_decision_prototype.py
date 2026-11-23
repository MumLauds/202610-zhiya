import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.schemas.agent import ModelGenerateResult
from qa.p6.co_reading_eval import qwen_decision as proto


def context(**overrides):
    fields = dict(
        age_band="AGE_4_5", approved_page="下雨了。小兔打开雨伞。不要出站的整页标记。",
        answer_relation="SUPPORTED",
        evidence=(proto.Evidence(id="e1", quote="小兔打开雨伞"), proto.Evidence(id="e2", quote="下雨了")),
        answer_evidence_ids=("e1",),
        questions=(
            proto.Question(id="q1", focus_id="f1", intent="OBSERVATION", focus="OBJECT",
                           evidence_ids=("e1",), text="你能找到雨伞吗？"),
            proto.Question(id="q2", focus_id="f2", intent="CAUSAL", focus="CAUSE",
                           evidence_ids=("e1", "e2"), text="为什么小兔打开雨伞？"),
        ),
        followups_remaining=2,
    )
    return proto.DecisionContext(**(fields | overrides))


def wire(**overrides):
    values = dict(understanding="GROUNDED", action="ASK_FOLLOWUP", questionId="q1", focusId="f1", evidenceIds=["e1"])
    return json.dumps(values | overrides, ensure_ascii=False)


def run(raw=None, *, ctx=None, review=False, result=None):
    model = AsyncMock()
    model.generate.return_value = result or ModelGenerateResult(success=True, text=raw if raw is not None else wire())
    return asyncio.run(proto.choose_reading_plan(model, ctx or context(), synthetic_review=review)), model


def test_valid_plan_keeps_natural_draft_strictly_separate():
    raw = wire(proposedResponse="原来小兔飞上月球啦。", proposedFollowup="月球上方有什么？",
               proposedConversationIntent="CONTINUE_READING")
    plan, model = run(raw, review=True)
    assert (plan.source, plan.action, plan.understanding) == ("MODEL", "ASK_FOLLOWUP", "GROUNDED")
    assert plan.followup == "你能找到雨伞吗？"
    assert plan.response == "书里写着“小兔打开雨伞”。"
    assert plan.synthetic_draft.response == "原来小兔飞上月球啦。"
    assert plan.synthetic_draft.followup == "月球上方有什么？"
    assert plan.synthetic_draft.conversation_intent == "CONTINUE_READING"
    assert "月球" not in repr(plan) + plan.response + plan.followup
    assert run(raw)[0].synthetic_draft is None
    model.generate.assert_awaited_once()
    request = model.generate.await_args.args[0]
    assert request.json_object and request.timeout_seconds <= 5 and request.page_image_url is None
    assert "不要出站的整页标记" not in request.context_text
    payload = json.loads(request.context_text)
    assert {"rawAnswer", "childName", "sessionId", "pageText"}.isdisjoint(payload)
    assert payload["questions"][0]["focus"] == "OBJECT"
    assert payload["questions"][0]["reviewedQuestion"] == "你能找到雨伞吗？"


@pytest.mark.parametrize("overrides", [
    {"action": "ADVANCE_PAGE"}, {"action": "HANDOFF"}, {"understanding": "IMAGINATIVE"},
    {"questionId": "q9"}, {"focusId": "f2"}, {"evidenceIds": ["e2"]}, {"evidenceIds": ["e1", "e1"]},
    {"evidenceIds": ["e1", "e9"]}, {"proposedResponse": "请告诉我你的手机号"},
    {"proposedFollowup": "第一个问题？第二个问题？"},
    {"action": "RESPOND", "questionId": None, "focusId": None, "proposedFollowup": "接下来怎么样？"},
    {"arbitraryAnswer": "不应显示的测试正文"},
])
def test_invalid_selection_degrades_after_one_call(overrides):
    plan, model = run(wire(**overrides), review=True)
    assert (plan.source, plan.reason, plan.followup, plan.synthetic_draft) == ("LOCAL", "INVALID_PLAN", None, None)
    assert plan.response == "书里写着“小兔打开雨伞”。"
    model.generate.assert_awaited_once()


@pytest.mark.parametrize("raw", [
    "就随便问孩子一个问题",
    '{"understanding":"GROUNDED","understanding":"UNVERIFIED","action":"RESPOND","questionId":null,"focusId":null,"evidenceIds":[]}',
    "x" * (proto.MAX_PLAN_CHARS + 1),
])
def test_malformed_responses_do_not_retry_or_display_raw_text(raw):
    plan, model = run(raw)
    assert plan.source == "LOCAL" and plan.reason == "INVALID_PLAN"
    assert raw not in plan.response
    model.generate.assert_awaited_once()


@pytest.mark.parametrize("intent,answer_action", [
    ("CONTINUE_READING", "CLOSE"), ("CLOSE_TOPIC", "CLOSE"),
    ("KEEP_OBSERVING", "INVITE_OBSERVATION"), ("REFUSE_READING", "RESPOND"),
])
def test_reading_intent_is_not_answer_correctness_or_book_navigation(intent, answer_action):
    ctx = context(turn_intent=intent, answer_relation="UNKNOWN", answer_evidence_ids=())
    plan, model = run(wire(understanding="UNVERIFIED", action=answer_action, questionId=None, focusId=None,
                           evidenceIds=[]), ctx=ctx)
    assert plan.source == "MODEL" and plan.action == answer_action and plan.followup is None
    assert "答错" not in plan.response
    if intent == "REFUSE_READING":
        assert "先不往下读" in plan.response
    model.generate.assert_awaited_once()


def test_grounded_turn_can_close_without_consuming_followups():
    plan, _ = run(wire(action="CLOSE", questionId=None, focusId=None))
    assert (plan.source, plan.action, plan.followup) == ("MODEL", "CLOSE", None)


def test_budget_current_focus_and_semantic_repeat_block_stale_asks():
    for ctx in (context(followups_remaining=0), context(current_focus_id="f1"), context(used_focus_ids=("f1",))):
        plan, model = run(ctx=ctx)
        assert plan.source == "LOCAL" and plan.followup is None
        assert all(q["questionId"] != "q1" for q in json.loads(model.generate.await_args.args[0].context_text)["questions"])
    general = proto.Question(id="q1", focus_id="f1", intent="OBSERVATION", evidence_ids=("e1",), text="你看见了什么？")
    reworded = proto.Question(id="q2", focus_id="f2", intent="OBSERVATION", evidence_ids=("e1",), text="画面里有什么？")
    plan, model = run(wire(questionId="q2", focusId="f2"),
                      ctx=context(questions=(general, reworded), current_question_id="q1"))
    assert plan.source == "LOCAL" and plan.followup is None
    assert json.loads(model.generate.await_args.args[0].context_text)["questions"] == []


def test_age_and_unknown_relation_constrain_but_do_not_preselect_actions():
    valid = wire(understanding="UNVERIFIED")
    plan, model = run(valid, ctx=context(age_band="AGE_3_4", answer_relation="UNKNOWN", answer_evidence_ids=()))
    out = json.loads(model.generate.await_args.args[0].context_text)
    assert [q["questionId"] for q in out["questions"]] == ["q1"]
    assert {"ASK_FOLLOWUP", "RESPOND", "INVITE_OBSERVATION"} == set(out["allowedActions"])
    assert (plan.source, plan.action, plan.understanding) == ("MODEL", "ASK_FOLLOWUP", "UNVERIFIED")
    assert run(ctx=context(answer_relation="UNKNOWN", answer_evidence_ids=()))[0].reason == "INVALID_PLAN"


def test_privacy_and_evidence_verification_are_local():
    with pytest.raises(ValidationError):
        context(raw_child_answer="只属于儿童作答的标记")
    with pytest.raises(ValidationError):
        context(evidence=(proto.Evidence(id="e1", quote="无来源的异页正文"),))
    with pytest.raises(ValidationError):
        context(approved_page="下雨了。小兔没有打开雨伞。",
                evidence=(proto.Evidence(id="e1", quote="打开雨伞"), proto.Evidence(id="e2", quote="下雨了")))
    with pytest.raises(ValidationError):
        context(approved_page="下雨了。小兔打开雨伞abc@example.invalid。")
    with pytest.raises(ValidationError):
        context(recent_turns=(proto.RecentSignal(intent="OBSERVATION", evidence_ids=("e9",)),))
    plan, model = run(ctx=context(unsafe_turn=True))
    assert (plan.action, plan.reason) == ("HANDOFF", "LOCAL_HANDOFF")
    model.generate.assert_not_awaited()


@pytest.mark.parametrize("question", [
    "小兔开火箭飞到月球了吗？",
    "你能找到书包吗？",
    "你能在图里找到雨伞吗？",
])
def test_a_real_evidence_id_does_not_authorize_an_unrelated_question(question):
    item = proto.Question(id="q1", focus_id="f1", intent="OBSERVATION", evidence_ids=("e1",), text=question)
    plan, model = run(ctx=context(questions=(item,)))
    payload = json.loads(model.generate.await_args.args[0].context_text)
    assert payload["questions"] == [] and "ASK_FOLLOWUP" not in payload["allowedActions"]
    assert (plan.source, plan.reason, plan.followup) == ("LOCAL", "INVALID_PLAN", None)
    assert question not in model.generate.await_args.args[0].context_text
    model.generate.assert_awaited_once()


@pytest.mark.parametrize("page,quote,allowed", [
    ("小兔手里有一把雨伞。", "小兔手里有一把雨伞", True),
    ("这一页没出现雨伞。", "这一页没出现雨伞", False),
    ("这一页未出现雨伞。", "这一页未出现雨伞", False),
    ("这一页未见雨伞。", "这一页未见雨伞", False),
    ("雨伞不在这一页。", "雨伞不在这一页", False),
    ("如果下雨，小兔打开雨伞。", "小兔打开雨伞", False),
    ("小熊说：“小兔打开雨伞。”", "小兔打开雨伞", False),
])
def test_object_question_needs_actual_mentions_not_negation_or_quotation(page, quote, allowed):
    item = proto.Question(id="q1", focus_id="f1", intent="OBSERVATION", evidence_ids=("e1",), text="你能找到雨伞吗？")
    ctx = context(approved_page=page, evidence=(proto.Evidence(id="e1", quote=quote),),
                  answer_relation="UNKNOWN", answer_evidence_ids=(), questions=(item,))
    plan, model = run(wire(understanding="UNVERIFIED"), ctx=ctx)
    outbound = json.loads(model.generate.await_args.args[0].context_text)
    assert bool(outbound["questions"]) == allowed
    assert (plan.source == "MODEL") == allowed


def test_causal_question_needs_two_bound_clauses_from_one_asserted_sentence():
    cited = (proto.Evidence(id="e1", quote="小兔打开雨伞"), proto.Evidence(id="e2", quote="因为下雨"))
    plan, _ = run(wire(questionId="q2", focusId="f2", evidenceIds=["e1", "e2"]),
                  ctx=context(approved_page="因为下雨，小兔打开雨伞。", evidence=cited))
    assert plan.source == "MODEL" and plan.followup == "为什么小兔打开雨伞？"
    plan, model = run(wire(questionId="q2", focusId="f2", evidenceIds=["e1", "e2"]))
    assert plan.reason == "INVALID_PLAN"
    assert "为什么小兔打开雨伞" not in model.generate.await_args.args[0].context_text


def test_visit_history_survives_rotating_catalog_and_missing_history_fails_closed():
    old, _ = run()
    shown = old.followup_history
    assert shown and shown.id == "q1"
    rephrased = proto.Question(id="q9", focus_id="f9", intent="OBSERVATION",
                                evidence_ids=("e1",), text="你能在这一页找一找雨具吗？")
    fresh = proto.Question(id="q3", focus_id="f3", intent="OBSERVATION",
                           evidence_ids=("e1",), text="你看见了什么？")
    fields = dict(questions=(rephrased, fresh), current_question_id="q1", shown_question_history=(shown,))
    repeated, model = run(wire(questionId="q9", focusId="f9"), ctx=context(**fields))
    assert repeated.reason == "INVALID_PLAN"
    payload = model.generate.await_args.args[0].context_text
    assert shown.signature not in payload
    assert [q["questionId"] for q in json.loads(payload)["questions"]] == ["q3"]
    next_turn, _ = run(wire(questionId="q3", focusId="f3"), ctx=context(**fields))
    assert next_turn.followup == fresh.text
    missing, model = run(wire(questionId="q3", focusId="f3"),
                         ctx=context(**(fields | {"shown_question_history": ()})))
    assert missing.reason == "INVALID_PLAN"
    assert json.loads(model.generate.await_args.args[0].context_text)["questions"] == []


def test_unknown_accepts_hypothesis_for_blind_review_but_cannot_grant_an_affirmation():
    ctx = context(answer_relation="UNKNOWN", answer_evidence_ids=())
    for hypothesis in ("GROUNDED", "OFF_TOPIC", "IMAGINATIVE"):
        raw = wire(understanding="UNVERIFIED", proposedUnderstanding=hypothesis)
        plan, _ = run(raw, ctx=ctx, review=True)
        assert (plan.source, plan.understanding) == ("MODEL", "UNVERIFIED")
        assert plan.synthetic_draft.understanding == hypothesis
        assert "书里写着" not in plan.response
        assert run(raw, ctx=ctx)[0].synthetic_draft is None
    assert run(wire(proposedUnderstanding="GROUNDED"), ctx=ctx, review=True)[0].reason == "INVALID_PLAN"


def test_timeout_unavailable_and_caught_cancellation_degrade_without_retry(monkeypatch):
    for error, expected in (("TIMEOUT", "TIMEOUT"), ("API_ERROR", "MODEL_ERROR")):
        plan, model = run(result=ModelGenerateResult(success=False, error_code=error))
        assert (plan.source, plan.reason) == ("LOCAL", expected)
        model.generate.assert_awaited_once()
    assert asyncio.run(proto.choose_reading_plan(None, context())).reason == "NO_MODEL"

    async def delayed_result(_request):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return ModelGenerateResult(success=True, text=wire())

    monkeypatch.setattr(proto, "TIMEOUT_SECONDS", 0.001)
    model = AsyncMock()
    model.generate.side_effect = delayed_result
    plan = asyncio.run(proto.choose_reading_plan(model, context()))
    assert (plan.source, plan.reason) == ("LOCAL", "TIMEOUT")
    model.generate.assert_awaited_once()
