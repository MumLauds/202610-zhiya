"""Protocol checks for the isolated A/B/C comparison; never call a provider."""

import asyncio
import json
from types import SimpleNamespace

from qa.p6.co_reading_eval import compare_qwen_semantics as ab
from qa.p6.co_reading_eval.qwen_decision import DecisionContext, Evidence, Question, SemanticSignals


def case(answer, *, expected=None, blocked=False):
    return SimpleNamespace(
        id="SYNTHETIC-01", category="PROTOCOL",
        context=DecisionContext(
            age_band="AGE_4_5", approved_page="小兔打开雨伞。",
            evidence=(Evidence(id="e1", quote="小兔打开雨伞"),), followups_remaining=1,
        ),
        question="小兔做了什么？", answer=answer, known_focuses={},
        expected=expected or SemanticSignals(), blocked=blocked,
    )


def test_comparison_uses_existing_projection_actual_extraction_and_an_independent_oracle(monkeypatch):
    raw_answer = "我猜它会遇到紫玻璃糖标识。"
    fixture = case(raw_answer, expected=SemanticSignals(speech_act="IMAGINES", polarity="UNKNOWN"))
    local = ab.extract_semantic_signals(
        question=fixture.question, answer=fixture.answer, page_text=fixture.context.approved_page,
        evidence=fixture.context.evidence, known_focuses=fixture.known_focuses,
    )
    seen = []
    original = ab._ScriptedAdapter.generate

    async def capture(self, request):
        assert "紫玻璃糖标识" not in request.model_dump_json()
        seen.append(json.loads(request.context_text))
        return await original(self, request)

    monkeypatch.setattr(ab._ScriptedAdapter, "generate", capture)
    data = asyncio.run(ab.compare([fixture]))
    assert len(seen) == 3
    actual, oracle = local.signals, fixture.expected
    assert [row["localSignals"]["speechAct"] for row in seen] == ["UNKNOWN", actual.speech_act, oracle.speech_act]
    assert [row["localSignals"]["polarity"] for row in seen] == ["UNKNOWN", actual.polarity, oracle.polarity]
    assert [row["ageBand"] for row in seen] == ["AGE_4_5"] * 3
    assert [row["evidence"] for row in seen] == [[{"id": "e1", "quote": "小兔打开雨伞"}]] * 3
    assert all(row["localSignals"]["answerRelation"] == "UNKNOWN" for row in seen)
    assert all(arm["scripted_calls"] == 1 for arm in data["arms"].values())
    assert data["actual_provider_calls"] == 0 and data["human_blind_review"] == "NOT_RUN"
    assert "紫玻璃糖标识" not in json.dumps(data, ensure_ascii=False)


def test_a_b_c_do_not_construct_an_adapter_for_a_known_unsafe_turn(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unsafe input reached extraction or adapter construction")

    monkeypatch.setattr(ab, "extract_semantic_signals", forbidden)
    monkeypatch.setattr(ab, "_ScriptedAdapter", forbidden)
    # Deliberately absent oracle block label: local guard, not gold, decides.
    data = asyncio.run(ab.compare([case("我的邮箱是 fixture@example.invalid。", blocked=False)]))
    assert data["privacy_blocked_cases"] == 1 and data["comparable_nonblocked_cases"] == 0
    assert all(arm["scripted_calls"] == 0 for arm in data["arms"].values())
    assert all(arm["actions"] == {"HANDOFF": 1} for arm in data["arms"].values())
    assert "example.invalid" not in json.dumps(data, ensure_ascii=False)


def test_harness_does_not_gate_safe_case_with_oracle_privacy_label():
    data = asyncio.run(ab.compare([case("我看到了雨伞。", blocked=True)]))
    assert data["privacy_blocked_cases"] == 0
    assert data["comparable_nonblocked_cases"] == 1
    assert all(arm["scripted_calls"] == 1 for arm in data["arms"].values())


def test_extractor_privacy_signal_blocks_all_three_arms(monkeypatch):
    monkeypatch.setattr(ab, "extract_semantic_signals", lambda **_: SimpleNamespace(unsafe_turn=True))
    data = asyncio.run(ab.compare([case("我看到了雨伞。")]))
    assert data["privacy_blocked_cases"] == 1
    assert all(arm["scripted_calls"] == 0 for arm in data["arms"].values())


def test_script_does_not_select_followup_without_required_ids():
    fixture = case("我还想看看这一页。")
    fixture.context = DecisionContext.model_validate({
        **dict(fixture.context), "turn_intent": "KEEP_OBSERVING",
        "questions": (Question(
            id="q1", focus_id="f1", intent="OBSERVATION", evidence_ids=("e1",), text="你能找到雨伞吗？",
        ),),
    })
    row, _ = asyncio.run(ab._case(fixture))
    # ASK_FOLLOWUP sorts before INVITE_OBSERVATION; the script must not select it with null IDs.
    assert all(arm["offered_followups"] == 1 and arm["action"] == "INVITE_OBSERVATION"
               and arm["source"] == "MODEL" for arm in row["arms"].values())
