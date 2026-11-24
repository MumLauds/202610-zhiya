import pytest

from qa.p6.co_reading_eval.response_uptake import UptakeRating, uptake_summary


def test_uptake_is_adjudicated_semantics_not_keyword_echo():
    summary = uptake_summary([
        ("correct-paraphrase", UptakeRating(True, True, True)),
        ("keyword-but-generic", UptakeRating(True, False, True)),
        ("wrong-referent", UptakeRating(False, True, True)),
        ("invented-location", UptakeRating(True, True, False)),
        ("not-reviewed", None),
    ])
    assert summary == {
        "passed": 1, "reviewed": 4, "total": 5, "rate": 0.25,
        "failed_case_ids": ["keyword-but-generic", "wrong-referent", "invented-location"],
        "pending_case_ids": ["not-reviewed"],
    }


def test_never_reports_a_rate_before_review_or_counts_duplicates():
    assert uptake_summary([("synthetic-1", None)])["rate"] is None
    with pytest.raises(ValueError, match="unique"):
        uptake_summary([("same-case", None), ("same-case", None)])
