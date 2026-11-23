"""Synthetic, offline protocol smoke and timing. Never connects to a provider."""

import asyncio
import json
from collections import Counter
from math import ceil
from time import perf_counter

from app.schemas.agent import ModelGenerateRequest, ModelGenerateResult
from qa.p6.co_reading_eval.qwen_decision import (
    DecisionContext, Evidence, Question, VisitQuestion, choose_reading_plan, question_signature,
)


class MockAdapter:
    def __init__(self, outcome: ModelGenerateResult):
        self.outcome = outcome
        self.calls = 0
        self.offered_question_ids = set()

    async def generate(self, request: ModelGenerateRequest) -> ModelGenerateResult:
        assert request.json_object and request.page_image_url is None
        self.calls += 1
        self.offered_question_ids = {
            item["questionId"] for item in json.loads(request.context_text)["questions"]
        }
        return self.outcome


def quantile(values: list[float], percentile: float) -> float:
    return round(sorted(values)[ceil(len(values) * percentile) - 1], 3)


async def benchmark() -> dict:
    evidence = Evidence(id="e1", quote="小兔打开雨伞")
    question = Question(
        id="q1", focus_id="f1", intent="OBSERVATION",
        evidence_ids=("e1",), text="你能找到雨伞吗？",
    )
    base = dict(age_band="AGE_4_5", turn_intent="ORDINARY", answer_relation="SUPPORTED",
                approved_page="小兔打开雨伞。", evidence=(evidence,), answer_evidence_ids=("e1",),
                questions=(question,), followups_remaining=1)
    valid = ModelGenerateResult(success=True, text=json.dumps({
        "understanding": "GROUNDED", "action": "ASK_FOLLOWUP", "questionId": "q1",
        "focusId": "f1", "evidenceIds": ["e1"], "proposedResponse": "你看到它撑开伞啦！",
        "proposedFollowup": "你还想找找它的伞吗？",
    }, ensure_ascii=False))
    shadow = ModelGenerateResult(success=True, text=json.dumps({
        "understanding": "UNVERIFIED", "action": "RESPOND", "questionId": None,
        "focusId": None, "evidenceIds": [], "proposedUnderstanding": "IMAGINATIVE",
    }))
    unsupported_question = Question(
        id="q1", focus_id="f1", intent="OBSERVATION",
        evidence_ids=("e1",), text="你能找到松果吗？",
    )
    cases = {
        "valid": ({}, valid, "MODEL", None, 1),
        "bad_json": ({}, ModelGenerateResult(success=True, text="{"), "LOCAL", "INVALID_PLAN", 1),
        "timeout": ({}, ModelGenerateResult(success=False, error_code="TIMEOUT"), "LOCAL", "TIMEOUT", 1),
        "used_focus": ({"used_focus_ids": ("f1",)}, valid, "LOCAL", "INVALID_PLAN", 1),
        "dropped_prior_question": (
            {"shown_question_ids": ("q9",), "used_focus_ids": ("f9",),
             "shown_question_history": (VisitQuestion(id="q9", signature=question_signature("可以找到雨具吗？")),)},
            valid, "LOCAL", "INVALID_PLAN", 1,
        ),
        "unrelated_question": ({"questions": (unsupported_question,)}, valid, "LOCAL", "INVALID_PLAN", 1),
        "shadow_hypothesis": (
            {"answer_relation": "UNKNOWN", "answer_evidence_ids": ()}, shadow, "MODEL", None, 1,
        ),
        "no_budget": ({"followups_remaining": 0}, valid, "LOCAL", "INVALID_PLAN", 1),
        "privacy_gate": ({"unsafe_turn": True}, valid, "LOCAL", "LOCAL_HANDOFF", 0),
    }
    samples, outcome_counts = [], Counter()
    calls = fallbacks = 0
    for _ in range(30):
        for name, (changes, response, expected_source, expected_reason, expected_calls) in cases.items():
            adapter = MockAdapter(response)
            ctx = DecisionContext(**(base | changes))
            started = perf_counter()
            result = await choose_reading_plan(adapter, ctx, synthetic_review=True)
            samples.append((perf_counter() - started) * 1000)
            assert (result.source, result.reason, adapter.calls) == (expected_source, expected_reason, expected_calls), name
            if name == "valid":
                assert result.followup == question.text and result.synthetic_draft is not None
                assert result.synthetic_draft.followup != result.followup
                assert result.synthetic_draft.response != result.response
            elif name == "shadow_hypothesis":
                assert result.followup is None and result.understanding == "UNVERIFIED"
                assert result.synthetic_draft is not None and result.synthetic_draft.understanding == "IMAGINATIVE"
            else:
                assert result.followup is None and result.synthetic_draft is None
            if name in {"dropped_prior_question", "unrelated_question"}:
                assert question.id not in adapter.offered_question_ids, name
            calls += adapter.calls
            fallbacks += result.source == "LOCAL" and result.reason != "LOCAL_HANDOFF"
            outcome_counts[name] += 1
    return {
        "mode": "MOCK_ONLY",
        "turns": len(samples),
        "case_counts": dict(outcome_counts),
        "actual_provider_calls": 0,
        "mock_adapter_calls": calls,
        "expected_fault_fallbacks": fallbacks,
        "local_privacy_blocks": outcome_counts["privacy_gate"],
        "local_p50_ms": quantile(samples, 0.50),
        "local_p95_ms": quantile(samples, 0.95),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(benchmark()), ensure_ascii=False, indent=2))
