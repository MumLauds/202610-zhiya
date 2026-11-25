import json
import io
import socketserver
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import demo


def result(reply,action="LISTEN",followup=None):
    return {"status":"RETURNED","text":json.dumps({"reply":reply,"next_action":action,"followup_question":followup})}


class DemoTest(unittest.TestCase):
    def test_real_prompt_contains_fixed_input_and_own_visible_history(self):
        captured=[]
        def generate(messages):
            captured.append(messages)
            return result("我先听一听。","ASK_FOLLOWUP","你在想什么？") if len(captured)==1 else result("你刚改了想法。")
        service=demo.Demo(generate)
        s=service.start("dev-04",0)["session"]
        self.assertEqual(service.turn(s,0)["kind"],"MODEL")
        self.assertEqual(service.turn(s,1)["actual_question"],"你在想什么？")
        msg=captured[-1]
        self.assertTrue(msg[0]["content"].startswith(demo.SYSTEM_B))
        self.assertNotIn("claims",msg[0]["content"])
        context=json.loads(msg[1]["content"])
        self.assertEqual(context["本轮孩子说"],"不，我想是等车")
        self.assertEqual(context["实际交流历史"][0]["芽芽实际说"],"我先听一听。")
        with self.assertRaises(ValueError): service.turn(s,1)
        self.assertEqual(len(captured),2)

    def test_timeout_and_late_model_never_enter_history(self):
        release=threading.Event()
        def model(_):
            release.wait(2)
            return result("迟到的模型草稿不应展示。")
        worker=demo.DeadlineGenerator(model,.02)
        try:
            service=demo.Demo(worker)
            s=service.start("dev-04",0)["session"]
            self.assertEqual(service.turn(s,0)["provider_status"],"DEMO_DEADLINE")
            self.assertEqual(service.turn(s,1)["provider_status"],"UPSTREAM_BUSY")
        finally:
            release.set()
            worker.close()
        self.assertTrue(all(h["diagnostic_reply"]==demo.FALLBACK for h in service.sessions[s][2]))

    def test_empty_turns_never_call_provider(self):
        seen=[]
        service=demo.Demo(lambda m: seen.append(m) or result("好。"))
        s=service.start("dev-11",1)["session"]
        for n in (0,1): service.turn(s,n)
        with self.assertRaises(ValueError): service.turn(s,2)
        self.assertEqual(len(seen),2)

    def test_invalid_structure_and_old_claims_fail(self):
        self.assertRaises(ValueError, demo.draft_fields,'{"reply":"听着呢","next_action":"LISTEN","claims":[]}')
        self.assertRaises(ValueError, demo.draft_fields,'{"reply":"听着呢","next_action":"LISTEN","followup_question":"还想说吗？"}')

    def test_fallback_reason_distinguishes_transport_from_draft(self):
        samples = [
            ({"status":"DEMO_DEADLINE"}, "DEMO_DEADLINE"),
            ({"status":"API_ERROR","error_type":"HTTP_STATUS","raw_body":"not for diagnostics"}, "API_ERROR"),
            ({"status":"TIMEOUT"}, "TIMEOUT"),
            ({"status":"RETURNED","text":"not JSON"}, "INVALID_DRAFT"),
            ({"status":"RETURNED","stop_type":"length"}, "INCOMPLETE_OUTPUT"),
        ]
        for upstream, reason in samples:
            with self.subTest(reason=reason):
                service=demo.Demo(lambda _: upstream)
                token=service.start("dev-10",0)["session"]
                receipt=service.turn(token,0)
                self.assertEqual((receipt["kind"],receipt["fallback_reason"]),("FALLBACK",reason))
                self.assertGreaterEqual(receipt["server_ms"],0)
                self.assertEqual(receipt["spoken"]["reply"],demo.FALLBACK)
                self.assertNotIn("not for diagnostics",json.dumps(receipt))

    def test_reviewed_double_question_falls_back_and_cannot_enter_history(self):
        draft=result("原来是等车呀！邮筒边有车经过吗？","ASK_FOLLOWUP","灰鸭会坐什么颜色的车去送信呢？")
        service=demo.Demo(lambda _: draft)
        token=service.start("dev-04",0)["session"]
        receipt=service.turn(token,0)
        self.assertEqual((receipt["kind"],receipt["fallback_reason"]),("FALLBACK","QUESTION_FORMAT"))
        saved=service.sessions[token][2][0]
        self.assertEqual(saved["diagnostic_reply"],demo.FALLBACK)
        self.assertIsNone(saved["diagnostic_followup"])

    def test_question_format_allows_listening_and_one_followup(self):
        examples=[
            (result("好，我陪着你。"),"MODEL"),
            (result("我看到一滴雨珠。","ASK_FOLLOWUP","你看到什么了吗？"),"MODEL"),
            (result("你也看到它了吗?"),"FALLBACK"),
            (result("这里有雨珠。","ASK_FOLLOWUP","它是什么？你看到了吗？"),"FALLBACK"),
        ]
        for draft, kind in examples:
            with self.subTest(draft=draft):
                service=demo.Demo(lambda _: draft)
                token=service.start("dev-10",0)["session"]
                self.assertEqual(service.turn(token,0)["kind"],kind)

    def test_client_disconnect_during_reply_leaves_server_available(self):
        write=socketserver._SocketWriter.write
        def disconnect_on_cards_body(writer, data):
            if bytes(data).startswith(b'{"mode":'):
                raise BrokenPipeError
            return write(writer, data)

        with demo.serve(0,demo.Demo(demo.mock_generate),"MOCK") as server:
            bg=threading.Thread(target=server.serve_forever,daemon=True); bg.start()
            host=f"http://127.0.0.1:{server.server_port}"
            try:
                diagnostics=io.StringIO()
                from contextlib import redirect_stderr
                with redirect_stderr(diagnostics), patch.object(socketserver._SocketWriter,"write",disconnect_on_cards_body):
                    import http.client
                    with self.assertRaises(http.client.IncompleteRead):
                        with urllib.request.urlopen(host+"/cards",timeout=2) as response:
                            response.read()
                self.assertIn("客户端在收取回复前断开",diagnostics.getvalue())
                self.assertNotIn("Traceback",diagnostics.getvalue())
                with urllib.request.urlopen(host+"/cards",timeout=2) as response:
                    self.assertEqual(json.load(response)["mode"],"MOCK")
            finally: server.shutdown(); bg.join()

    def test_replay_draft_and_receipt_are_distinct(self):
        src=demo.REPLAY
        self.assertIn("好，我就陪着你。", [r["draft"]["reply"] for r in src])
        self.assertIn("嗯，书里确实是这样写的。", [r["draft"]["reply"] for r in src])
        self.assertTrue(all(r["receipt"]["status"]=="SAVED_FALLBACK" and r["receipt_text"]=="我在这里陪你。" for r in src))

    def test_http_same_origin_and_no_arbitrary_text(self):
        with demo.serve(0,demo.Demo(demo.mock_generate),"MOCK") as server:
            bg=threading.Thread(target=server.serve_forever,daemon=True); bg.start()
            host=f"http://127.0.0.1:{server.server_port}"
            def post(path,body,origin=host):
                return json.load(urllib.request.urlopen(urllib.request.Request(host+path,
                    data=json.dumps(body).encode(),headers={"Content-Type":"application/json","Origin":origin})))
            try:
                self.assertIn("课程演示",urllib.request.urlopen(host+"/").read().decode())
                self.assertEqual(json.load(urllib.request.urlopen(host+"/cards"))["mode"],"MOCK")
                s=post("/start",{"id":"dev-04","arm":0})["session"]
                for payload,origin,status in [
                    ({"session":s,"number":0,"text":"非合成用户输入"},host,400),
                    ({"session":s,"number":0},"https://example.invalid",403)]:
                    with self.assertRaises(urllib.error.HTTPError) as err: post("/turn",payload,origin)
                    self.assertEqual(err.exception.code,status)
                self.assertEqual(post("/turn",{"session":s,"number":0})["kind"],"MODEL")
            finally: server.shutdown(); bg.join()

    def test_nut_source_text_and_presenter_note_are_separate_on_cards(self):
        with demo.serve(0,demo.Demo(demo.mock_generate),"MOCK") as server:
            bg=threading.Thread(target=server.serve_forever,daemon=True); bg.start()
            try:
                url=f"http://127.0.0.1:{server.server_port}/cards"
                with urllib.request.urlopen(url,timeout=2) as response:
                    card,=[c for c in json.load(response)["cards"] if c["id"]=="dev-06"]
                self.assertEqual(card["book_text"],"一粒木纽扣安静地躺在井沿。")
                self.assertEqual(card["page"],{"p1.e1":card["book_text"]})
                self.assertEqual(card["demo_evidence"],"本页没有描述木纽扣变成小船。")
            finally: server.shutdown(); bg.join()

    def test_nut_choice_over_http_only_changes_child_text_and_resets_history(self):
        captured=[]
        def fake_generate(messages):
            captured.append(messages)
            return result(f"模拟回复{len(captured)}，不作质量判断。")

        with demo.serve(0,demo.Demo(fake_generate),"MOCK") as server:
            bg=threading.Thread(target=server.serve_forever,daemon=True); bg.start()
            host=f"http://127.0.0.1:{server.server_port}"
            def post(path,body):
                req=urllib.request.Request(host+path,data=json.dumps(body).encode(),
                    headers={"Content-Type":"application/json","Origin":host})
                with urllib.request.urlopen(req,timeout=2) as response:
                    return json.load(response)
            try:
                with urllib.request.urlopen(host+"/cards",timeout=2) as response:
                    cards=json.load(response)["cards"]
                self.assertEqual([(c["id"],c["arm"]) for c in cards if c["id"]!="dev-06"],
                    [(k,i) for k in demo.FIXTURE if k!="dev-06" for i in (0,1)])
                card,= [c for c in cards if c["id"]=="dev-06"]
                children=["我想让这只小船飞上天。","等等，书里真的写它变成船了吗？"]
                self.assertEqual([c["text"] for c in card["choices"]],children)
                def start(): return post("/start",{"id":card["id"],"arm":card["arm"]})["session"]
                first=start()
                for bad in ({"number":0},{"choice_id":"3"},{"choice_id":"1","text":"任意文本"}):
                    with self.assertRaises(urllib.error.HTTPError) as err:
                        post("/turn",{"session":first,**bad})
                    self.assertEqual(err.exception.code,400)
                a=post("/turn",{"session":first,"choice_id":"1"})
                with self.assertRaises(urllib.error.HTTPError) as err:
                    post("/turn",{"session":first,"choice_id":"2"})
                self.assertEqual(err.exception.code,400)
                second=start()
                self.assertNotEqual(first,second)
                b=post("/turn",{"session":second,"choice_id":"2"})
                self.assertEqual([a["child"],b["child"]],children)
                self.assertEqual(len(captured),2)
                self.assertEqual(captured[0][0],captured[1][0])
                self.assertEqual(captured[0][0]["content"],demo.SYSTEM)
                first_context,second_context=(json.loads(m[1]["content"]) for m in captured)
                self.assertEqual([first_context.pop("本轮孩子说"),second_context.pop("本轮孩子说")],children)
                self.assertEqual(first_context,second_context)
                self.assertEqual(first_context["本页证据"],card["page"])
                hist,=second_context["实际交流历史"]
                self.assertEqual(hist["孩子"],card["preset"][0]["child"])
                self.assertEqual(hist["芽芽实际说"],card["preset"][0]["diagnostic_reply"])
                self.assertNotIn(a["spoken"]["reply"],captured[1][1]["content"])
            finally: server.shutdown(); bg.join()


if __name__=="__main__": unittest.main()
