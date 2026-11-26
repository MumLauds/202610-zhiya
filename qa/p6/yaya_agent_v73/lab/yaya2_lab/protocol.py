"""Strict draft gate for synthetic human review. No semantic verifier or child display."""

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import unicodedata


_ACTIONS = {"RESPOND", "LISTEN", "ASK_FOLLOWUP", "CLOSE_TOPIC", "HANDOFF"}
_MODES = {"BOOK_FACT", "BOOK_INFERENCE", "CHILD_VIEW", "HYPOTHETICAL"}
_BOOK_MODES = {"BOOK_FACT", "BOOK_INFERENCE"}


class Verdict(Enum):
    PASS = "pass"
    UNSUPPORTED = "unsupported"
    UNDECLARED_CLAIM = "undeclared_claim"
    UNDECLARED_PREMISE = "undeclared_premise"
    REPEATED = "repeated"
    UNSUITABLE_SHAPE = "unsuitable_shape"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FixtureReport:
    """Trusted in-process fixture/human labels, never supplied by the generator.

    A label must cover a whole field, including unreported claims and implied premises.
    Reply PASS means independently complete, with no concealed request for an answer.
    Follow-up PASS means exactly one genuine question, not an instruction to the app.
    Missing labels/safety or any unresolved whole-draft issue cannot authorize review.
    """

    context_id: str
    draft_sha256: str
    safe: bool
    reply: Verdict
    followup: Verdict | None = None


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    reply: str | None = None
    question: str | None = None
    next_action: str | None = None
    proposed_action: str | None = None


def draft_digest(raw: str) -> str:
    """Exact fixture identity, local-only even if the source data were private."""
    return sha256(raw.encode("utf-8")).hexdigest()


def _unique_object(pairs: list) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonstandard JSON constant")


def _line(value: object, max_chars: int) -> bool:
    return (
        type(value) is str and 0 < len(value) <= max_chars and bool(value.strip())
        and not any(unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in value)
    )


def parse_draft(raw: str, *, protocol: str = "A") -> dict:
    if protocol not in ("A", "B"):
        raise ValueError("unknown protocol")
    if type(raw) is not str or len(raw) > 4096 or len(raw.encode("utf-8")) > 4096:
        raise ValueError("draft byte budget")
    draft = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    required = {"reply", "next_action"} | ({"claims"} if protocol == "A" else set())
    if type(draft) is not dict or not required <= draft.keys() <= required | {"followup_question"}:
        raise ValueError("draft keys")
    reply, action, claims = draft["reply"], draft["next_action"], draft.get("claims", [])
    question = draft.get("followup_question")
    if not _line(reply, 100) or not isinstance(action, str) or action not in _ACTIONS:
        raise ValueError("reply or action")
    if action == "ASK_FOLLOWUP":
        if not _line(question, 60):
            raise ValueError("missing or invalid follow-up")
    elif question is not None:
        raise ValueError("follow-up/action conflict")
    if type(claims) is not list or len(claims) > 4:
        raise ValueError("claims budget")
    for claim in claims:
        if type(claim) is not dict or claim.keys() != {"target", "span", "mode", "evidence_refs"}:
            raise ValueError("claim keys")
        target, span, mode, refs = (claim[k] for k in ("target", "span", "mode", "evidence_refs"))
        if target not in ("reply", "followup_question") or not _line(span, 100):
            raise ValueError("claim target or span")
        line = reply if target == "reply" else question
        first = line.find(span) if line is not None else -1
        if first < 0 or line.find(span, first + 1) >= 0:
            raise ValueError("missing or ambiguous claim span")
        if not isinstance(mode, str) or mode not in _MODES:
            raise ValueError("claim mode")
        if type(refs) is not list or not all(_line(ref, 64) for ref in refs) or len(set(refs)) != len(refs):
            raise ValueError("claim references")
        if mode not in _BOOK_MODES and refs:
            raise ValueError("attributed views/hypotheses cannot claim book refs")
    return draft


def review(
    raw: str,
    *,
    context_id: str,
    protocol: str = "A",
    report: FixtureReport | None = None,
    allowed_evidence: frozenset[str] = frozenset(),
    remaining: int = 2,
    used_questions: tuple[str, ...] = (),
) -> Outcome:
    """Review only. Callers bind context_id to the immutable local visit snapshot."""
    try:
        draft = parse_draft(raw, protocol=protocol)
    except (ValueError, RecursionError):
        return Outcome("SAVED_FALLBACK", "invalid_draft")
    reply, proposed = draft["reply"], draft["next_action"]
    question = draft.get("followup_question")
    if not context_id or not isinstance(report, FixtureReport):
        return Outcome("SAVED_FALLBACK", "no_fixture_review")
    if report.context_id != context_id or report.draft_sha256 != draft_digest(raw):
        return Outcome("SAVED_FALLBACK", "fixture_review_mismatch")
    if report.safe is not True:
        return Outcome("SAVED_FALLBACK", "whole_draft_safety_not_cleared")
    if proposed == "HANDOFF":
        return Outcome("SAVED_FALLBACK", "local_handoff_required")
    if (
        not isinstance(report.reply, Verdict)
        or (question is not None and not isinstance(report.followup, Verdict))
        or (question is None and report.followup is not None)
    ):
        return Outcome("SAVED_FALLBACK", "fixture_coverage_mismatch")

    invalid_evidence_targets = {
        claim["target"] for claim in draft.get("claims", [])
        if claim["mode"] in _BOOK_MODES
        and (not claim["evidence_refs"] or not allowed_evidence.issuperset(claim["evidence_refs"]))
    }
    if report.reply is not Verdict.PASS:
        return Outcome("SAVED_FALLBACK", f"reply_{report.reply.value}")
    if "reply" in invalid_evidence_targets:
        return Outcome("SAVED_FALLBACK", "reply_evidence_scope")
    if question is None:
        return Outcome("REVIEW_ONLY", "fixture_pass", reply, next_action=proposed, proposed_action=proposed)
    if report.followup in {Verdict.UNKNOWN, Verdict.UNSUITABLE_SHAPE}:
        return Outcome("SAVED_FALLBACK", "followup_unresolved")

    if type(remaining) is not int or not 0 <= remaining <= 2:
        return Outcome("SAVED_FALLBACK", "invalid_visit_quota")
    if report.followup is not Verdict.PASS:
        rejection = report.followup.value
    elif "followup_question" in invalid_evidence_targets:
        rejection = "evidence_scope"
    elif remaining <= 0:
        rejection = "quota"
    elif any(question.strip() == used.strip() for used in used_questions):
        rejection = "repeated"
    else:
        return Outcome("REVIEW_ONLY", "fixture_pass", reply, question, proposed, proposed)
    return Outcome("REVIEW_ONLY", f"followup_dropped_{rejection}", reply,
                   next_action="RESPOND", proposed_action=proposed)
