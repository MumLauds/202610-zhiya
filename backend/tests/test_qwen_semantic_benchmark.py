"""Semantic fixture and metrics checks; no provider or quality claims."""

import asyncio
import json
from dataclasses import replace
from hashlib import sha256

import pytest

from qa.p6.co_reading_eval.qwen_decision import SemanticSignals
from qa.p6.co_reading_eval.semantic_benchmark import (
    AGES, CATEGORIES, FIELDS, FIXTURE, FIXTURE_V1, _run_case, evaluate, load_cases,
)


def test_corpus_has_independent_labels_and_controlled_pairs():
    cases = load_cases()
    assert len(cases) == 103
    assert {item.category for item in cases} == set(CATEGORIES)
    assert {item.context.age_band for item in cases} == set(AGES)
    assert sum(item.blocked for item in cases) == 7
    assert all(not item.context.unsafe_turn and item.context.semantic_signals == SemanticSignals() for item in cases)
    assert all(item.answer not in repr(item) for item in cases)
    for age in AGES:
        cases_at_age = [case for case in cases if case.context.age_band == age]
        assert {case.category for case in cases_at_age} == set(CATEGORIES)
        added_at_age = {case.phenomenon for case in cases_at_age if case.phenomenon}
        assert {"colloquial", "double_negation", "multiple_actions"} <= added_at_age
        assert any(tag.startswith("asr_") for tag in added_at_age)
        assert any("causal" in tag for tag in added_at_age)
        assert sum(case.phenomenon is not None for case in cases_at_age) == 18


def test_v2_label_revision_log_exactly_matches_independent_changes_to_retained_v1_rows():
    assert sha256(FIXTURE_V1.read_bytes()).hexdigest() == (
        "5316ec91f0a3cab3c0466ef554d8b2ddd5096a07420d55522c2f0fb10ee38f12"
    )
    assert len(load_cases(FIXTURE_V1)) == 49
    before = json.loads(FIXTURE_V1.read_text(encoding="utf-8"))
    after = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert before["contexts"] == after["contexts"]
    assert after["annotation_status"] == "PENDING_HUMAN_REVIEW"
    old_cases = {case["id"]: case for case in before["cases"]}
    new_cases = {case["id"]: case for case in after["cases"]}
    revisions = {(row["case_id"], row["field"]): row for row in after["label_revisions"]}
    assert len(revisions) == len(after["label_revisions"])
    assert {case_id for case_id, _ in revisions} == {"e4", "d4", "d5"}
    observed = set()
    for case_id, old in old_cases.items():
        current = new_cases[case_id]
        assert {key: value for key, value in old.items() if key != "expected"} == {
            key: value for key, value in current.items() if key != "expected"
        }
        for field in FIELDS:
            if old["expected"][field] != current["expected"][field]:
                observed.add((case_id, field))
                entry = revisions[case_id, field]
                assert entry["previous"] == old["expected"][field] and entry["current"] == current["expected"][field]
                assert entry["reason"].strip()
    assert observed == set(revisions)
    assert len(set(new_cases) - set(old_cases)) == 54


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
    assert overall["scored_non_protected_cases"] == 96
    assert overall["different_meaning_collision"]["baseline"] == {
        "numerator": 177, "denominator": 177, "rate": 1.0,
    }
    assert overall["paraphrase_split"]["baseline"] == {"numerator": 0, "denominator": 36, "rate": 0.0}
    assert all(metric["denominator"] == 96 for metric in overall["exact_accuracy"].values())
    assert set(overall["exact_accuracy"]) == set(FIELDS)
    assert overall["false_confidence_any_gold_unknown"]["denominator"] == 30
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
    assert data["fixture_version"] == 2 and data["historical_label_revision_count"] == 5
    cases = load_cases()
    assert set(data["by_age"]) == set(AGES)
    assert sum(item["cases"] for item in data["by_age"].values()) == len(cases)
    for key, age_slices in data["by_category_age"].items():
        assert set(age_slices) == set(AGES)
        assert sum(item["cases"] for item in age_slices.values()) == data["by_category"][key]["cases"]
        for age, metrics in age_slices.items():
            case_ids = {item.id for item in cases if item.category == key and item.context.age_band == age}
            assert metrics["cases"] == len(case_ids)
            for field in FIELDS:
                result = metrics["exact_accuracy"][field]
                assert len(result["mismatch_case_ids"]) == result["denominator"] - result["numerator"]
                assert set(result["mismatch_case_ids"]) <= case_ids
    serialized = json.dumps(data, ensure_ascii=False)
    assert not any(case.answer in serialized for case in cases)
