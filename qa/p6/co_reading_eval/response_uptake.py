"""Summarize human scores for synthetic short-answer review; no text auto-grading."""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class UptakeRating:
    expression_understood: bool
    naturally_continued: bool
    no_unverified_claim: bool


def uptake_summary(
    cases: Iterable[tuple[str, UptakeRating | None]],
) -> dict[str, int | float | list[str] | None]:
    """One adjudicated rating per case; unreviewed cases are not passes or fails."""
    passed = []
    failed = []
    pending = []
    seen = set()
    for case_id, rating in cases:
        if not case_id or case_id in seen:
            raise ValueError("each review case must have a unique nonempty ID")
        seen.add(case_id)
        if rating is None:
            pending.append(case_id)
        elif rating.expression_understood and rating.naturally_continued and rating.no_unverified_claim:
            passed.append(case_id)
        else:
            failed.append(case_id)
    reviewed = len(passed) + len(failed)
    return {
        "passed": len(passed),
        "reviewed": reviewed,
        "total": len(seen),
        "rate": len(passed) / reviewed if reviewed else None,
        "failed_case_ids": failed,
        "pending_case_ids": pending,
    }
