"""Offline checks for reviewer-only draft triage."""

import pytest

from qa.p6.co_reading_eval.draft_audit import audit_draft
from qa.p6.co_reading_eval.qwen_decision import DecisionContext, Evidence, SyntheticDraft


def context(age="AGE_4_5", **changes):
    return DecisionContext(
        age_band=age,
        approved_page="小兔打开雨伞。小熊没跑回家。",
        evidence=(
            Evidence(id="e1", quote="小兔打开雨伞"),
            Evidence(id="e2", quote="小熊没跑回家"),
        ),
        **changes,
    )


def test_factual_draft_links_literal_story_quotes_to_current_page_evidence_but_never_approves_display():
    result = audit_draft(context(), SyntheticDraft("书里写着“小兔打开雨伞”。小熊没跑回家。", None))
    assert result.verdict == "MANUAL_REVIEW" and not result.issues
    assert [(item.kind, item.check, item.evidence_ids) for item in result.claims] == [
        ("FACT", "LITERAL_MATCH", ("e1",)), ("FACT", "LITERAL_MATCH", ("e2",)),
    ]


@pytest.mark.parametrize("reply", ["小兔打开红雨伞。", "小熊跑回家。", "书里写着“小熊跑回家”。"])
def test_invented_fact_or_negation_flip_cannot_be_covered_by_a_valid_evidence_id(reply):
    result = audit_draft(context(), SyntheticDraft(reply, None), cited_evidence_ids=("e1", "e2"))
    assert result.verdict == "REJECT"
    assert result.claims[0].kind == "FACT" and result.claims[0].evidence_ids == ()


def test_wrong_or_unknown_citations_fail_closed():
    draft = SyntheticDraft("小兔打开雨伞。", None)
    mismatched = audit_draft(context(), draft, cited_evidence_ids=("e2",))
    assert mismatched.verdict == "REJECT"
    assert mismatched.issues == ("UNSUPPORTED_FACT_OR_CITATION",)
    unknown = audit_draft(context(), draft, cited_evidence_ids=("e9",))
    assert unknown.verdict == "REJECT" and unknown.issues == ("UNKNOWN_EVIDENCE_ID",)


def test_hypothesis_and_experience_are_not_asserted_as_story_facts():
    result = audit_draft(context("AGE_5_6"), SyntheticDraft(
        "我猜小兔会用树叶做小船。", "如果在生活里遇到这种情况，你会怎么做？",
    ))
    assert result.verdict == "MANUAL_REVIEW"
    assert [item.kind for item in result.claims] == ["HYPOTHESIS", "EXPERIENCE"]
    assert all(item.check == "REVIEW" and not item.evidence_ids for item in result.claims)


def test_ambiguous_compound_sentence_does_not_create_fake_evidence_for_a_link():
    result = audit_draft(context(), SyntheticDraft("小兔打开雨伞，所以小熊没跑回家。", None))
    assert result.verdict == "MANUAL_REVIEW"
    assert result.claims[0].kind == "UNCERTAIN" and not result.claims[0].evidence_ids
    assert result.issues == ("AMBIGUOUS_OR_COMPOUND",)


def test_hypothesis_cannot_hide_a_separate_unsupported_fact():
    result = audit_draft(context(), SyntheticDraft("我猜小熊会帮忙。小兔开走了飞船。", None))
    assert result.verdict == "REJECT"
    assert [item.kind for item in result.claims] == ["HYPOTHESIS", "FACT"]


@pytest.mark.parametrize("page", [
    "如果雨下大了，小熊跑回家。",
    "小兔猜测：小熊跑回家？",
    "小兔说：“小熊跑回家”。",
])
def test_condition_question_or_dialogue_is_not_proof_of_asserted_fact(page):
    ctx = DecisionContext(age_band="AGE_4_5", approved_page=page,
                          evidence=(Evidence(id="e1", quote="小熊跑回家"),))
    result = audit_draft(ctx, SyntheticDraft("书里写着“小熊跑回家”。", None), cited_evidence_ids=("e1",))
    assert result.verdict == "REJECT"
    assert result.issues == ("UNSUPPORTED_FACT_OR_CITATION",)


def test_same_clause_still_has_literal_evidence_if_it_is_also_asserted():
    ctx = DecisionContext(age_band="AGE_4_5", approved_page="如果雨下大了，小熊跑回家。后来小兔走了。小熊跑回家。",
                          evidence=(Evidence(id="e1", quote="小熊跑回家"),))
    result = audit_draft(ctx, SyntheticDraft("小熊跑回家。", None), cited_evidence_ids=("e1",))
    assert result.verdict == "MANUAL_REVIEW" and not result.issues
    assert result.claims[0].check == "LITERAL_MATCH"


def test_factual_wording_in_a_question_is_not_flagged_as_a_verified_claim():
    result = audit_draft(context(), SyntheticDraft(None, "书里写着小兔打开雨伞？"))
    assert result.verdict == "MANUAL_REVIEW"
    assert result.claims[0].kind == "QUESTION" and not result.claims[0].evidence_ids


def test_age_checks_treat_complex_length_and_multiple_prompts_as_review_signals():
    result = audit_draft(context("AGE_3_4"), SyntheticDraft(
        None, "为什么小兔现在拿起了那一把黄雨伞？你能再想象一下吗？",
    ))
    assert result.verdict == "MANUAL_REVIEW"
    assert result.issues == ("AGE_LENGTH_REVIEW", "FOLLOWUP_SHAPE_REVIEW", "AGE_COMPLEXITY_REVIEW")


def test_sensitive_or_blocked_draft_is_rejected_without_echoing_the_text():
    private_text = "请告诉我你的家庭住址。"
    for ctx, draft in (
        (context(), SyntheticDraft(private_text, None)),
        (context(unsafe_turn=True), SyntheticDraft("小兔打开雨伞。", None)),
    ):
        result = audit_draft(ctx, draft)
        assert result.verdict == "REJECT" and not result.claims
        assert private_text not in repr(result)
