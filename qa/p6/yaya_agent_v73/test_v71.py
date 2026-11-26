
import json
import unittest
from coreading import project
from story_v7 import BookEvidenceIndex

class V71Test(unittest.TestCase):
    def test_attribute_stays_with_its_subject_and_respects_reading_progress(self):
        index = BookEvidenceIndex({"pages":[
            {"id":"p1","text":"冬冬抱着一只布鹅。布鹅有两条橙色腿。","story_events":[
                ["冬冬","抱着","布鹅","ESTABLISHED","冬冬抱着一只布鹅","HOLDS"],
                ["布鹅","有","两条橙色腿","ESTABLISHED","布鹅有两条橙色腿","HAS_ATTRIBUTE"]]},
            {"id":"p2","text":"他们走到门口。"},
            {"id":"p3","text":"橙色腿上绑了绿带子。","story_events":[
                ["布鹅","绑着","绿带子","ESTABLISHED","橙色腿上绑了绿带子","HAS_ATTRIBUTE"]]}
        ]})
        ctx = index.context("p2", {"p1","p2","p3"}, "谁的腿是橙色的")
        rows = next(v for k,v in ctx.items() if "时间线" in k)
        self.assertEqual([row["关系类型"] for row in rows],["HOLDS","HAS_ATTRIBUTE"])
        self.assertEqual(index.timeline.events[1].subject,"布鹅")
        self.assertIn("布鹅",rows[1].values())
        self.assertEqual(rows[1]["原文片段"],"布鹅有两条橙色腿")
        self.assertNotIn("绿带子",json.dumps(ctx,ensure_ascii=False))

    def test_quote_must_be_from_same_page(self):
        with self.assertRaises(ValueError):
            BookEvidenceIndex({"pages":[{"id":"p1","text":"纸偶站在门口","story_events":[
                ["纸偶","是","紫色","ESTABLISHED","纸偶是紫色","HAS_ATTRIBUTE"]]}]})

    def project(self,reply,action="LISTEN",q=None,used=None,enabled=True):
        doc={"reply":reply,"next_action":action,"followup_question":q}
        spoken, raw, reason = project({"status":"RETURNED","text":json.dumps(doc,ensure_ascii=False)},used or [],reported_quotes=enabled)
        self.assertEqual(raw,doc)
        return spoken,reason

    def test_reported_source_question_does_not_force_fallback(self):
        reply="原文写的是「你听到了吗？」。我们安静听一会儿。"
        self.assertEqual(self.project(reply)[0]["reply"],reply)
        self.assertEqual(self.project(reply, enabled=False)[1],"QUESTION_FORMAT")

    def test_open_question_and_duplicate_still_trigger_fallback(self):
        self.assertEqual(self.project("你是不是也听到了呢？")[1],"QUESTION_FORMAT")
        self.assertEqual(self.project("你看到了什么？","ASK_FOLLOWUP","你看到了什么？")[1],"QUESTION_FORMAT")
        self.assertEqual(self.project("书里写的是「谁在那儿？」。你猜是谁？")[1],"QUESTION_FORMAT")

    def test_confirmed_quote_does_not_release_quota(self):
        reply="你刚才问的是「谁在那里？」。"
        q="你想再看一眼吗？"
        self.assertIsNone(self.project(reply,"ASK_FOLLOWUP",q)[1])
        self.assertEqual(self.project(reply,"ASK_FOLLOWUP",q,["第一问？","第二问？"])[1],"FOLLOWUP_QUOTA")
