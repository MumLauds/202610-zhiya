"""Offline A/B/C protocol comparison, not a measurement of Qwen decisions."""

import asyncio
import json
from collections import Counter
from collections.abc import Sequence
from hashlib import sha256

from app.schemas.agent import ModelGenerateRequest, ModelGenerateResult
from app.services.reading_agent import answer_is_unsafe
from qa.p6.co_reading_eval.qwen_decision import DecisionContext, SemanticSignals, choose_reading_plan
from qa.p6.co_reading_eval.semantic_benchmark import SemanticCase, load_cases
from qa.p6.co_reading_eval.semantic_extractor import extract_semantic_signals

ARMS = {
    "A": "EXISTING_PAYLOAD_DEFAULT_SIGNALS",
    "B": "ACTUAL_LOCAL_EXTRACTION",
    "C": "INDEPENDENT_REFERENCE_LABELS_PENDING_HUMAN_REVIEW",
}


class _ScriptedAdapter:
    """Check what the real QA decision module offers, then choose a controlled action."""

    def __init__(self):
        self.calls = 0
        self.payload_fingerprint: bytes | None = None
        self.offered_followups = 0

    async def generate(self, request: ModelGenerateRequest) -> ModelGenerateResult:
        self.calls += 1
        payload = json.loads(request.context_text)
        self.payload_fingerprint = sha256(request.context_text.encode("utf-8")).digest()
        self.offered_followups = len(payload["questions"])
        # The choice is intentionally scripted; do not use the reference label.
        actions = payload["allowedActions"]
        action = next(item for item in ("RESPOND", "INVITE_OBSERVATION", "CLOSE") if item in actions)
        return ModelGenerateResult(success=True, text=json.dumps({
            "understanding": "UNVERIFIED", "action": action,
            "questionId": None, "focusId": None, "evidenceIds": [],
        }))


async def _case(case: SemanticCase) -> tuple[dict, dict[str, bytes | None]]:
    ctx = case.context
    # The oracle label is never used as the local privacy filter.
    blocked = ctx.unsafe_turn or answer_is_unsafe(case.answer)
    if not blocked:
        local = extract_semantic_signals(
            question=case.question, answer=case.answer, page_text=ctx.approved_page,
            evidence=ctx.evidence, known_focuses=case.known_focuses, reviewed_focuses=ctx.questions,
        )
        blocked = local.unsafe_turn

    branches = {}
    fingerprints = {}
    for arm in ARMS:
        adapter = None if blocked else _ScriptedAdapter()
        semantic_signals = SemanticSignals()
        if not blocked and arm == "B":
            semantic_signals = local.signals
        elif not blocked and arm == "C":
            semantic_signals = case.expected
        context = DecisionContext.model_validate({
            **ctx.model_dump(), "unsafe_turn": blocked, "semantic_signals": semantic_signals,
        })
        result = await choose_reading_plan(adapter, context)
        dispatches = adapter.calls if adapter is not None else 0
        if dispatches > 1 or result.synthetic_draft is not None:
            raise AssertionError("offline arm exceeded its dispatch or draft boundary")
        branches[arm] = {
            "scripted_calls": dispatches, "offered_followups": adapter.offered_followups if adapter else 0,
            "action": result.action, "understanding": result.understanding,
            "source": result.source, "fallback_reason": result.reason,
        }
        fingerprints[arm] = adapter.payload_fingerprint if adapter else None
    return {
        "case_id": case.id, "category": case.category, "age_band": ctx.age_band,
        "privacy_blocked_all_arms": blocked, "arms": branches,
    }, fingerprints


async def compare(cases: Sequence[SemanticCase] | None = None) -> dict:
    """Use fixed synthetic fixtures, one fake adapter at most per arm, no external calls."""
    rows = []
    comparable = b_a_different = b_c_different = 0
    for case in load_cases() if cases is None else cases:
        row, fingerprints = await _case(case)
        rows.append(row)
        if all(fingerprints.values()):
            comparable += 1
            b_a_different += fingerprints["B"] != fingerprints["A"]
            b_c_different += fingerprints["B"] != fingerprints["C"]
    return {
        "mode": "OFFLINE_SCRIPTED_NOT_QWEN_QUALITY",
        "actual_provider_calls": 0,
        "human_blind_review": "NOT_RUN",
        "qwen_accuracy_latency_and_cost": "NOT_RUN",
        "case_count": len(rows),
        "comparable_nonblocked_cases": comparable,
        "privacy_blocked_cases": sum(row["privacy_blocked_all_arms"] for row in rows),
        "B_changed_request_vs_A": b_a_different,
        "B_different_request_from_C": b_c_different,
        "arms": {
            arm: {
                "input": description,
                "scripted_calls": sum(row["arms"][arm]["scripted_calls"] for row in rows),
                "actions": dict(Counter(row["arms"][arm]["action"] for row in rows)),
                "fallbacks_after_dispatch": sum(
                    row["arms"][arm]["scripted_calls"] > 0 and row["arms"][arm]["fallback_reason"] is not None
                    for row in rows
                ),
            }
            for arm, description in ARMS.items()
        },
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(compare()), ensure_ascii=False, indent=2))
