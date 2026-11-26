"""v7.2 projection checks; not a claim of real model quality."""
import json
import unittest
from pathlib import Path

from coreading import Catalog, Coreading, project_v72

HERE = Path(__file__).resolve().parent


def render(reply, action="RESPOND", q=None, used=()):
    doc = {"reply":reply,"next_action":action,"followup_question":q}
    decision = {}
    spoken, draft, reason = project_v72(
        {"status":"RETURNED","text":json.dumps(doc,ensure_ascii=False)},used,decision)
    assert draft == doc
    return spoken, reason, decision


class DialogueQuality(unittest.TestCase):
    def test_natural_exclamation_and_quoted_questions_are_not_prompts(self):
        for reply in (
            "哇，真的吗？原来你发现叶子像小船。",
            "是吗？我在这里陪你。",
            "原句是「谁提着灯？」。我们接着看。",
            "你说的是「雨停了吗？」。我听见了。",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(render(reply)[0]["reply"], reply)

    def test_inline_real_ask_cannot_be_hidden_in_reply(self):
        for reply in (
            "哇，真的吗？你看到了什么？",
            "还没呢。你猜信要送给谁呀？",
            "我们先歇歇。告诉我你看到的颜色。",
            "你觉得那是谁",
            "「你看到了什么？」",
        ):
            with self.subTest(reply=reply):
                for action in ("RESPOND","LISTEN","PAUSE","CLOSE_TOPIC"):
                    self.assertEqual(render(reply,action)[1], "QUESTION_FORMAT")
        q = "你看见了谁？"
        self.assertEqual(render("我听到了。" + q,"ASK_FOLLOWUP",q)[1],"QUESTION_FORMAT")

    def test_invalid_followup_preserves_original_body_and_does_not_ask(self):
        reply = "这页写的是小兔带着信出门。"
        rows = [(("你想看看信吗？",),"你想看看信吗？","REPEATED_QUESTION"),
                (("一？","二？"),"你想看看信吗？","FOLLOWUP_QUOTA"),
                ((),"谁拿了信？它去哪儿？","FOLLOWUP_QUESTION_SHAPE")]
        for used,q,problem in rows:
            with self.subTest(problem=problem):
                spoken, reason, decision = render(reply,"ASK_FOLLOWUP",q,used)
                self.assertIsNone(reason)
                self.assertEqual(spoken,{"reply":reply,"next_action":"RESPOND","followup_question":None})
                self.assertEqual(decision,{"kind":"REPLY_ONLY","reason":problem})

    def test_live_shape_doesnt_edit_mixed_body(self):
        reply = "还没呢，书里说小兔才刚带着信出门。你猜它要去哪儿？"
        spoken, reason, decision = render(reply)
        self.assertEqual(reason,"QUESTION_FORMAT")
        self.assertNotEqual(spoken["reply"],reply.split("。")[0]+"。")
        self.assertEqual(decision["reason"],"BODY_INTERROGATIVE_UNRESOLVED")

    def test_actual_conversation_history_and_quota_only_use_displayed_question(self):
        cat = Catalog.load(HERE/"fixtures_v72_regression.json")
        for turn in cat.cases["dev-v6-05"]["turns"][:3]:
            turn["page"] = "p1"
        events = iter([
            ("我听见了。","ASK_FOLLOWUP","你发现什么？"),
            ("我记住你改的想法了。","ASK_FOLLOWUP","你发现什么？"),
            ("好，咱们静静地看。","PAUSE",None),
            ("我们继续。","RESPOND",None),
        ])
        seen=[]
        def generate(messages):
            seen.append(json.loads(messages[1]["content"]))
            reply,action,q=next(events)
            return {"status":"RETURNED","text":json.dumps(
                {"reply":reply,"next_action":action,"followup_question":q},ensure_ascii=False)}
        reader=Coreading(cat,generate,"MOCK",version=7)
        sid=reader.start("dev-v6-05")["session"]
        for i,t in enumerate(cat.cases["dev-v6-05"]["turns"][:4]):
            reader.page(sid,t["page"])
            receipt=reader.turn(sid,i)
            if i==1:
                self.assertEqual(receipt["display_decision"]["kind"],"REPLY_ONLY")
                self.assertIsNone(receipt["spoken"]["followup_question"])
                self.assertIs(reader.turn(sid,i),receipt)
            if i==2:
                self.assertEqual(receipt["state"],"PAUSE")
            if i==3:
                self.assertEqual(receipt["state"],"CONTINUE")
        self.assertEqual(len(seen),4)
        self.assertIsNone(seen[2]["此刻实际待答问题"])
        self.assertEqual(seen[2]["实际交流历史"][-1]["芽芽实际说"],"我记住你改的想法了。")
        self.assertLessEqual(len(reader.sessions[sid].used),1)


if __name__ == "__main__":
    unittest.main()
