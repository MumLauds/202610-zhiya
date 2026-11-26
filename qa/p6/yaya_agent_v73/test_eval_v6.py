"""Contract tests with generated local objects, never submitted for grading."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from eval_v6 import reconcile, review_forms, merge_votes, sha, read, UNKNOWN


def synthetic_schema_sample():
    # LIVE is a schema discriminator tested here, not a claim of any real request.
    meta=dict(session_id="unit-session",case_id="unit-only",page_id="p1",visit_id=1,turn_number=0,version=6,mode="LIVE")
    context={"本轮孩子说":"嗯","当前书页":{"原文":"虚构页","页码":"p1"},"此刻实际待答问题":None,"年龄":"测试","实际交流历史":[]}
    msgs=[dict(role="system",content="unit"),dict(role="user",content=json.dumps(context,ensure_ascii=False))]
    draft={"reply":"<script>单测</script>","next_action":"LISTEN","followup_question":None}
    req={**meta,"event":"request","messages":msgs,"system_prompt_sha256":sha(b"unit"),"book_text":"虚构页","child":"嗯"}
    up={**meta,"event":"upstream","status":"RETURNED","request_body":{"messages":deepcopy(msgs),"model":"test"},
        "usage":{"prompt_tokens":10,"completion_tokens":20},"call_ms":5,"text":json.dumps(draft),"parsed_model_json":draft}
    receipt=dict(session="unit-session",case_id=meta["case_id"],page_id="p1",visit_id=1,number=0,mode="LIVE",kind="MODEL",
                 child="嗯",server_ms=8,state="LISTEN",spoken=draft,fallback_reason=None,independent_review="NOT_PERFORMED")
    sr={**meta,"event":"server_receipt","receipt":receipt}
    p={**meta,"number":0,"child":"嗯","ms":12,"category":"MODEL_WITHIN_5S","kind":"MODEL","source":"RETURNED",
       "server_receipt":deepcopy(receipt),"displayed_reply":draft["reply"],"displayed_followup":None,"fallback_reason":None}
    exp=dict(schema="yaya-v6-page-timings-v1",mode="LIVE",session_id="unit-session",case_id=meta["case_id"],timings=[p])
    return [req,up,sr],exp


class EvalV6Test(unittest.TestCase):
    def test_actual_timings_usage_and_no_default_price(self):
        e,p=synthetic_schema_sample()
        stats=reconcile(e,[p])["summary"]
        self.assertEqual([stats[k]["p50_ms"] for k in ("api_latency","server_latency","foreground_page_latency")],[5,8,12])
        self.assertIsNone(stats["estimated_usd_for_observed_live_calls"])
        self.assertEqual(reconcile(e,[p],(Decimal(1),Decimal(2)))["summary"]["estimated_usd_for_observed_live_calls"],"0.00005")
        self.assertIsNone(stats["api_latency"]["p95_ms"])
        del e[1]["usage"]["completion_tokens"]
        self.assertIsNone(reconcile(e,[],(Decimal(1),Decimal(2)))["summary"]["estimated_usd_for_observed_live_calls"])

    def test_reject_mode_receipt_context_and_duplicate(self):
        for bad in ("mode","page","messages","duplicate","child","fake_display","receipt","digest"):
            with self.subTest(bad):
                e,p=synthetic_schema_sample()
                if bad=="mode":p["mode"]="MOCK"
                if bad=="page":p["timings"][0]["visit_id"]+=1
                if bad=="messages":e[1]["request_body"]["messages"][1]["content"]="{}"
                if bad=="duplicate":e.append(e[1])
                if bad=="child":e[0]["child"]="换话"
                if bad=="fake_display":p["timings"][0]["displayed_reply"]="不是模型所说"
                if bad=="receipt":p["timings"][0]["server_receipt"]["kind"]="FALLBACK"
                if bad=="digest":e[0]["system_prompt_sha256"]="bad"
                with self.assertRaises(ValueError):reconcile(e,[p])

    def test_wire_body_hash_and_actual_server_history(self):
        e,p=synthetic_schema_sample()
        wire=json.dumps({"choices":[{"message":{"content":e[1]["text"]}}],"usage":e[1]["usage"]},ensure_ascii=False)
        e[1].update(raw_body=wire,raw_body_sha256=sha(wire.encode()))
        reconcile(e,[p])
        e[1]["raw_body"]+=" "
        with self.assertRaisesRegex(ValueError,"指纹"):reconcile(e,[p])
        e[1]["raw_body"]=wire
        u=json.loads(e[0]["messages"][1]["content"]);u["实际交流历史"]=[{"孩子":"别的会话里的发言"}]
        e[0]["messages"][1]["content"]=json.dumps(u);e[1]["request_body"]["messages"]=deepcopy(e[0]["messages"])
        with self.assertRaisesRegex(ValueError,"上下文历史"):reconcile(e,[p])

    def test_local_fallback_distinct_from_model_draft(self):
        e,p=synthetic_schema_sample()
        p["timings"][0].update(kind="FALLBACK",category="OVER_5S",ms=5100,server_receipt=None,
                                source="BROWSER_DEADLINE",fallback_reason="BROWSER_DEADLINE",displayed_reply="页面备用句")
        out=reconcile(e,[p])
        self.assertEqual(out["rows"][0]["server_receipt"]["kind"],"MODEL")
        self.assertIsNotNone(out["rows"][0]["parsed_draft"])
        self.assertEqual(out["summary"]["page_categories"],{"OVER_5S":1})
        self.assertEqual(out["summary"]["server_fallbacks"],0)

    def test_missing_source_is_not_zero_or_false_success(self):
        e,_=synthetic_schema_sample()
        report=reconcile([e[0]],[])
        self.assertEqual(report["summary"]["no_upstream_result"],1)
        self.assertIsNone(report["summary"]["usage"]["prompt_tokens"]["observed_sum"])
        self.assertEqual(report["summary"]["foreground_page_latency"]["n"],0)
        self.assertEqual(report["rows"][0]["independent_review"],"UNKNOWN")

    def test_mock_cannot_get_quality_scores_or_cost(self):
        e,p=synthetic_schema_sample()
        for row in e:row["mode"]="MOCK"
        e[2]["receipt"].update(kind="MOCK",mode="MOCK")
        p["mode"]="MOCK";p["timings"][0].update(kind="MOCK",mode="MOCK",category="MOCK_WITHIN_5S",server_receipt=e[2]["receipt"])
        r=reconcile(e,[p]); self.assertEqual(r["summary"]["observed_live_api_calls"],0)
        with self.assertRaises(ValueError):review_forms(r,None)

    def test_blank_is_not_an_api_call(self):
        e,_=synthetic_schema_sample();sr=e[-1]
        sr["receipt"].update(kind="NO_SUBMISSION",spoken=None,child="",provider_status="NOT_CALLED")
        r=reconcile([sr],[])
        self.assertEqual(r["summary"]["skipped_empty"],1)
        self.assertEqual(r["summary"]["requested_turns"],0)
        self.assertEqual(r["summary"]["observed_live_api_calls"],0)

    def test_independent_blind_key_and_missing_votes(self):
        e,p=synthetic_schema_sample()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);review_forms(reconcile(e,[p]),root)
            form=(root/"盲评-甲.html").read_text(encoding="utf-8")
            self.assertNotIn("unit-session",form);self.assertNotIn("<script>单测</script>",form)
            key=read(root/"blind-key-private.json")
            def fake(rater):
                return {"reviewer":rater,"batch":key["batch"],"cases":{"01":{
                    "scores":["3","4",UNKNOWN,"3","5","4"],"turns":{"1":[None]*4},"note":"仅单测"}}}
            a,b=fake("甲"),fake("乙")
            with self.assertRaises(ValueError):merge_votes(key,[a,a])
            result=merge_votes(key,[a,b])
            self.assertIsNone(result["human_rubric"]["上下文承接"]["mean_of_pairs"])
            self.assertEqual(result["human_rubric"]["引导质量"]["equal_scores"],1)
            b["batch"]="another"
            with self.assertRaises(ValueError):merge_votes(key,[a,b])
