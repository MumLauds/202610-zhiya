"""Book-owned evidence only. No model, session, or expected-answer data is indexed."""
from dataclasses import dataclass
import re

MAX_PRIOR_PAGES = 4
MAX_PRIOR_TEXT = 4800
RELATIONS = {"HAS_ATTRIBUTE", "HOLDS", "BELONGS_TO", "ACTS_ON"}
STATES = {"ESTABLISHED", "IN_PROGRESS", "CHARACTER_BELIEF", "CHARACTER_IMAGINATION"}
# A retrieval hint only, never a classifier or a filter on what may be a fact.
def terms(text):
    out = set()
    for chunk in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", text.lower()):
        if chunk.isascii():
            out.add(chunk)
        else:
            out.update(chunk[i:i+2] for i in range(len(chunk)-1))
    return out


@dataclass(frozen=True)
class Event:
    page_id: str
    evidence_ref: str
    subject: str
    action: str
    object: str
    status: str
    quote: str
    relation: str | None = None

    def view(self):
        row = {"页码": self.page_id, "证据": self.evidence_ref, "人物或主体": self.subject,
                "动作": self.action, "对象": self.object, "事件状态": self.status, "原文片段": self.quote}
        if self.relation:
            row["关系类型"] = self.relation
        return row


class StoryTimeline:
    def __init__(self, events):
        self.events = events

    def through(self, visible_pages):
        return [event.view() for event in self.events if event.page_id in visible_pages]


class BookEvidenceIndex:
    def __init__(self, book):
        self.pages = {page["id"]: page["text"] for page in book["pages"]}
        self.order = {page_id: i for i, page_id in enumerate(self.pages)}
        events, refs = [], set()
        for page in book["pages"]:
            page_id = page["id"]
            raw = page.get("story_events", [])
            if not isinstance(raw, list) or len(raw) > 8:
                raise ValueError("无效的书页事件数量")
            for n, row in enumerate(raw, 1):
                if not isinstance(row, list) or len(row) not in (5, 6):
                    raise ValueError("无效的书页事件结构")
                subject, action, obj, status, quote, *tag = row
                if (not all(type(v) is str and v.strip() and len(v) <= 180 and
                            not any(ord(c) < 32 or 0xd800 <= ord(c) <= 0xdfff for c in v) for v in row)
                        or status not in STATES or quote not in self.pages[page_id]
                        or (tag and tag[0] not in RELATIONS)):
                    raise ValueError("书页事件必须有本页逐字原文依据")
                ref = f"{page_id}.e{n}"
                if ref in refs:
                    raise ValueError("重复证据编号")
                refs.add(ref)
                events.append(Event(page_id, ref, subject, action, obj, status, quote, tag[0] if tag else None))
        self.timeline = StoryTimeline(events)
        self.search = {p: terms(text) for p, text in self.pages.items()}
        for e in events:
            self.search[e.page_id].update(terms(" ".join((e.subject, e.action, e.object))))

    def available(self, current, seen):
        return {p for p in seen if p in self.order and self.order[p] <= self.order[current]} | {current}

    def context(self, current, seen, query):
        available = self.available(current, seen)
        keys = terms(query)
        candidates = sorted(available - {current}, key=lambda p: (-len(keys & self.search[p]), -self.order[p]))
        selected, remaining = [], MAX_PRIOR_TEXT
        for p in candidates:
            if len(selected) == MAX_PRIOR_PAGES:
                break
            if len(self.pages[p]) <= remaining:
                selected.append(p)
                remaining -= len(self.pages[p])
        selected.sort(key=self.order.__getitem__)
        return {"已读相关前页": [{"页码": p, "原文": self.pages[p]} for p in selected],
                "有原文依据的事件时间线": self.timeline.through(set(selected) | {current})}

