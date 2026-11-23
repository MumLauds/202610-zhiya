"""Executable examples of the proposed summary contract. No production import or model call."""

from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError


class ProposedTurn(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    questionIntent: Literal["OBSERVATION", "PLOT", "CAUSAL", "EMOTION", "IMAGINATION", "LIFE_CONNECTION", "UNKNOWN"]
    answerRelation: Literal["SUPPORTED", "PARTIAL", "CONFLICT", "UNCERTAIN", "IMAGINATIVE", "UNKNOWN"]
    locallyAcknowledged: bool
    evidenceVerified: bool


def project(events, visit):
    """Test-only reference projection from already-authorized synthetic event metadata."""
    by_event = {}
    for event in events:
        if event["visit"] != visit or not event["completed"] or event["blocked"]:
            continue
        signal = {key: event[key] for key in ProposedTurn.model_fields}
        if not signal["evidenceVerified"] and signal["answerRelation"] in {"SUPPORTED", "PARTIAL", "CONFLICT"}:
            signal["answerRelation"] = "UNKNOWN"
        by_event.setdefault(event["event_id"], ProposedTurn(**signal).model_dump())
    return [by_event[event_id] for event_id in sorted(by_event)[-2:]]


ACK = dict(questionIntent="OBSERVATION", answerRelation="UNKNOWN", locallyAcknowledged=True, evidenceVerified=False)
VISIT = (1, 2, 3, 4)  # session, version, page and page arrival, kept local


def test_unverified_local_ack_does_not_become_an_answer_or_leak_text():
    event = dict(ACK, visit=VISIT, completed=True, blocked=False, event_id=10,
                 rawAnswer="虚构标记原答", transcript="虚构标记转录", replyText="虚构标记承接",
                 answerRelation="SUPPORTED")
    assert project([event], VISIT) == [ACK]


def test_summary_keeps_only_two_finished_safe_distinct_turns_in_current_visit():
    def event(event_id, **values):
        return ACK | dict(event_id=event_id, visit=VISIT, completed=True, blocked=False) | values

    assert project([
        event(18, questionIntent="PLOT"), event(12, questionIntent="EMOTION"),
        event(15, questionIntent="CAUSAL", evidenceVerified=True, answerRelation="CONFLICT"),
        event(18, questionIntent="PLOT"), event(7, visit=(1, 2, 3, 3)), event(25, visit=(2, 2, 3, 4)),
        event(28, visit=(1, 3, 3, 4)), event(29, visit=(1, 2, 4, 4)),
        event(30, blocked=True), event(40, completed=False),
    ], VISIT) == [
        ACK | dict(questionIntent="CAUSAL", evidenceVerified=True, answerRelation="CONFLICT"),
        ACK | dict(questionIntent="PLOT"),
    ]


@pytest.mark.parametrize("extra", [
    {"rawAnswer": "虚构原答"}, {"transcript": "虚构转录"}, {"evidenceIds": ["e1"]},
    {"questionIntent": "虚构原答"}, {"answerRelation": "对"}, {"locallyAcknowledged": "true"},
    {"evidenceVerified": 1},
])
def test_only_closed_values_and_exact_fields_can_be_exported(extra):
    with pytest.raises(ValidationError):
        ProposedTurn(**(ACK | extra))
