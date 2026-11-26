import copy
import json
import unittest
from v72_fact_audit import DATA, evidence, lines, load, score
from story_v7 import BookEvidenceIndex

class OfflineFactReview(unittest.TestCase):
    def test_candidate_is_separate_attributed_and_future_safe(self):
        baseline=load("v72_fact_book_sources.json")
        rows=evidence(baseline)
        candidate=load("v72_fact_candidate_books.json")
        self.assertEqual("PROPOSAL_ONLY_PENDING_HUMAN_REVIEW",candidate["status"])
        changes={(r["book"],r["ref"]) for r in rows if r["candidate"] is not None}
        self.assertEqual(len(changes),3)
        cidx={b["id"]:BookEvidenceIndex(b) for b in candidate["books"]}
        for b in baseline["books"]:
            before=BookEvidenceIndex(b)
            after=cidx[b["id"]]
            self.assertEqual(before.pages,after.pages)
            self.assertEqual([e.evidence_ref for e in before.timeline.events],[e.evidence_ref for e in after.timeline.events])
            for x,y in zip(before.timeline.events,after.timeline.events):
                self.assertEqual(x!=y,(b["id"],x.evidence_ref) in changes)
        forest=cidx["forest-light"]
        self.assertEqual("自己的猜测",next(e.object for e in forest.timeline.events if e.evidence_ref=="p2.e3"))
        ctx=forest.context("p2",{"p1","p2","p3","p4"},"小狐狸说了什么")
        self.assertNotIn("萤火虫",json.dumps(ctx,ensure_ascii=False))
    def test_diagnostic_is_blind_and_unsigned_results_are_not_scores(self):
        inputs=lines("v72_fact_blind_inputs.jsonl")
        for row in inputs:
            payload=json.loads(row["messages"][1]["content"])
            self.assertEqual(set(payload),{"child","draft","read_pages"})
        report=score()
        self.assertEqual(report["prediction_count"],18)
        self.assertEqual(report["human_signed_count"],0)
        self.assertTrue(all(v["rate"] is None for v in report["human_reference_metrics"].values()))
        self.assertEqual(score(prediction_rows=[])["desk_vs_provisional_only"]["fact_miss"]["denominator"],0)
    def test_raw_hash_and_span_fail_closed(self):
        reviews=lines("v72_fact_blind_desk.jsonl")
        wrong=copy.deepcopy(reviews); wrong[0]["input_sha256"]="0"*64
        with self.assertRaises(ValueError):score(prediction_rows=wrong)
        wrong=copy.deepcopy(reviews); next(x for x in wrong if x["findings"])["findings"][0]["span"]="不在草稿中"
        with self.assertRaises(ValueError):score(prediction_rows=wrong)
