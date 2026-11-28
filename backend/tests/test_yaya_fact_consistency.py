"""Offline fact-quality contrasts for one synthetic book.

This scorer labels reviewed literal contrasts in the fixture. An unfamiliar
reply is unscored. This module is not part of the runtime response policy.
"""

import json
import re
from dataclasses import dataclass
from hashlib import sha256

import pytest

from app.model_gateway import ModelReply
from app.services.yaya_agent import BOOK_ANNOTATIONS, BookEvidenceIndex, project

SOURCE = "synthetic-yaya-staging"


@dataclass(frozen=True)
class Contrast:
    defect: str
    evidence_refs: tuple[str, ...]
    question: str
    good: str
    bad: str
    support: str
    contradiction: str


CASES = [
    Contrast("actor", ("1.leaf",), "谁拿起黄色叶子？",
             "拿起黄色叶子的是小兔。", "小熊拿起了黄色叶子。",
             r"小兔.{0,8}拿起.{0,5}黄色叶子|拿起黄色叶子的(?:就)?是小兔",
             r"小熊.{0,8}拿起.{0,5}黄色叶子"),
    Contrast("attribute_owner", ("1.hat",), "小熊戴的帽子是什么颜色？",
             "小熊戴着蓝色帽子。", "小兔戴着蓝色帽子。",
             r"小熊.{0,8}蓝色.{0,6}帽子|小熊.{0,6}帽子.{0,6}蓝色",
             r"小兔.{0,8}蓝色.{0,6}帽子|小熊.{0,8}红色.{0,6}帽子"),
    Contrast("action_object", ("2.windmill",), "小熊把什么插进土里？",
             "小熊把纸风车插进了土里。", "小熊把信箱插进了土里。",
             r"小熊.{0,5}纸风车.{0,5}插进.{0,3}土里",
             r"小熊.{0,5}信箱.{0,5}插进.{0,3}土里"),
    Contrast("invented_motive", ("1.mail",), "松鼠是不是怕信掉了才轻放？",
             "有可能哦，不过这几句没说松鼠为什么轻轻放信。", "松鼠怕信掉了，所以轻轻把信放进信箱。",
             r"有可能.{0,20}(?:没说|没写|没有交代).{0,10}为什么",
             r"松鼠(?:是)?怕信掉了.{0,8}(?:才|所以)"),
    Contrast("future_leak", ("2.spinning",), "纸风车怎么了？",
             "风吹过来，纸风车慢慢转了起来。", "纸风车转起来了，后来松鼠取出信带回了小屋。",
             r"风.{0,20}纸风车.{0,8}转",
             r"松鼠.{0,10}(?:取出信|把信带回)|带回(?:了)?小屋"),
    Contrast("child_imagination_reversed_action", (), "我想让彩虹尾巴拖着一串会唱歌的石头。",
             "彩虹尾巴拖着会唱歌的石头，真有意思呀！", "会唱歌的石头拖着彩虹尾巴，真有意思呀！",
             r"彩虹尾巴.{0,5}拖着.{0,10}会唱歌的石头",
             r"会唱歌的石头.{0,5}拖着.{0,6}彩虹尾巴"),
    Contrast("child_imagination_returns_to_book", (), "我想让彩虹尾巴拖着一串会唱歌的石头。",
             "一串会唱歌的石头，跟着彩虹尾巴一路唱呢！", "哇，会唱歌的石头！小羊想象纸种子是一条彩虹尾巴。",
             r"石头.{0,8}跟着彩虹尾巴.{0,8}唱",
             r"(?:小羊|书里).{0,8}想象.{0,6}纸种子.{0,6}彩虹尾巴"),
    Contrast("imagination_source", ("1.blanket",), "小兔想的被子和我想的雨点帐篷一样吗？",
             "小兔把黄色叶子想成小被子。你让雨点住进帐篷，是自己的想法。",
             "书上写小兔想让雨点住进帐篷，而你想把黄色叶子当成小被子。",
             r"小兔.{0,12}黄色叶子.{0,10}小被子.{0,8}你.{0,12}雨点住进帐篷",
             r"小兔[^。！？]{0,14}雨点住进帐篷|你[^。！？]{0,14}黄色叶子[^。！？]{0,8}小被子"),
]


def score(contrast, rendered_reply):
    if re.search(contrast.contradiction, rendered_reply):
        return contrast.defect
    if re.search(contrast.support, rendered_reply):
        return "PASS"
    return "UNSCORED"


def candidate(text):
    output = project(ModelReply(json.dumps({"reply": text, "next_action": "RESPOND"}, ensure_ascii=False)), ())
    # The production protocol checks displayability. Fact evaluation inspects what would be shown.
    assert output.decision == "UNCHANGED"
    return output.reply


@pytest.mark.parametrize("contrast", CASES, ids=lambda case: case.defect)
def test_offline_synthetic_fact_consistency_detects_each_negative(contrast):
    book = BOOK_ANNOTATIONS[SOURCE]
    index = BookEvidenceIndex((int(page), row["text"], SOURCE) for page, row in book.items())
    projected = index.context(2, [1, 3, 2], contrast.question)
    visible = {fact["evidenceRef"]: fact for fact in projected["currentFacts"] + projected["visitedTimeline"]}
    assert set(contrast.evidence_refs) <= visible.keys()
    assert all(fact["evidenceLevel"] == "reviewed_structured"
               and fact["quote"] in book[str(fact["page"])]["text"] for fact in visible.values())
    assert not any(fact["page"] == 3 for fact in visible.values())
    assert score(contrast, candidate(contrast.good)) == "PASS"
    assert score(contrast, candidate(contrast.bad)) == contrast.defect
    assert score(contrast, candidate("我们一起继续看绘本吧。")) == "UNSCORED"


def test_cross_page_reviewed_evidence_and_sentence_evidence_remain_distinct(monkeypatch):
    book = BOOK_ANNOTATIONS[SOURCE]
    for row in book.values():
        assert row["text_sha256"] == sha256(row["text"].encode("utf-8")).hexdigest()

    materials = BookEvidenceIndex([(1, book["1"]["text"], SOURCE), (2, "风吹过来了。", "unannotated")]).context(
        2, [1, 2], "书里小兔想到了什么？"
    )
    assert materials["currentEvidenceLevel"] == "sentence" and materials["currentFacts"] == []
    assert materials["currentQuotes"] == ["风吹过来了。"]
    timeline = {event["evidenceRef"]: event for event in materials["visitedTimeline"]}
    assert timeline["1.blanket"]["state"] == "CHARACTER_IMAGINATION"
    assert timeline["1.hat"]["subject"] == "小熊" and timeline["1.hat"]["attribute"] == "蓝色"
    assert timeline["1.leaf"]["subject"] == "小兔" and timeline["1.leaf"]["object"] == "黄色叶子"
    assert timeline["1.mail"]["subject"] == "松鼠" and timeline["1.mail"]["object"] == "信"

    updated = book["1"]["text"] + "风来了。"
    monkeypatch.setitem(book["1"], "text", updated)
    stale = BookEvidenceIndex([(1, updated, SOURCE), (2, book["2"]["text"], SOURCE)]).context(2, [1, 2], "")
    assert stale["currentEvidenceLevel"] == "reviewed_structured"
    assert all(set(event) == {"page", "quote", "evidenceLevel"} and event["evidenceLevel"] == "sentence"
               for event in stale["visitedTimeline"])
