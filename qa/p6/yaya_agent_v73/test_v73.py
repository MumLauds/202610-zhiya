
import json
from pathlib import Path
import unittest
from coreading import Catalog, Coreading, SYSTEM_V72, SYSTEM_V73

HERE = Path(__file__).resolve().parent

class V73(unittest.TestCase):
    def test_only_prompt_differs_on_identical_context(self):
        catalog = Catalog.load(HERE/"fixtures_v73_live.json")
        sent = []
        def fake(messages):
            sent.append(messages)
            return {"status": "RETURNED", "text": '{"reply":"我听见啦。","next_action":"LISTEN"}'}
        case = next(iter(catalog.cases))
        self.assertTrue(catalog.cases[case]["turns"][0]["child"])
        for prompt in (None, SYSTEM_V73):
            r = Coreading(catalog, fake, "MOCK", version=7, system_prompt=prompt)
            sid = r.start(case)["session"]
            self.assertEqual(r.turn(sid,0)["kind"],"MOCK")
        self.assertEqual([x[0]["content"] for x in sent],[SYSTEM_V72,SYSTEM_V73])
        self.assertEqual(sent[0][1:], sent[1][1:])
        self.assertFalse({"标准答案","评分","测试目标"} & json.loads(sent[-1][1]["content"]).keys())

    def test_supported_action_and_speaker_in_eligible_pages_only(self):
        original = Catalog.load(HERE/"fixtures_v7.json")
        revised = Catalog.load(HERE/"fixtures_v73_default.json")
        self.assertEqual([[p["text"] for p in b["pages"]] for b in original.books.values()],
                         [[p["text"] for p in b["pages"]] for b in revised.books.values()])
        for name in ("default","diagnostics","live"):
            c = Catalog.load(HERE/f"fixtures_v73_{name}.json")
            r = Coreading(c, None, "MOCK", version=7, system_prompt=SYSTEM_V73)
            facts = r.indexes["forest-light"].context("p2", {"p1","p2"}, "是谁说的")
            rows = {x["证据"]:x for x in facts["有原文依据的事件时间线"] if x["页码"]=="p2"}
            self.assertEqual(rows["p2.e3"]["动作"],"蹲下说")
            self.assertEqual(rows["p2.e3"]["原文片段"],"小狐狸蹲下说")
            self.assertEqual(rows["p2.e2"]["人物或主体"],"小狐狸")
            self.assertEqual(rows["p2.e2"]["事件状态"],"CHARACTER_BELIEF")
            self.assertNotIn("p4",{x["页码"] for x in facts["有原文依据的事件时间线"]})

    def test_sample_uses_complete_diagnostic_sessions(self):
        complete = Catalog.load(HERE/"fixtures_v73_diagnostics.json")
        sample = Catalog.load(HERE/"fixtures_v73_live.json")
        self.assertGreaterEqual(len(complete.cases),18)
        self.assertEqual(sum(bool(t["child"]) for c in sample.cases.values() for t in c["turns"]),14)
        self.assertEqual(sample.books,complete.books)
        self.assertEqual(sample.cases,{k:complete.cases[k] for k in sample.cases})
