import copy
import json
from pathlib import Path
import unittest

from coreading import Catalog, Coreading, SYSTEM_V72, project
from story_v7 import BookEvidenceIndex
from test_coreading import answer, sample

HERE = Path(__file__).parent

def drive(data, case, reply=None):
    wire = []
    def generate(messages):
        wire.append(messages)
        return reply or answer()
    s = Coreading(Catalog(data), generate, "MOCK", version=7)
    session = s.start(case["id"])
    token = session["session"]
    for i, t in enumerate(case["turns"]):
        if t["page"] != session["page_id"]:
            session = s.page(token, t["page"])
        s.turn(token, i)
    return wire

class V7Tests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((HERE/"fixtures_v7.json").read_text(encoding="utf-8"))

    def test_character_attribution_with_page_two_on_page_four(self):
        case = next(c for c in self.data["cases"] if c["id"] == "dev-v6-09")
        content = json.loads(drive(self.data, case)[3][1]["content"])
        self.assertIn("p2", [x["页码"] for x in content["已读相关前页"]])
        p2 = [x for x in content["有原文依据的事件时间线"] if x["页码"] == "p2"]
        beliefs = {x["人物或主体"]: x["对象"] for x in p2 if x["事件状态"] == "CHARACTER_BELIEF"}
        self.assertEqual(beliefs, {"小象":"有人提着小灯", "小狐狸":"水里映着一颗星"})

    def test_every_curated_event_has_exact_original_source(self):
        for book in self.data["books"]:
            index = BookEvidenceIndex(book)
            events = index.timeline.through(set(index.pages))
            self.assertTrue(events)
            for e in events:
                self.assertIn(e["原文片段"], index.pages[e["页码"]])
        bad = copy.deepcopy(self.data["books"][0])
        bad["pages"][2]["story_events"][0][-1] = "不在本页"
        with self.assertRaises(ValueError):
            BookEvidenceIndex(bad)

    def test_future_changes_are_invisible_until_page_visit_and_on_revisit(self):
        book = self.data["books"][0]
        case = {"id":"test-only","book_id":book["id"],"label":"do not emit",
                "turns":[{"page":"p1","child":"看看"}, {"page":"p3","child":"送到哪啦"},
                         {"page":"p4","child":"现在呢"}, {"page":"p3","child":"回头看看"}]}
        self.data["cases"] = [case]
        alt = copy.deepcopy(self.data)
        ending = alt["books"][0]["pages"][3]
        ending.update(text="小兔把信送到白鲸家。", story_events=[["小兔","送到","白鲸家","ESTABLISHED","小兔把信送到白鲸家"]])
        original, alternative = (drive(d, d["cases"][0]) for d in (self.data, alt))
        self.assertEqual(original[:2], alternative[:2])
        self.assertNotEqual(original[2], alternative[2])
        self.assertEqual(original[3], alternative[3])
        before = json.loads(original[1][1]["content"])
        self.assertEqual([x["页码"] for x in before["已读相关前页"]], ["p1"])
        self.assertNotIn("树洞", original[1][1]["content"])
        self.assertNotIn("白鲸家", alternative[1][1]["content"])
        self.assertNotIn("松鼠抱来", original[1][1]["content"])
        self.assertNotIn("现在呢", original[3][1]["content"])
        self.assertIn("IN_PROGRESS", original[1][1]["content"])
        self.assertIn("树洞", original[2][1]["content"])
        self.assertIn("白鲸家", alternative[2][1]["content"])

    def test_only_trusted_book_events_enter_timeline(self):
        data = sample(("one", "three", "one"))
        s = Coreading(Catalog(data), lambda _: answer("我猜这是现实发生的。"), "MOCK", version=7)
        token = s.start("test-only")["session"]
        self.assertEqual(s.turn(token, 0)["kind"],"MOCK")
        self.assertEqual(s.page(token, "three")["page_id"],"three")
        self.assertEqual(s.turn(token, 1)["kind"],"MOCK")
        self.assertEqual(s.page(token, "one")["page_id"],"one")
        first, second, back = drive(data,data["cases"][0],answer("我猜这是现实发生的。"))
        self.assertEqual(first[0]["content"],SYSTEM_V72)
        self.assertIn("我猜", second[1]["content"])
        context = json.loads(back[1]["content"])
        self.assertEqual(context["有原文依据的事件时间线"],[])
        self.assertTrue(all(x["页码"]=="one" for x in context["实际交流历史"]))

    def test_question_format_calibration_is_not_a_semantic_validator(self):
        for text, action, question, reason in (
            ("你看到什么？","ASK_FOLLOWUP","你还看到什么？","QUESTION_FORMAT"),
            ("你是不是看到了星星？","LISTEN",None,"QUESTION_FORMAT"),
            ("先看一会儿，芽芽陪你。","PAUSE",None,None),
            ("我听着呢。","LISTEN",None,None),
            ("你刚刚说的是它。","ASK_FOLLOWUP","你在哪看到的？",None),
            ("原句是“谁提着灯？”","RESPOND",None,"QUESTION_FORMAT"), # known false positive
            ("说说它是不是在等车。","LISTEN",None,None),  # known implicit request
        ):
            with self.subTest(text=text):
                self.assertEqual(project(answer(text, action, question),[])[-1],reason)

if __name__ == "__main__":
    unittest.main()

