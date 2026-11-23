"""Semantic fixture and metrics checks; no provider or quality claims."""

import asyncio
import json
from dataclasses import replace

import pytest

from qa.p6.co_reading_eval.qwen_decision import SemanticSignals
from qa.p6.co_reading_eval.semantic_benchmark import (
    AGES, CATEGORIES, FIELDS, FIXTURE, _run_case, evaluate, load_cases,
)


def test_corpus_has_independent_labels_and_controlled_pairs():
    cases = load_cases()
    assert len(cases) == 49
    assert {item.category for item in cases} == set(CATEGORIES)
    assert {item.context.age_band for item in cases} == set(AGES)
    assert sum(item.blocked for item in cases) == 7
    assert all(not item.context.unsafe_turn and item.context.semantic_signals == SemanticSignals() for item in cases)
    assert all(item.answer not in repr(item) for item in cases)


def test_load_rejects_pair_that_changes_page_or_question(tmp_path):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # a6's gold f1 stays legal in the other context; pair confounding still fails.
    next(item for item in fixture["cases"] if item["id"] == "a6")["context_id"] = "play"
    file = tmp_path / "semantic-cases-invalid.json"
    file.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(ValueError, match="hold the page, question and context fixed"):
        load_cases(file)


def test_gold_cannot_influence_actual_semantics_or_payload():
    case = load_cases()[0]
    different_gold = SemanticSignals(speech_act="ASKS", polarity="UNKNOWN")
    assert case.expected != different_gold
    actual = asyncio.run(_run_case(case))
    modified = asyncio.run(_run_case(replace(case, expected=different_gold)))
    assert actual == modified and actual.signals != different_gold
    first = json.loads(actual.baseline_wire)
    second = json.loads(actual.extracted_wire)
    assert first["localSignals"]["speechAct"] == "UNKNOWN"
    assert second["localSignals"]["speechAct"] == actual.signals.speech_act
    assert second["localSignals"]["referencedEvidenceIds"] == list(actual.signals.referenced_evidence_ids)
    assert first["evidence"] == second["evidence"]


def test_benchmark_reports_unconfounded_denominators_and_privacy_gate():
    data = asyncio.run(evaluate())
    overall = data["overall"]
    assert set(data["by_category"]) == set(CATEGORIES)
    assert overall["scored_non_protected_cases"] == 42
    assert overall["different_meaning_collision"]["baseline"] == {
        "numerator": 124, "denominator": 124, "rate": 1.0,
    }
    assert overall["paraphrase_split"]["baseline"] == {"numerator": 0, "denominator": 17, "rate": 0.0}
    assert all(metric["denominator"] == 42 for metric in overall["exact_accuracy"].values())
    assert set(overall["exact_accuracy"]) == set(FIELDS)
    assert overall["false_confidence_any_gold_unknown"]["denominator"] == 14
    for field in ("speech_act", "polarity"):
        metric = overall["unknown"][field]
        assert metric["correct_abstention"]["denominator"] == metric["false_confidence"]["denominator"]
        assert sum(metric[key]["numerator"] for key in ("correct_abstention", "false_confidence")) == (
            metric["correct_abstention"]["denominator"]
        )
    private = data["by_category"]["privacy_injection"]
    assert private["different_meaning_collision"]["extracted"]["rate"] is None
    assert private["privacy"]["local_handoff_without_dispatch"] == {"numerator": 7, "denominator": 7, "rate": 1.0}
    assert private["privacy"]["protected_mock_dispatches"] == 0
    assert private["privacy"]["protected_full_answer_mock_dispatches"] == 0
    assert private["privacy"]["protected_nondefault_semantics"] == 0
    assert data["provider_calls"] == 0 and data["reference_labels"] == "PENDING_HUMAN_REVIEW"
    assert not any(case.answer in json.dumps(data, ensure_ascii=False) for case in load_cases())
