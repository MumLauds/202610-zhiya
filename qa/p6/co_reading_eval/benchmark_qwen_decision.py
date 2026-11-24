"""Synthetic, offline protocol smoke and timing. Never connects to a provider."""

import asyncio
import json
from collections import Counter
from hashlib import sha256
from math import ceil
from time import perf_counter

from app.schemas.agent import ModelGenerateRequest, ModelGenerateResult
from qa.p6.co_reading_eval.draft_audit import audit_draft
from qa.p6.co_reading_eval.qwen_decision import (
    DecisionContext, Evidence, Question, SemanticSignals, VisitQuestion, choose_reading_plan, question_signature,
)


class MockAdapter:
    def __init__(self, outcome: ModelGenerateResult):
        self.outcome = outcome
        self.calls = 0
        self.offered_questions = {}
        self.allowed_actions = set()
        self.projection = None
        self.projection_without_added_signals = None

    @property
    def offered_question_ids(self):
        return set(self.offered_questions)

    async def generate(self, request: ModelGenerateRequest) -> ModelGenerateResult:
        assert request.json_object and request.page_image_url is None
        self.calls += 1
        payload = json.loads(request.context_text)
        self.offered_questions = {item["questionId"]: item for item in payload["questions"]}
        self.allowed_actions = set(payload["allowedActions"])
        self.projection = sha256(request.context_text.encode()).digest()
        if "speechAct" in payload["localSignals"]:
            for field in ("speechAct", "polarity", "referencedEvidenceIds", "referencedFocusIds"):
                payload["localSignals"].pop(field)
            self.projection_without_added_signals = sha256(json.dumps(payload, sort_keys=True).encode()).digest()
        return self.outcome


def quantile(values: list[float], percentile: float) -> float:
    return round(sorted(values)[ceil(len(values) * percentile) - 1], 3)


def mock_plan(question=None, *, understanding="UNVERIFIED", action="RESPOND", evidence_ids=(), draft=None):
    plan = {
        "understanding": understanding, "action": "ASK_FOLLOWUP" if question else action,
        "questionId": question.id if question else None, "focusId": question.focus_id if question else None,
        "evidenceIds": list(question.evidence_ids if question else evidence_ids),
    }
    if draft is not None:
        plan["proposedResponse"] = draft
    return ModelGenerateResult(success=True, text=json.dumps(plan, ensure_ascii=False))


async def capability_probes() -> dict:
    """Known-label spot checks; force every mock choice rather than estimating model preference."""
    age_allowlist = {
        "AGE_3_4": {"OBSERVATION", "PLOT"},
        "AGE_4_5": {"OBSERVATION", "PLOT", "CAUSAL", "EMOTION"},
        "AGE_5_6": {"OBSERVATION", "PLOT", "CAUSAL", "EMOTION", "IMAGINATION", "LIFE_CONNECTION"},
    }
    page = "因为下雨，小兔打开雨伞。小熊拿着书包。小兔放下篮子。"
    evidence = tuple(Evidence(id=f"e{i}", quote=quote) for i, quote in enumerate(
        ("小兔打开雨伞", "因为下雨", "小熊拿着书包", "小兔放下篮子"), 1,
    ))
    forms = (
        ("OBSERVATION", "OBJECT", "你能找到雨伞吗？", ("e1",)),
        ("PLOT", "ACTION", "小兔做了什么？", ("e4",)),
        ("CAUSAL", "CAUSE", "为什么小兔打开雨伞？", ("e1", "e2")),
        ("EMOTION", "FEELING", "你猜小兔是什么心情？", ("e4",)),
        ("IMAGINATION", "PREDICTION", "读到“小兔打开雨伞”时，如果换种办法，接下来可能怎样？", ("e1",)),
        ("LIFE_CONNECTION", "DAILY_LIFE", "读到“小兔打开雨伞”时，如果在生活里遇到类似情况，你会怎么做？", ("e1",)),
    )
    questions = tuple(Question(id=f"q{i}", focus_id=f"f{i}", intent=intent, focus=focus, text=text, evidence_ids=ids)
                      for i, (intent, focus, text, ids) in enumerate(forms, 1))
    base = dict(age_band="AGE_5_6", approved_page=page, evidence=evidence, questions=questions, followups_remaining=2)
    samples = []
    calls = Counter()
    fallbacks = Counter()

    async def probe(group, changes, outcome, *, synthetic_review=False):
        adapter = MockAdapter(outcome)
        context = DecisionContext(**(base | changes))
        started = perf_counter()
        result = await choose_reading_plan(adapter, context, synthetic_review=synthetic_review)
        samples.append((perf_counter() - started) * 1000)
        assert adapter.calls == 1, group
        calls[group] += adapter.calls
        if result.source == "LOCAL":
            fallbacks[result.reason] += 1
        return adapter, result

    age_strata = {}
    for age, expected in age_allowlist.items():
        offered_intents, offered_focuses, offered_focus_ids, accepted = set(), set(), set(), set()
        for question in questions:
            adapter, result = await probe("age_strata", {"age_band": age}, mock_plan(question))
            offered = set(adapter.offered_question_ids)
            assert offered == {item.id for item in questions if item.intent in expected}, age
            offered_intents.update(item["intent"] for item in adapter.offered_questions.values())
            offered_focuses.update(item["focus"] for item in adapter.offered_questions.values())
            offered_focus_ids.update(item["focusId"] for item in adapter.offered_questions.values())
            if question.intent in expected:
                assert result.source == "MODEL" and result.action == "ASK_FOLLOWUP" and result.followup == question.text, (age, question.intent)
                assert result.followup_history and result.followup_history.id == question.id, (age, question.intent)
                accepted.add(question.intent)
            else:
                assert result.source == "LOCAL" and result.reason == "INVALID_PLAN" and not result.followup, (age, question.intent)
        age_strata[age] = {
            "expected_intents": sorted(expected), "offered_distinct_intents": sorted(offered_intents),
            "offered_distinct_focus_types": sorted(offered_focuses), "offered_distinct_focus_ids": sorted(offered_focus_ids),
            "verified_selectable_intents": sorted(accepted), "scripted_selection_attempts": len(questions),
            "out_of_age_attempts_rejected": len(questions) - len(expected),
        }

    independent = Question(id="q7", focus_id="f7", intent="OBSERVATION", focus="OBJECT",
                           evidence_ids=("e3",), text="你能找到书包吗？")
    broad = Question(id="q8", focus_id="f8", intent="OBSERVATION", focus="DETAIL",
                     evidence_ids=("e1",), text="你发现了什么？")
    duplicate_pairs = (
        ("SAME_OBJECT_ALIAS", questions[0], "你能找到雨具吗？"),
        ("BROAD_PARAPHRASE", broad, "你看到了什么？"),
    )
    blocked_duplicates = []
    distinct_focus_retained = 0
    for label, duplicate, earlier in duplicate_pairs:
        changes = {"questions": (duplicate, independent), "shown_question_ids": ("q9",),
                   "shown_question_history": (VisitQuestion(id="q9", signature=question_signature(earlier)),)}
        adapter, result = await probe("semantic_duplicates", changes, mock_plan(duplicate))
        assert adapter.offered_question_ids == {independent.id}, label
        assert result.reason == "INVALID_PLAN" and not result.followup, label
        blocked_duplicates.append(label)
        _, result = await probe("semantic_duplicates", changes, mock_plan(independent))
        assert result.source == "MODEL" and result.followup == independent.text, label
        distinct_focus_retained += 1

    endings = {
        "CONTINUE_READING": ("CLOSE", "接着看故事"),
        "CLOSE_TOPIC": ("CLOSE", "先聊到这里"),
        "REFUSE_READING": ("RESPOND", "先不往下读"),
    }
    ending_counts = Counter()
    for age in age_allowlist:
        for intent, (action, reply_cue) in endings.items():
            changes = {"age_band": age, "turn_intent": intent}
            adapter, result = await probe("natural_end", changes, mock_plan(action=action))
            assert adapter.allowed_actions == {action} and not adapter.offered_questions, (age, intent)
            assert (result.source == "MODEL" and result.action == action and result.understanding == "UNVERIFIED"
                    and not result.followup and reply_cue in result.response), (age, intent)
            ending_counts[intent] += 1
    _, result = await probe("natural_end", {"turn_intent": "CONTINUE_READING"}, mock_plan(questions[0]))
    assert result.source == "LOCAL" and result.reason == "INVALID_PLAN" and result.action == "CLOSE" and not result.followup
    _, result = await probe("natural_end", {"turn_intent": "KEEP_OBSERVING"}, mock_plan(action="CLOSE"))
    assert result.source == "LOCAL" and result.reason == "INVALID_PLAN" and result.action == "INVITE_OBSERVATION" and not result.followup

    unrelated = (
        ("WRONG_OBJECT_FOR_EVIDENCE", questions[0].model_copy(update={"text": "你能找到书包吗？"}), {}),
        ("WRONG_ACTOR_FOR_EVIDENCE", questions[1].model_copy(update={"text": "小熊做了什么？"}), {}),
        ("WRONG_CAUSAL_EFFECT", questions[2].model_copy(update={"text": "为什么小兔放下篮子？"}), {}),
        ("UNSUPPORTED_IMAGINATION_ANCHOR",
         questions[4].model_copy(update={"text": "读到“小兔收起雨伞”时，如果换种办法，接下来可能怎样？"}), {}),
        ("HYPOTHETICAL_NOT_OBSERVED", questions[0],
         {"approved_page": "小兔想打开雨伞。", "evidence": (Evidence(id="e1", quote="小兔想打开雨伞"),)}),
    )
    blocked_facts = []
    for label, question, changes in unrelated:
        adapter, result = await probe("fact_rejects", changes | {"questions": (question,)}, mock_plan(question))
        assert not adapter.offered_questions and "ASK_FOLLOWUP" not in adapter.allowed_actions, label
        assert result.source == "LOCAL" and result.reason == "INVALID_PLAN" and not result.followup, label
        blocked_facts.append(label)
    _, result = await probe("fact_rejects", {"answer_relation": "CONFLICT"},
                            mock_plan(understanding="GROUNDED", evidence_ids=("e1",)))
    assert result.source == "LOCAL" and result.reason == "INVALID_PLAN" and result.understanding == "UNVERIFIED"

    audits = Counter()
    audit_context = DecisionContext(**base)
    for draft, verdict in (
        ("书里写着“小兔打开雨伞”。", "MANUAL_REVIEW"),
        ("我猜雨伞会飞起来。", "MANUAL_REVIEW"),
        ("书里写着“小兔开走了飞船”。", "REJECT"),
    ):
        _, result = await probe("draft_audit", {}, mock_plan(draft=draft, evidence_ids=("e1",)), synthetic_review=True)
        assert result.synthetic_draft and result.response != draft and not result.followup
        audit = audit_draft(audit_context, result.synthetic_draft, cited_evidence_ids=("e1",))
        assert audit.verdict == verdict
        audits[audit.verdict] += 1

    # Pair labels are independent fixture annotations. No transcript classifier is being evaluated.
    known_assertion = SemanticSignals(speech_act="ASSERTS", polarity="AFFIRMS",
                                     referenced_evidence_ids=("e1",), referenced_focus_ids=("f1",))
    projection_pairs = (
        ("ACKNOWLEDGEMENT_VS_ASSERTION", False, SemanticSignals(speech_act="ACKNOWLEDGES"), known_assertion),
        ("IMAGINATION_VS_CORRECTION", False, SemanticSignals(speech_act="IMAGINES"),
         SemanticSignals(speech_act="CORRECTS", polarity="NEGATES", referenced_evidence_ids=("e1",))),
        ("ASSERTION_PARAPHRASES", True, known_assertion, known_assertion),
        ("DISTINCT_SENSES_WITHOUT_LABELLED_SIGNAL", False, SemanticSignals(), SemanticSignals()),
    )
    projected_pair_results = {}
    for label, same_semantics, left_signal, right_signal in projection_pairs:
        left, _ = await probe("fixture_projection", {"semantic_signals": left_signal}, mock_plan())
        right, _ = await probe("fixture_projection", {"semantic_signals": right_signal}, mock_plan())
        same_payload = left.projection == right.projection
        assert same_payload == (same_semantics or label == "DISTINCT_SENSES_WITHOUT_LABELLED_SIGNAL"), label
        same_without_added = left.projection_without_added_signals == right.projection_without_added_signals
        assert left.projection_without_added_signals and same_without_added, label
        projected_pair_results[label] = {
            "same_fixture_meaning": same_semantics, "without_added_signals_same_payload": same_without_added,
            "with_labelled_signals_same_payload": same_payload,
        }
    different_meaning = [item for item in projected_pair_results.values() if not item["same_fixture_meaning"]]
    paraphrases = [item for item in projected_pair_results.values() if item["same_fixture_meaning"]]

    return {
        "mode": "SCRIPTED_MOCKS_NOT_MODEL_QUALITY",
        "turns": len(samples),
        "actual_provider_calls": 0,
        "mock_adapter_calls": sum(calls.values()),
        "mock_calls_by_group": dict(calls),
        "age_strata": age_strata,
        "semantic_duplicates": {
            "known_duplicate_attempts": len(duplicate_pairs), "blocked_cases": blocked_duplicates,
            "distinct_focus_accepted": distinct_focus_retained, "distinct_focus_attempts": len(duplicate_pairs),
        },
        "natural_end": {
            "correct_distinct_actions_by_intent": dict(ending_counts),
            "labeled_control_turns": len(age_allowlist) * len(endings),
            "forbidden_followup_on_continue_rejected": True, "false_close_on_keep_observing_rejected": True,
        },
        "fact_rejects": {
            "known_fact_candidate_attempts": len(unrelated), "excluded_and_rejected_cases": blocked_facts,
            "unsupported_affirmation_rejected": True,
        },
        "draft_audit_separate_from_display": {
            "scripted_drafts": sum(audits.values()), "verdicts": dict(audits), "approved_for_display": 0,
        },
        "semantic_projection": {
            "mode": "HAND_LABELLED_FIXTURES_NOT_AUTOMATIC_EXTRACTION",
            "pairs": projected_pair_results,
            "different_meaning_pairs": len(different_meaning),
            "different_meaning_collisions_without_added_signals": sum(
                item["without_added_signals_same_payload"] for item in different_meaning
            ),
            "different_meaning_collisions_with_labelled_signals": sum(
                item["with_labelled_signals_same_payload"] for item in different_meaning
            ),
            "same_meaning_paraphrase_pairs": len(paraphrases),
            "same_meaning_splits_with_labelled_signals": sum(
                not item["with_labelled_signals_same_payload"] for item in paraphrases
            ),
        },
        "fallbacks_on_mock_dispatched_turns": {
            "numerator": sum(fallbacks.values()), "denominator": sum(calls.values()), "by_reason": dict(fallbacks),
        },
        "local_p50_ms": quantile(samples, 0.50),
        "local_p95_ms": quantile(samples, 0.95),
    }


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
    fallback_counts = Counter()
    handoffs = 0
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
            if result.source == "LOCAL":
                if adapter.calls:
                    fallbacks += 1
                    fallback_counts[result.reason] += 1
                else:
                    handoffs += 1
            outcome_counts[name] += 1
    capability = await capability_probes()
    return {
        "mode": "MOCK_ONLY",
        "quality_evaluation": "NOT_RUN",
        "latency_scope": "Local decision, validation and rendering with immediate mock capture; no real model/network latency",
        "turns": len(samples),
        "case_counts": dict(outcome_counts),
        "actual_provider_calls": 0,
        "mock_adapter_calls": calls,
        "expected_fault_fallbacks": fallbacks,
        "local_privacy_blocks": handoffs,
        "fallbacks_on_mock_dispatched_turns": {
            "numerator": fallbacks, "denominator": calls, "by_reason": dict(fallback_counts),
        },
        "local_p50_ms": quantile(samples, 0.50),
        "local_p95_ms": quantile(samples, 0.95),
        "capability_probes": capability,
        "total_offline_turns": len(samples) + capability["turns"],
        "total_mock_adapter_calls": calls + capability["mock_adapter_calls"],
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(benchmark()), ensure_ascii=False, indent=2))
