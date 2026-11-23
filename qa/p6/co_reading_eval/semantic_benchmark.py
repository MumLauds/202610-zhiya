"""Offline synthetic semantic benchmark. Gold labels are evaluation-only, never extractor inputs."""

import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from itertools import combinations
from pathlib import Path

from .qwen_decision import (
    Action, DecisionContext, Intent, SemanticSignals, _choices, _payload, choose_reading_plan,
)

CATEGORIES = (
    "different_meanings", "paraphrases", "child_speech", "complex", "negation_hypothesis", "privacy_injection",
)
AGES = ("AGE_3_4", "AGE_4_5", "AGE_5_6")
FIELDS = ("speech_act", "polarity", "referenced_evidence_ids", "referenced_focus_ids")
FIXTURE = Path(__file__).with_name("semantic-cases-v1.json")


@dataclass(frozen=True)
class SemanticCase:
    id: str
    category: str
    meaning_id: str
    pair_group: str
    context: DecisionContext
    question: str
    answer: str = field(repr=False)
    expected: SemanticSignals
    blocked: bool
    target_intent: Intent | None = None
    target_action: Action | None = None
    target_focus_id: str | None = None

    @property
    def known_focuses(self) -> dict[str, tuple[str, ...]]:
        return {question.focus_id: question.evidence_ids for question in self.context.questions}


def _context(value: dict) -> DecisionContext:
    return DecisionContext.model_validate_json(json.dumps(value, ensure_ascii=False))


def load_cases(path: Path = FIXTURE) -> tuple[SemanticCase, ...]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture.keys() != {"version", "synthetic_only", "contexts", "cases"} or (
        fixture["version"] != 1 or fixture["synthetic_only"] is not True
    ):
        raise ValueError("expected the versioned synthetic semantic fixture")
    contexts = {key: _context(value) for key, value in fixture["contexts"].items()}
    cases = []
    for item in fixture["cases"]:
        case_data = dict(item)
        ctx = contexts[case_data.pop("context_id")]
        question = next(question.text for question in ctx.questions if question.id == ctx.current_question_id)
        gold = case_data.pop("expected")
        if set(gold) != set(FIELDS):
            raise ValueError("every label must explicitly state all four semantic fields")
        expected = SemanticSignals.model_validate_json(json.dumps(gold))
        # Valid IDs matter for gold too, independent of the extraction implementation.
        _with_extraction(ctx, expected, False)
        cases.append(SemanticCase(context=ctx, question=question, expected=expected, **case_data))
    _validate_cases(cases)
    return tuple(cases)


def _with_extraction(context: DecisionContext, signals: SemanticSignals, unsafe: bool) -> DecisionContext:
    return DecisionContext.model_validate({
        **dict(context), "semantic_signals": signals, "unsafe_turn": unsafe,
    })


def _validate_cases(cases: list[SemanticCase]) -> None:
    if set(case.category for case in cases) != set(CATEGORIES) or set(case.context.age_band for case in cases) != set(AGES):
        raise ValueError("fixture must exercise all six families and three age bands")
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    for group in _groups(cases).values():
        if len(group) < 2 or len({(case.context, case.question) for case in group}) != 1:
            raise ValueError("a comparison group must hold the page, question and context fixed")
        if len({case.answer for case in group}) != len(group):
            raise ValueError("comparison answers must differ in wording")
        gold_by_meaning = {}
        for case in group:
            previous = gold_by_meaning.setdefault(case.meaning_id, case.expected)
            if case.expected != previous:
                raise ValueError("reference labels for paraphrases must agree")
    for case in cases:
        if not case.answer.strip() or not case.meaning_id or not case.pair_group or type(case.blocked) is not bool:
            raise ValueError("a synthetic case requires nonempty text and explicit metadata")
        if case.context.unsafe_turn or case.context.semantic_signals != SemanticSignals():
            raise ValueError("the shared baseline cannot contain gold semantics or a privacy label")
        if (case.category == "privacy_injection") != case.blocked or (
            case.blocked and (case.expected != SemanticSignals() or case.target_action != "HANDOFF")
        ):
            raise ValueError("protected cases must target local handoff with default semantic labels")
        if case.target_focus_id and not any(
            question.focus_id == case.target_focus_id and question.intent == case.target_intent
            for question in case.context.questions
        ):
            raise ValueError("the locally reviewed candidate must have the labelled target intent and focus")
    for category in CATEGORIES[:-1]:
        cats = [case for case in cases if case.category == category]
        if not all(any((left.meaning_id == right.meaning_id) == same for left, right in _pairs(cats))
                   for same in (False, True)):
            raise ValueError("each safe category needs both a contrast and a paraphrase within fixed groups")


def _groups(cases):
    groups = defaultdict(list)
    for case in cases:
        groups[case.category, case.pair_group].append(case)
    return groups


def _pairs(cases):
    for group in _groups(cases).values():
        yield from combinations(group, 2)


@dataclass(frozen=True)
class _Outcome:
    signals: SemanticSignals
    unsafe: bool
    baseline_wire: str | None = field(repr=False)
    extracted_wire: str | None = field(repr=False)
    handoff: bool = False
    dispatches: int = 0
    raw_answer_dispatches: int = 0


def _wire(ctx: DecisionContext) -> str:
    actions, questions = _choices(ctx)
    data = _payload(ctx, actions, questions)
    # Evidence/focus IDs are sets for metric purposes. Wire field ordering and
    # semantically empty differences in reference list order are not a split.
    signals = data["localSignals"]
    for key in ("referencedEvidenceIds", "referencedFocusIds"):
        signals[key] = sorted(set(signals[key]))
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


class _PrivacyProbe:
    """Mock adapter: records unexpected dispatch, never makes a provider call."""

    def __init__(self, answer: str):
        self.answer = answer
        self.calls = 0
        self.raw_dispatches = 0

    async def generate(self, request):
        self.calls += 1
        self.raw_dispatches += self.answer in "\n".join(
            (request.system_prompt, request.context_text, request.user_input)
        )
        raise AssertionError("privacy probe has no provider")


async def _run_case(case: SemanticCase) -> _Outcome:
    from .semantic_extractor import extract_semantic_signals

    extracted = extract_semantic_signals(
        question=case.question, answer=case.answer, page_text=case.context.approved_page,
        evidence=case.context.evidence, known_focuses=case.known_focuses,
    )
    ctx = _with_extraction(case.context, extracted.signals, extracted.unsafe_turn)
    baseline = _wire(case.context) if not extracted.unsafe_turn else None
    wire = _wire(ctx) if not extracted.unsafe_turn else None
    outcome = _Outcome(extracted.signals, extracted.unsafe_turn, baseline, wire)
    if case.blocked:
        probe = _PrivacyProbe(case.answer)
        result = await choose_reading_plan(probe, ctx)
        outcome = replace(outcome, handoff=result.action == "HANDOFF" and result.reason == "LOCAL_HANDOFF",
                          dispatches=probe.calls, raw_answer_dispatches=probe.raw_dispatches)
    return outcome


def _rate(num: int, den: int) -> dict:
    return {"numerator": num, "denominator": den, "rate": round(num / den, 6) if den else None}


def _value(signals: SemanticSignals, key: str):
    value = getattr(signals, key)
    return frozenset(value) if key.startswith("referenced_") else value


def _pair_rates(cases: list[SemanticCase], outcomes: dict[str, _Outcome], *, same: bool) -> dict:
    pairs = [(left, right) for left, right in _pairs(cases) if (left.meaning_id == right.meaning_id) == same]
    eligible = [(left, right) for left, right in pairs if all(not outcomes[item.id].unsafe for item in (left, right))]

    def failures(attr: str):
        return [[left.id, right.id] for left, right in eligible
                if (getattr(outcomes[left.id], attr) != getattr(outcomes[right.id], attr)) == same]

    baseline, extracted = failures("baseline_wire"), failures("extracted_wire")
    return {
        "labelled_pairs": len(pairs), "excluded_unexpected_blocks": len(pairs) - len(eligible),
        "baseline": _rate(len(baseline), len(eligible)), "extracted": _rate(len(extracted), len(eligible)),
        "extracted_pair_ids": extracted,
    }


def _measure(cases: list[SemanticCase], outcomes: dict[str, _Outcome]) -> dict:
    safe = [case for case in cases if not case.blocked]
    protected = [case for case in cases if case.blocked]
    exact = {}
    for key in FIELDS:
        wrong = [case.id for case in safe if _value(case.expected, key) != _value(outcomes[case.id].signals, key)]
        exact[key] = {**_rate(len(safe) - len(wrong), len(safe)), "mismatch_case_ids": wrong}
    unknown = {}
    for key in ("speech_act", "polarity"):
        expected_unknown = [case for case in safe if _value(case.expected, key) == "UNKNOWN"]
        unknown_hits = sum(_value(outcomes[case.id].signals, key) == "UNKNOWN" for case in safe)
        false_confident = [case.id for case in expected_unknown if _value(outcomes[case.id].signals, key) != "UNKNOWN"]
        unknown[key] = {
            "observed": _rate(unknown_hits, len(safe)),
            "correct_abstention": _rate(len(expected_unknown) - len(false_confident), len(expected_unknown)),
            "false_confidence": {**_rate(len(false_confident), len(expected_unknown)), "case_ids": false_confident},
        }
    abstention_cases = {case.id for case in safe if "UNKNOWN" in (case.expected.speech_act, case.expected.polarity)}
    confidence_cases = sorted({case_id for item in unknown.values() for case_id in item["false_confidence"]["case_ids"]})
    extra_ids = {}
    for key in FIELDS[2:]:
        ids = [case.id for case in safe if _value(outcomes[case.id].signals, key) - _value(case.expected, key)]
        extra_ids[key] = {**_rate(len(ids), len(safe)), "case_ids": ids}
    blocked = [case for case in protected if outcomes[case.id].unsafe]
    handoff = [case for case in protected if outcomes[case.id].handoff and outcomes[case.id].dispatches == 0]
    return {
        "cases": len(cases), "scored_non_protected_cases": len(safe),
        "age_cases": dict(sorted(Counter(case.context.age_band for case in cases).items())),
        "different_meaning_collision": _pair_rates(safe, outcomes, same=False),
        "paraphrase_split": _pair_rates(safe, outcomes, same=True),
        "exact_accuracy": exact, "unknown": unknown,
        "false_confidence_any_gold_unknown": {**_rate(len(confidence_cases), len(abstention_cases)), "case_ids": confidence_cases},
        "extra_reference_ids": extra_ids,
        "privacy": {
            "extractor_interception": _rate(len(blocked), len(protected)),
            "local_handoff_without_dispatch": _rate(len(handoff), len(protected)),
            "incorrect_block_on_safe_case": _rate(sum(outcomes[case.id].unsafe for case in safe), len(safe)),
            "protected_mock_dispatches": sum(outcomes[case.id].dispatches for case in protected),
            "protected_full_answer_mock_dispatches": sum(outcomes[case.id].raw_answer_dispatches for case in protected),
            "protected_nondefault_semantics": sum(outcomes[case.id].signals != SemanticSignals() for case in protected),
        },
    }


async def evaluate() -> dict:
    cases = load_cases()
    outcomes = {case.id: await _run_case(case) for case in cases}
    return {
        "scope": "independently prelabelled synthetic extraction and wire projection; not model/interaction quality",
        "reference_labels": "PENDING_HUMAN_REVIEW",
        "provider_calls": 0,
        "interpretation": {
            "pair_population": "same category, pair group and fixed story/question/context; protected cases excluded",
            "baseline": "existing wire shape with default UNKNOWN/empty semantic signals",
            "extracted": "existing wire shape with actual local extractor signals, never gold input",
            "unknown": "gold UNKNOWN is a valid label; false-confidence denominator is gold UNKNOWN for that field",
            "references": "exact ID-set agreement; gold evidence capped at two, prioritizing the current question",
            "privacy": "actual local extractor and mock decision boundary; zero provider calls by design",
        },
        "overall": _measure(list(cases), outcomes),
        "by_category": {key: _measure([case for case in cases if case.category == key], outcomes) for key in CATEGORIES},
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(evaluate()), ensure_ascii=False, indent=2))
