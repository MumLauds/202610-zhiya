import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

import httpx

import demo
from evidence import EvidenceRecorder, build_report
from provider import BailianProvider, Settings, PRIMARY_MODEL


class EvidenceTest(unittest.TestCase):
    def test_paired_mock_transport_retains_wire_data_and_final_display(self):
        requests=[]
        replies=[]
        def respond(request):
            sent=json.loads(request.content)
            requests.append(sent)
            content=json.dumps({"reply":f"模拟草稿 {len(requests)}。","next_action":"LISTEN"},ensure_ascii=False)
            res=httpx.Response(200,headers={"x-request-id":f"mock-{len(requests)}"},
                json={"id":f"mock-{len(requests)}","model":PRIMARY_MODEL,
                      "choices":[{"message":{"content":content},"finish_reason":"stop"}],
                      "usage":{"prompt_tokens":101,"completion_tokens":20}})
            replies.append(res.text)
            return res
        settings=Settings("mock-secret-do-not-store",
                          "https://ws-mock.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        with TemporaryDirectory() as tmp, BailianProvider(settings,allow_message=lambda _:True,
                                                           _transport=httpx.MockTransport(respond)) as provider:
            recorder=EvidenceRecorder(Path(tmp)/"run","MOCK")
            bounded=demo.DeadlineGenerator(lambda m:provider.complete(m,PRIMARY_MODEL))
            service=demo.Demo(bounded,recorder)
            try:
                ids=[service.start("dev-06",0)["session"] for _ in range(2)]
                for i,session in enumerate(ids,1):
                    self.assertEqual(service.turn(session,choice_id=str(i))["kind"],"MODEL")
            finally:
                bounded.close()
                recorder.close()
            raw=recorder.path.read_text()
            self.assertNotIn(settings.api_key,raw)
            self.assertNotEqual(*ids)
            contents=[json.loads(r["messages"][1]["content"]) for r in requests]
            self.assertEqual([c.pop("本轮孩子说") for c in contents],[
                "我想让这只小船飞上天。","等等，书里真的写它变成船了吗？",
            ])
            for sent,context in zip(requests,contents):
                self.assertEqual(set(context),{"年龄","虚构绘本","本页证据","实际交流历史",
                                               "此刻实际待答问题","剩余可追问次数"})
                self.assertEqual(context["本页证据"],{"p1.e1":"一粒木纽扣安静地躺在井沿。"})
                prompt=json.dumps(sent["messages"],ensure_ascii=False)
                for phrase in ("最后一行没有提到船","本页没有描述木纽扣变成小船",
                               "继续想象","核实原文","branch_id","choice_id","标准答案","预期行为"):
                    self.assertNotIn(phrase,prompt)
            self.assertEqual(*contents)
            self.assertEqual(requests[0]["messages"][0],requests[1]["messages"][0])
            for r in requests: r.pop("messages")
            self.assertEqual(*requests)
            samples={"mode":"MOCK","timings":[
                {"card":"dev-06","choice_id":"1","session_id":ids[0],"ms":4716,
                 "kind":"FALLBACK","fallback_reason":"BROWSER_DEADLINE","displayed_reply":"浏览器备用回复"},
                {"card":"dev-06","choice_id":"2","session_id":ids[1],"ms":850,"kind":"MODEL",
                 "displayed_reply":"模拟草稿 2。"}]}
            report=build_report([json.loads(line) for line in raw.splitlines()],samples)
            a,b=report["trials"]
            self.assertEqual(report["observed_upstream_results"],{"A":1,"B":1})
            self.assertTrue(report["paired_context_and_parameters_match"])
            self.assertEqual(report["input_tokens"],{"sum":202,"reported_rows":2})
            self.assertEqual([a["provider_response"]["raw_body"],b["provider_response"]["raw_body"]],replies)
            self.assertEqual(a["reply"],"模拟草稿 1。")
            self.assertEqual(a["server_receipt"]["kind"],"MODEL")
            self.assertEqual(a["final_display"]["displayed_reply"],"浏览器备用回复")
            self.assertEqual(a["independent_review"]["status"],"PENDING")
            self.assertEqual(b["page_ms"],850)

    def test_late_response_is_recorded_but_never_replaces_fallback(self):
        release=threading.Event()
        def complete(_):
            release.wait(2)
            return {"status":"RETURNED","text":'{"reply":"迟到的模拟草稿。","next_action":"LISTEN"}'}
        with TemporaryDirectory() as tmp:
            recorder=EvidenceRecorder(Path(tmp)/"run","MOCK")
            bounded=demo.DeadlineGenerator(complete,.02)
            service=demo.Demo(bounded,recorder)
            try:
                session=service.start("dev-06",0)["session"]
                self.assertEqual(service.turn(session,choice_id="1")["fallback_reason"],"DEMO_DEADLINE")
            finally:
                release.set()
                bounded.close()
                recorder.close()
            out=build_report([json.loads(l) for l in recorder.path.read_text().splitlines()],
                             {"mode":"MOCK","timings":[]})["trials"][0]
            self.assertEqual(out["reply"],"迟到的模拟草稿。")
            self.assertEqual(out["server_receipt"]["spoken"]["reply"],demo.FALLBACK)
            self.assertIsNone(out["final_display"])
            self.assertIsNone(out["page_ms"])

    def test_provider_does_not_retain_key_escaped_inside_a_model_draft(self):
        key="fake-secret-for-mock"
        content='{"reply":"'+''.join("\\u%04x"%ord(c) for c in key)+'","next_action":"LISTEN"}'
        def reply(_):
            return httpx.Response(200,json={"choices":[{"message":{"content":content}}]})
        cfg=Settings(key,"https://ws-mock.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        with BailianProvider(cfg,allow_message=lambda _:True,_transport=httpx.MockTransport(reply)) as p:
            r=p.complete([{"role":"system","content":"synthetic only"},{"role":"user","content":"mock"}],PRIMARY_MODEL)
        self.assertEqual(r["error_type"],"CREDENTIAL_ECHO_BLOCKED")
        self.assertIsNone(r["raw_body"])
        self.assertIsNone(r["text"])
