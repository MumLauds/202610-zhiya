"""Conservative local triage for free-text drafts from the synthetic Qwen pilot.

No ruling approves text for display. Literal quote matches are not entailment
checks, and unrecognized paraphrases or compound statements remain for review.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.services.reading_agent import ACTOR_WORD, answer_is_unsafe
from qa.p6.co_reading_eval.qwen_decision import DecisionContext, SyntheticDraft

DraftField = Literal["response", "followup"]
Kind = Literal["FACT", "HYPOTHESIS", "EXPERIENCE", "QUESTION", "UNCERTAIN"]
Check = Literal["LITERAL_MATCH", "UNSUPPORTED", "REVIEW"]


@dataclass(frozen=True)
class DraftClaim:
    field: DraftField
    kind: Kind
    check: Check
    evidence_ids: tuple[str, ...]
    text: str = field(repr=False)


@dataclass(frozen=True)
class DraftAudit:
    verdict: Literal["MANUAL_REVIEW", "REJECT"]
    claims: tuple[DraftClaim, ...]
    issues: tuple[str, ...]


_SENTENCES = re.compile(r"[^。！？.!?；;]+[。！？.!?；;]?")
_FACT = re.compile(r"^(?:书里|故事里|这(?:一)?页|当页)(?:写(?:着|到|了)?|说(?:了)?)[：:\s]*")
_EXPERIENCE = re.compile(r"^(?:(?:你)?在生活[里中]|如果(?:你)?在生活[里中]|你有没有遇到过|你有过类似)")
_HYPOTHESIS = re.compile(r"^(?:我猜|也许|大概|可能|如果|假如|想象一下|你猜)")
_MIXED = re.compile(r"[，,、：:]|因为|所以|虽然|但[是也]|因此|然后|而且|其实|已经|事实是")
_LOW_AGE_COMPLEX = re.compile(r"为什么|什么原因|如何|如果|假如|生活[里中]|想象|比较")
_LENGTHS = {"AGE_3_4": (32, 20), "AGE_4_5": (48, 32), "AGE_5_6": (64, 45)}
_OUTER_PUNCTUATION = '。！？.!?；;“”「」" '


def _key(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip(_OUTER_PUNCTUATION)


def _extract(
    field_name: DraftField, text: str, evidence: dict[str, str], cited: set[str] | None,
) -> DraftClaim:
    sentence = unicodedata.normalize("NFKC", text).strip()
    asserted = _FACT.match(sentence)
    body = _key(sentence[asserted.end():] if asserted else sentence)
    if not asserted and _EXPERIENCE.match(body):
        return DraftClaim(field_name, "EXPERIENCE", "REVIEW", (), text)
    if not asserted and _HYPOTHESIS.match(body):
        # The lead alone does not license later factual clauses.
        kind = "UNCERTAIN" if _MIXED.search(body) else "HYPOTHESIS"
        return DraftClaim(field_name, kind, "REVIEW", (), text)
    if sentence.endswith("?"):
        return DraftClaim(field_name, "QUESTION", "REVIEW", (), text)
    matching = tuple(item for item, quote in evidence.items() if quote == body)
    if matching:
        verified = tuple(item for item in matching if cited is None or item in cited)
        return DraftClaim(field_name, "FACT", "LITERAL_MATCH" if verified else "UNSUPPORTED", verified, text)
    if asserted:
        return DraftClaim(field_name, "FACT", "UNSUPPORTED", (), text)
    if not _MIXED.search(body) and (ACTOR_WORD.match(body) or body.startswith(("它", "他", "她"))):
        return DraftClaim(field_name, "FACT", "UNSUPPORTED", (), text)
    return DraftClaim(field_name, "UNCERTAIN", "REVIEW", (), text)


def audit_draft(
    ctx: DecisionContext, draft: SyntheticDraft, *, cited_evidence_ids: Sequence[str] | None = None,
) -> DraftAudit:
    """Audit only synthetic reviewer text against vetted fixture evidence.

    Omitted citations mean inferred matches, not citations claimed by the model.
    Supplied citations must belong to this context and support each literal match.
    """
    fields: tuple[tuple[DraftField, str | None], ...] = (("response", draft.response), ("followup", draft.followup))
    if ctx.unsafe_turn or any(
        text is not None and (not text.isprintable() or answer_is_unsafe(text))
        for _, text in fields
    ):
        return DraftAudit("REJECT", (), ("UNSAFE_OR_NONPRINTABLE",))
    evidence = {item.id: _key(item.quote) for item in ctx.evidence}
    cited = set(cited_evidence_ids) if cited_evidence_ids is not None else None
    if cited is not None and not cited <= evidence.keys():
        return DraftAudit("REJECT", (), ("UNKNOWN_EVIDENCE_ID",))

    claims = tuple(
        _extract(name, part.strip(), evidence, cited)
        for name, text in fields
        for part in _SENTENCES.findall(text or "") if part.strip(_OUTER_PUNCTUATION)
    )
    issues = []
    if not claims:
        issues.append("NO_TEXT_TO_AUDIT")
    if any(claim.check == "UNSUPPORTED" for claim in claims):
        issues.append("UNSUPPORTED_FACT_OR_CITATION")
    if any(claim.kind == "UNCERTAIN" for claim in claims):
        issues.append("AMBIGUOUS_OR_COMPOUND")

    response_limit, followup_limit = _LENGTHS[ctx.age_band]
    if any(len(text or "") > limit for (_, text), limit in zip(fields, (response_limit, followup_limit))):
        issues.append("AGE_LENGTH_REVIEW")
    followup = draft.followup or ""
    if followup and (not followup.endswith(("?", "？")) or sum(followup.count(mark) for mark in "?？") != 1):
        issues.append("FOLLOWUP_SHAPE_REVIEW")
    if ctx.age_band == "AGE_3_4" and _LOW_AGE_COMPLEX.search(followup):
        issues.append("AGE_COMPLEXITY_REVIEW")
    verdict = "REJECT" if "UNSUPPORTED_FACT_OR_CITATION" in issues else "MANUAL_REVIEW"
    return DraftAudit(verdict, claims, tuple(issues))
