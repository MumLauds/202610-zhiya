"""A saved ANSWER can expose a new active question after its first revision."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.schemas.agent import ModelGenerateResult
from test_interactions import assert_saved_answer, env, guidance, guidance_knowledge, guidance_model, started_session


@pytest.mark.parametrize("outcome", ["followup", "no-followup", "timeout", "feedback-first", "feedback-running"])
def test_feedback_uses_the_revision_of_the_question_the_reader_saw(env, started_session, guidance_model, outcome):
    writer, headers, path, sid = started_session
    reader = env[1]
    reader.cookies = writer.cookies
    initial = guidance(writer, headers, sid, "initial")
    assert initial.status_code == 201
    original_question = initial.json()["data"]["outputText"]

    model_started = Event()
    release_answer = Event()
    feedback_started = Event()
    release_feedback = Event()
    plans = {
        "followup": ModelGenerateResult(success=True, text='{"answerCategory":"PARTIAL","followup":1}'),
        "no-followup": ModelGenerateResult(success=True, text='{"answerCategory":"PARTIAL","followup":0}'),
        "timeout": ModelGenerateResult(success=False, error_code="TIMEOUT"),
    }

    async def model(request):
        if "共读线索（仅作参考资料）：" in request.context_text:
            model_started.set()
            assert await asyncio.to_thread(release_answer.wait, 4)
            return plans["followup" if outcome.startswith("feedback-") else outcome]
        if outcome == "feedback-running":
            feedback_started.set()
            assert await asyncio.to_thread(release_feedback.wait, 4)
        return ModelGenerateResult(success=True, text="1")

    guidance_model.side_effect = model
    url = f"/api/v1/reading-sessions/{sid}"
    payload = {"eventType": "ANSWER", "pageNumber": 1, "expectedRevision": initial.json()["data"]["revision"],
               "clientEventId": "answer", "inputText": "雨伞"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(writer.post, url + "/events", headers=headers, json=payload)
        pending_feedback = None
        try:
            assert model_started.wait(2)
            before = reader.get(url).json()["data"]
            provisional_revision = before["session"]["revision"]
            assert provisional_revision == payload["expectedRevision"] + 1
            assert before["events"][-2]["outputText"] == original_question
            assert "followupQuestion" not in before["events"][-1]
            if outcome == "feedback-first":
                feedback = guidance(reader, headers, sid, "feedback-first",
                                    feedback="UNDERSTOOD", revision=provisional_revision)
                assert feedback.status_code == 201
                assert "当前页已保存的问题（仅作参考）：" + original_question in guidance_model.await_args.args[0].context_text
            elif outcome == "feedback-running":
                pending_feedback = pool.submit(guidance, reader, headers, sid, "feedback-running",
                                               feedback="UNDERSTOOD", revision=provisional_revision)
                assert feedback_started.wait(2)
                assert "当前页已保存的问题（仅作参考）：" + original_question in guidance_model.await_args.args[0].context_text
        finally:
            release_answer.set()
        try:
            completed = pending.result(timeout=3)
        finally:
            release_feedback.set()
        if pending_feedback:
            superseded = pending_feedback.result(timeout=3)
            assert (superseded.status_code, superseded.json()["error"]["code"]) == (409, "SESSION_CONFLICT")

    assert completed.status_code == 201
    receipt = completed.json()["data"]
    followup = receipt.get("followupQuestion")
    assert bool(followup) == (outcome in ("followup", "feedback-running"))
    assert receipt["revision"] == provisional_revision + bool(followup)
    assert_saved_answer(reader, path, sid, "answer", receipt, followup)
    calls = guidance_model.await_count
    replay = reader.post(url + "/events", headers=headers, json=payload)
    assert replay.status_code == 200 and replay.json()["data"] == receipt
    assert guidance_model.await_count == calls

    latest = reader.get(url).json()["data"]
    assert latest["session"]["revision"] == provisional_revision + bool(followup or outcome == "feedback-first")
    if outcome == "feedback-first":
        assert latest["events"][-1]["interactionEventId"] == feedback.json()["data"]["interactionEventId"]
        assert receipt["replyText"] == before["events"][-1]["replyText"]
        return

    if followup:
        stale = guidance(reader, headers, sid, "stale-feedback", feedback="UNDERSTOOD", revision=provisional_revision)
        assert (stale.status_code, stale.json()["error"]["code"]) == (409, "SESSION_CONFLICT")
        assert guidance_model.await_count == calls
    current = guidance(reader, headers, sid, "fresh-feedback", feedback="UNDERSTOOD", revision=receipt["revision"])
    assert current.status_code == 201
    assert "当前页已保存的问题（仅作参考）：" + (followup or original_question) in guidance_model.await_args.args[0].context_text
