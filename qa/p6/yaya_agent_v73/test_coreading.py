"""Protocol, provenance and state invariants on invented data; not Qwen quality tests."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from coreading import Catalog, Coreading, FALLBACK, HISTORY_LIMIT, SYSTEM_V6, project
from demo import DeadlineGenerator, Demo, serve
from evidence import EvidenceRecorder


def sample(pages=("one","one","two","one"), words=None):
    words = words or [f"合成发言{i}" for i in range(len(pages))]
    return {"synthetic_only":True,"books":[{"id":"new-book","title":"虚构积木","age":"AGE_5_6",
            "pages":[{"id":k,"text":text,"facts":["不许传给模型的展示备注"]} for k,text in
                     [("one","桌面放着一块积木。"),("two","积木边出现一枚指环。"),("three","小鼓响了一声。")]]}],
            "cases":[{"id":"test-only","book_id":"new-book","label":"不许传给模型的案例标签",
                      "turns":[{"page":p,"child":w} for p,w in zip(pages,words)]}]}


def answer(text="我听见了。", action="LISTEN", q=None):
    return {"status":"RETURNED","text":json.dumps({"reply":text,"next_action":action,"followup_question":q},ensure_ascii=False)}


def reader(replies, data=None):
    captured = []
    def generate(messages):
        captured.append(messages)
        return replies[len(captured)-1]
    service = Coreading(Catalog(data or sample()), generate, "MOCK")
    sid = service.start("test-only")["session"]
    return service, sid, captured


class CoreadingTests(unittest.TestCase):
    def test_parameters_case_labels_facts_and_future_text_never_enter_messages(self):
        catalog = sample(pages=("one",), words=["我想搭圆圆的。"])
        variant = json.loads(json.dumps(catalog))
        case = variant["cases"][0]
        case.update(id="other-id", label="另一个本地评分答案")
        first = case["turns"][0]; first["child"] = "我想搭尖尖的。"
        variant["books"][0]["pages"][0]["facts"] = ["案外标注"]
        inputs = []
        for data in (catalog,variant):
            def capture(messages):
                inputs.append(messages)
                return answer()
            service = Coreading(Catalog(data), capture, "MOCK")
            response = service.turn(service.start(data["cases"][0]["id"])["session"], 0)
            self.assertEqual((response["kind"],response["independent_review"]), ("MOCK","NOT_PERFORMED"))
        m1,m2=inputs
        self.assertEqual(m1[0],m2[0]); self.assertEqual(m1[0]["content"],SYSTEM_V6)
        self.assertNotIn("claims", m1[0]["content"])
        contexts=[json.loads(m[1]["content"]) for m in inputs]
        self.assertNotEqual(contexts[0].pop("本轮孩子说"),contexts[1].pop("本轮孩子说"))
        self.assertEqual(*contexts); self.assertIsNone(contexts[0]["已读相邻前页"])
        self.assertEqual(contexts[0]["当前书页"]["原文"], catalog["books"][0]["pages"][0]["text"])
        wire=json.dumps(inputs,ensure_ascii=False)
        for needle in ("展示备注","案例标签","另一个本地评分答案","案外标注","test-only","other-id","小鼓","指环"):
            self.assertNotIn(needle,wire)

    def test_page_transition_pending_quota_and_revisit_are_explicit(self):
        ask = answer("我听到了。","ASK_FOLLOWUP","你还想说什么？")
        service, sid, capture = reader([ask, answer("明白。"), ask, ask])
        r = service.turn(sid,0)
        self.assertEqual(service.turn(sid,0),r); self.assertEqual(len(capture),1)
        self.assertEqual(service.turn(sid,1)["actual_question"],"你还想说什么？")
        with self.assertRaises(ValueError): service.turn(sid,2)
        with self.assertRaises(ValueError): service.page(sid,"three")
        self.assertEqual(service.page(sid,"two")["visit_id"],2)
        self.assertIsNone(service.turn(sid,2)["actual_question"])
        ctx = json.loads(capture[-1][1]["content"])
        self.assertEqual(ctx["已读相邻前页"]["页码"],"one")
        self.assertEqual(ctx["实际交流历史"][-1]["芽芽实际说"],"明白。")
        self.assertEqual(ctx["实际交流历史"][-1]["页码"],"one")
        self.assertEqual(ctx["剩余可追问次数"],2)
        self.assertEqual(service.page(sid,"one")["visit_id"],3)
        with self.assertRaises(ValueError): service.turn(sid,0)
        r=service.turn(sid,3)
        self.assertIsNone(r["actual_question"]); self.assertEqual(r["kind"],"MOCK")
        self.assertEqual(json.loads(capture[-1][1]["content"])["剩余可追问次数"],2)

    def test_skipping_an_unread_page_does_not_expose_intermediate_text(self):
        service,sid,capture=reader([answer(),answer()],sample(("one","three")))
        service.turn(sid,0); service.page(sid,"three");service.turn(sid,1)
        ctx=json.loads(capture[-1][1]["content"])
        self.assertIsNone(ctx["已读相邻前页"])
        self.assertNotIn("指环", json.dumps(ctx,ensure_ascii=False))

    def test_recent_actual_visible_history_only_and_session_isolation(self):
        service,sid,seen=reader([answer(f"留档发言 {i}") for i in range(8)],sample(("one",)*7))
        for n in range(7): service.turn(sid,n)
        history=json.loads(seen[-1][1]["content"])["实际交流历史"]
        self.assertEqual([h["芽芽实际说"] for h in history],[f"留档发言 {i}" for i in range(2,6)])
        self.assertEqual(len(history),HISTORY_LIMIT)
        new=service.start("test-only")["session"]
        self.assertNotEqual(sid,new)
        service.turn(new,0)
        self.assertEqual(json.loads(seen[-1][1]["content"])["实际交流历史"],[])

    def test_continue_after_pause_listen_and_end_topic_without_moving_page(self):
        service,sid,seen=reader([answer("一起歇歇。","PAUSE"),answer("我在。","LISTEN"),
                                 answer("那就先聊到这里。","CLOSE_TOPIC"),answer("你又想到一个。","RESPOND")],
                                 sample(("one",)*4))
        states = []
        for n in range(4):
            r=service.turn(sid,n); self.assertEqual(r["page_id"],"one")
            self.assertIsNone(r["actual_question"]); states.append(r["state"])
        self.assertEqual(states,["PAUSE","LISTEN","END_TOPIC","CONTINUE"])
        self.assertEqual(json.loads(seen[-1][1]["content"])["前一轮交流状态"],"END_TOPIC")

    def test_one_visit_quota_and_duplicate_have_no_accepted_or_hidden_question(self):
        q1="它在什么位置？";q2="还有什么想说？"
        service,sid,seen=reader([answer("我听见了。","ASK_FOLLOWUP",q) for q in [q1,q1,q2,"新问题？"]],
                                sample(("one",)*4))
        receipts = [service.turn(sid,i) for i in range(4)]
        self.assertEqual([r["fallback_reason"] for r in receipts],[None,"REPEATED_QUESTION",None,"FOLLOWUP_QUOTA"])
        self.assertEqual(receipts[3]["draft"]["followup_question"],"新问题？")
        self.assertIsNone(receipts[3]["spoken"]["followup_question"])
        self.assertIsNone(receipts[2]["actual_question"])
        self.assertEqual(json.loads(seen[2][1]["content"])["实际交流历史"][-1]["芽芽实际说"],FALLBACK)

    def test_empty_does_not_consume_question_or_call_and_invalid_output_stays_out_of_history(self):
        q="木头在哪儿？"
        service,sid,seen=reader([answer("看一看。","ASK_FOLLOWUP",q),
                                 answer("不该出现的疑问？"),answer()],
                                 sample(("one",)*4, ["搭起来","", "这里","再听听"]))
        service.turn(sid,0)
        r=service.turn(sid,1)
        self.assertEqual((len(seen), r["kind"],r["spoken"],r["actual_question"]),(1,"NO_SUBMISSION",None,q))
        r=service.turn(sid,2)
        self.assertEqual((r["actual_question"],r["fallback_reason"]),(q,"QUESTION_FORMAT"))
        service.turn(sid,3)
        self.assertNotIn("不该出现",seen[-1][1]["content"])
        self.assertIn(FALLBACK, seen[-1][1]["content"])

    def test_protocol_strictness_and_transport_failures_are_distinct(self):
        invalid = ['{"reply":"好","next_action":"PAUSE","claims":[]}',
                   '{"reply":"好","next_action":"PAUSE","followup_question":"什么？"}',
                   '{"reply":"好","next_action":"PAUSE","next_action":"LISTEN"}',
                   '{"reply":"好","next_action":"TURN_PAGE"}',
                   '{"reply":"好","next_action":"ASK_FOLLOWUP"}']
        for raw in invalid:
            with self.subTest(raw=raw):
                self.assertEqual(project({"status":"RETURNED","text":raw},[])[-1],"INVALID_DRAFT")
        for status in ("TIMEOUT","API_ERROR","DEMO_DEADLINE","UPSTREAM_BUSY","PARSE_ERROR"):
            self.assertEqual(project({"status":status},[])[-1],status)
        self.assertEqual(project({**answer(),"stop_type":"length"},[])[-1],"INCOMPLETE_OUTPUT")

    def test_late_raw_draft_kept_separate_from_committed_receipt(self):
        gate,entered=threading.Event(),threading.Event()
        def late(_):
            entered.set(); gate.wait(4); return answer("只可离线看的迟到草稿。")
        with tempfile.TemporaryDirectory() as d:
            record=EvidenceRecorder(Path(d), "MOCK", version=6)
            worker=DeadlineGenerator(late, .05)
            service=Coreading(Catalog(sample()),worker,"MOCK",record)
            sid=service.start("test-only")["session"]
            try:
                r=service.turn(sid,0)
                self.assertEqual(r["fallback_reason"],"DEMO_DEADLINE"); self.assertIsNone(r["draft"])
                self.assertTrue(entered.wait(2))
                self.assertEqual(service.turn(sid,1)["provider_status"],"UPSTREAM_BUSY")
            finally:
                gate.set();worker.close();record.close()
            events=[json.loads(l) for l in record.path.read_text().splitlines()]
            first={e["event"]:e for e in events if e["turn_number"]==0}
            self.assertEqual(set(first),{"request","upstream","server_receipt"})
            self.assertEqual(first["upstream"]["parsed_model_json"]["reply"],"只可离线看的迟到草稿。")
            self.assertEqual(first["server_receipt"]["receipt"]["spoken"]["reply"],FALLBACK)
            self.assertTrue(all(e["mode"]=="MOCK" and e["case_id"]=="test-only" for e in events))
            second={e["event"]:e for e in events if e["turn_number"]==1}
            self.assertNotIn("upstream",second)
            self.assertNotIn("只可离线",second["request"]["messages"][1]["content"])

    def test_frozen_fixture_gate_and_hash(self):
        data=sample()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"f.json"
            raw=json.dumps(data).encode()
            path.write_bytes(raw)
            Catalog.load(path,hashlib.sha256(raw).hexdigest())
            with self.assertRaises(ValueError): Catalog.load(path,"0"*64)
            data["synthetic_only"]=False; path.write_text(json.dumps(data))
            with self.assertRaises(ValueError): Catalog.load(path,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_v6_http_same_origin_rejects_raw_text_or_arbitrary_page(self):
        service,sid,seen=reader([answer(),{"status":"RETURNED","text":'{"reply":"\\ud800","next_action":"LISTEN"}'}])
        app=serve(0,Demo(lambda _:answer()),"MOCK",v6=service)
        thread=threading.Thread(target=app.serve_forever,daemon=True)
        thread.start()
        base=f"http://127.0.0.1:{app.server_port}"; http=build_opener(ProxyHandler({}))
        def req(route, data=None, origin=base):
            r=Request(base+route,data=json.dumps(data).encode() if data is not None else None,
                      headers={"Content-Type":"application/json","Origin":origin})
            with http.open(r,timeout=5) as res: return json.load(res)
        try:
            self.assertEqual(req("/v6/catalog")["mode"],"MOCK")
            state=req("/v6/start",{"case_id":"test-only"})
            for route,payload,origin,code in [
                ("/v6/turn",{"session":sid,"number":0},"http://not-this-origin",403),
                ("/v6/turn",{"session":sid,"number":0,"child":"任意文字"},base,400),
                ("/v6/turn",{"session":sid,"number":True},base,400),
                ("/v6/page",{"session":sid,"page_id":"three"},base,400)]:
                with self.assertRaises(HTTPError) as cm: req(route,payload,origin)
                self.assertEqual(cm.exception.code,code)
            self.assertEqual(len(seen),0)
            r=req("/v6/turn",{"session":state["session"],"number":0})
            self.assertEqual((r["child"],r["kind"]),(sample()["cases"][0]["turns"][0]["child"],"MOCK"))
            rejected=req("/v6/turn",{"session":state["session"],"number":1})
            self.assertEqual((rejected["kind"],rejected["fallback_reason"]),("FALLBACK","INVALID_DRAFT"))
            self.assertEqual(rejected["spoken"]["reply"],FALLBACK)
        finally:
            app.shutdown();app.server_close();thread.join(2)
