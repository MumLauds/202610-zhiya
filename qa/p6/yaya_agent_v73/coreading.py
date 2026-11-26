"""Isolated synthetic multi-page reader. No story-specific dialogue rules or production imports."""
from collections import deque
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
import secrets
import sys
import threading
from pathlib import Path
from time import perf_counter

from story_v7 import BookEvidenceIndex
from provider import PRIMARY_MODEL, _strict_json
LAB_PATH = str(Path(__file__).resolve().parent / "lab")
if LAB_PATH not in sys.path: sys.path.insert(0, LAB_PATH)
from run_batch import SYSTEM_B
from yaya2_lab.protocol import parse_draft


# One protocol for all books, all turns, including a future, independently authored holdout.
SYSTEM_V6 = SYSTEM_B.replace("LISTEN、ASK_FOLLOWUP", "LISTEN、PAUSE、ASK_FOLLOWUP") + (
    "PAUSE表示暂歇，CLOSE_TOPIC只收束话题，不结束阅读。"
    "当前页和已读前页的原文是书证；交流历史不是书证。区分孩子现在与先前的意思。"
    "不确定就如实说明。每轮最多一个问题，只放在followup_question。"
)
SYSTEM_V7 = SYSTEM_B.replace("LISTEN、ASK_FOLLOWUP", "LISTEN、PAUSE、ASK_FOLLOWUP") + (
    "PAUSE表示歇歇，CLOSE_TOPIC只结束话题，不结束阅读。"
    "书页原文和带出处的事件按阅读进度提供；正在做不等于做完，角色猜测也不是已证实事实。"
    "实际交流历史是孩子与芽芽的表达，不是书证。涉及书里是否发生了什么，先看对应原文；没有读到就诚实说明。"
    "人物、物件、动作和属性按原文对应，不能把相邻描述当成同一对象。只把原文支持的细节说成书里的事实；缺证据不等于证实其反面。"
    "如是推测或想象，可以自然表示这不是书里已发生的事。承接、倾听和引用不用追问；正文不要求孩子作答，最多在followup_question放一个真正要孩子回答的问题。"
)
SYSTEM_V72 = SYSTEM_V7 + (
    "不要把可能的动机、心情、归属或动作对象说成已证实。谁做了什么、作用在什么对象上、"
    "动作到哪一步，都以对应原文为准；如果只是在推想，用自己的自然语气说明。"
    "非索答的感叹、引用可放在正文；要孩子回答的问题只能另列。"
)
SYSTEM_V73 = SYSTEM_V7 + (
    "孩子接着想象时，跟上他刚添的想法，可以只接一句或倾听，不必重新讲书或总结。"
    "孩子问书里写了什么时，直接按已读原文回答。刚改的想法以孩子最新的表达为准。"
    "书里的人物、孩子和芽芽各自说过或想过什么，不要合在一起；未经原文证实的属性、动作和动机仍只是推测。"
    "非索答的感叹、引用可放在正文；要孩子回答的问题只能另列。"
)
FALLBACK = "这次没能接上，我们先一起看看这一页吧。"
STATES = {"RESPOND": "CONTINUE", "ASK_FOLLOWUP": "CONTINUE", "LISTEN": "LISTEN",
          "PAUSE": "PAUSE", "CLOSE_TOPIC": "END_TOPIC"}
HISTORY_LIMIT = 4


def _line(value, limit):
    return (type(value) is str and bool(value.strip()) and len(value) <= limit
            and not any(ord(c) < 32 or 0xd800 <= ord(c) <= 0xdfff for c in value))


def _indexed(items, limit):
    if type(items) is not list or not 1 <= len(items) <= limit:
        raise ValueError("合成资料数量无效")
    out = {}
    for item in items:
        if type(item) is not dict or not _line(item.get("id"), 80) or item["id"] in out:
            raise ValueError("合成资料标识无效或重复")
        out[item["id"]] = item
    return out


class Catalog:
    def __init__(self, data):
        if type(data) is not dict or data.get("synthetic_only") is not True:
            raise ValueError("仅允许已确认全部为虚构绘本与合成发言的资料")
        self.books = _indexed(data.get("books"), 100)
        self.cases = _indexed(data.get("cases"), 500)
        for book in self.books.values():
            if not _line(book.get("title"), 120) or not _line(book.get("age"), 80):
                raise ValueError("缺少书名或目标年龄")
            for p in _indexed(book.get("pages"), 100).values():
                if not _line(p.get("text"), 1200):
                    raise ValueError("书页须为有限长度的单行原文")
                if type(p.get("facts")) is not list or not all(_line(f, 400) for f in p["facts"]):
                    raise ValueError("须单列仅供本地审阅的事实")
        for case in self.cases.values():
            book = self.books.get(case.get("book_id"))
            if book is None or not _line(case.get("label"), 160):
                raise ValueError("剧本缺少绘本或标题")
            pages = {p["id"] for p in book["pages"]}
            turns = case.get("turns")
            if type(turns) is not list or not 1 <= len(turns) <= 64:
                raise ValueError("剧本轮数无效")
            for t in turns:
                if (type(t) is not dict or t.get("page") not in pages
                        or (t.get("child") != "" and not _line(t.get("child"), 300))):
                    raise ValueError("剧本中有无效书页或发言")

    @classmethod
    def load(cls, path, expected_sha256=None):
        path = Path(path)
        if path.stat().st_size > 2_000_000:
            raise ValueError("合成资料文件过大")
        raw = path.read_bytes()
        if expected_sha256 is not None and (
            type(expected_sha256) is not str or not re.fullmatch("[a-fA-F0-9]{64}", expected_sha256)
            or sha256(raw).hexdigest() != expected_sha256.lower()
        ):
            raise ValueError("合成资料与指定的冻结 SHA-256 不一致")
        return cls(_strict_json(raw.decode("utf-8")))

    def public(self, mode):
        # Evaluation annotations are local, not copied into generator inputs.
        return {"mode": mode, "books": list(self.books.values()), "cases": list(self.cases.values())}


@dataclass
class Session:
    case: dict
    book: dict
    page: str
    visit: int = 1
    state: str = "CONTINUE"
    pending: str | None = None
    used: list = field(default_factory=list)
    read: set = field(default_factory=set)
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_LIMIT))
    receipts: list = field(default_factory=list)


def _page_context(page):
    return {"页码": page["id"], "原文": page["text"]}


def make_messages(s, child):
    pages = s.book["pages"]
    index = next(i for i,p in enumerate(pages) if p["id"] == s.page)
    prior = pages[index - 1] if index and pages[index - 1]["id"] in s.read else None
    content = {"年龄":s.book["age"], "虚构绘本":s.book["title"], "当前书页":_page_context(pages[index]),
               "已读相邻前页":_page_context(prior) if prior else None, "实际交流历史":list(s.history),
               "此刻实际待答问题":s.pending, "本轮孩子说":child, "剩余可追问次数":2-len(s.used),
               "前一轮交流状态":s.state}
    return [{"role":"system", "content":SYSTEM_V6},
            {"role":"user", "content":json.dumps(content, ensure_ascii=False, separators=(",", ":"))}]


def make_messages_v7(s, child, index, *, quality_v72=False, system_prompt=None):
    messages = make_messages(s, child)
    content = json.loads(messages[1]["content"])
    content.pop("已读相邻前页")
    available = index.available(s.page, s.read)
    content["实际交流历史"] = [h for h in s.history if h["页码"] in available]
    content.update(index.context(s.page, s.read, child + " " + (s.pending or "")))
    return [{"role": "system", "content": system_prompt or (SYSTEM_V72 if quality_v72 else SYSTEM_V7)},
            {"role": "user", "content": json.dumps(content, ensure_ascii=False, separators=(",", ":"))}]


def _parse(raw):
    # Extend the v5 action vocabulary without changing v5 or relaxing its field/byte checks.
    if type(raw) is not str or len(raw) > 4096 or len(raw.encode("utf-8")) > 4096:
        raise ValueError("draft budget")
    doc = _strict_json(raw)
    if type(doc) is dict and doc.get("next_action") == "PAUSE":
        normalized = json.dumps({**doc, "next_action":"LISTEN"}, ensure_ascii=False, separators=(",", ":"))
        parse_draft(normalized, protocol="B")
        return doc
    return parse_draft(raw, protocol="B")


# Only exempt complete questions explicitly presented as reported speech, not
# arbitrary quoted questions. This is a formatting check, not a semantic judge.
_REPORTED_QUOTE = re.compile(
    r"(?:刚才问(?:的是)?|原文(?:写|问)(?:的是)?|书里(?:写|问)(?:的是)?)[：: ]*"
    r"(?:“[^“”]+”|「[^「」]+」|『[^『』]+』|\"[^\"]+\")"
)

def _unquoted_questions(text):
    return len(re.findall(r"[?？]+", _REPORTED_QUOTE.sub("", text)))

# Minimal deterministic presentation check, not a conversational intent classifier.
# Only detached backchannels are exempt; an added ask in the same or next clause still counts.
_BACKCHANNEL = re.compile(r"(?:[哇啊呀哦咦诶欸嗯][，, ]*)?(?:真的|真的吗|是吗|是呀)[?？]+")
_V72_REPORTED = re.compile(r"(?:原句是|你说的是|你说)[：: ]*(?:“[^“”]+”|「[^「」]+」|『[^『』]+』)")
_ANSWER_REQUEST = re.compile(
    r"^(?:请|来|那|再)?(?:(?:你)?(?:猜猜|说说|想想|告诉我|回答我|回答一下)|"
    r"你[^。！？!?]{0,38}(?:是不是|能不能|觉得[^。！？!?]{0,18}(?:什么|哪个|谁|哪里|为什么|怎么)|"
    r"(?:看到|听到|找到)(?:了)?(?:什么|谁|哪个|哪里)))"
)


def reply_question_issue(reply):
    attributed = _V72_REPORTED.sub("转述", _REPORTED_QUOTE.sub("转述", reply))
    for part in re.findall(r"[^。！？!?；;]+[？?！!]*", attributed):
        part = part.strip().lstrip("，,：: ")
        if _BACKCHANNEL.fullmatch(part):
            continue
        if re.search(r"[?？]", part):
            return "BODY_INTERROGATIVE_UNRESOLVED"
        if _ANSWER_REQUEST.match(part):
            return "BODY_ANSWER_REQUEST"
    return None


def project_v72(result, used, decision):
    """Check body separately; never rewrite it to try to save a draft."""
    spoken, draft, reason = project(result, used, reported_quotes=True, quality_v72=True, decision=decision)
    decision.setdefault("kind", "FALLBACK" if reason else "UNCHANGED")
    if reason:
        decision.setdefault("reason", reason)
    return spoken, draft, reason


def project(result, used, *, reported_quotes=False, quality_v72=False, decision=None):
    """Validate the protocol only; no fact/naturalness decisions are made here."""
    status, raw = result.get("status"), result.get("text")
    draft = None
    try:
        if type(raw) is str and len(raw) <= 4096:
            parsed = _strict_json(raw)
            draft = parsed if type(parsed) is dict else None
    except (ValueError, RecursionError):
        pass
    reason = None
    if status != "RETURNED":
        reason = status if status in {"DEMO_DEADLINE","UPSTREAM_BUSY","TIMEOUT","API_ERROR","PARSE_ERROR"} else "API_ERROR"
    elif result.get("stop_type") not in (None, "stop"):
        reason = "INCOMPLETE_OUTPUT"
    else:
        try:
            doc = _parse(raw)
            q = doc.get("followup_question")
            if doc["next_action"] == "HANDOFF":
                reason = "LOCAL_HANDOFF"
            elif quality_v72:
                issue = reply_question_issue(doc["reply"])
                if issue:
                    decision["reason"] = issue
                    reason = "QUESTION_FORMAT"
                else:
                    q_reason = None
                    if q:
                        if _unquoted_questions(q) != 1:
                            q_reason = "FOLLOWUP_QUESTION_SHAPE"
                        elif len(used) >= 2:
                            q_reason = "FOLLOWUP_QUOTA"
                        elif q.strip() in used:
                            q_reason = "REPEATED_QUESTION"
                    if q_reason:
                        decision.update(kind="REPLY_ONLY", reason=q_reason)
                        return {"reply":doc["reply"],"next_action":"RESPOND","followup_question":None}, draft, None
                    return {"reply":doc["reply"],"next_action":doc["next_action"],"followup_question":q}, draft, None
            elif ((bool(_unquoted_questions(doc["reply"])) or _unquoted_questions(q or "") > 1)
                  if reported_quotes else
                  (re.search(r"[?？]", doc["reply"]) or len(re.findall(r"[?？]+", q or "")) > 1)):
                reason = "QUESTION_FORMAT"  # Punctuation, not semantic verification.
            elif q and len(used) >= 2:
                reason = "FOLLOWUP_QUOTA"
            elif q and q.strip() in used:
                reason = "REPEATED_QUESTION"  # Exact repeat only.
            else:
                return {"reply":doc["reply"],"next_action":doc["next_action"],"followup_question":q}, draft, None
        except (ValueError, TypeError, RecursionError):
            reason = "INVALID_DRAFT"
    return {"reply":FALLBACK,"next_action":"RESPOND","followup_question":None}, draft, reason


class Coreading:
    def __init__(self, catalog, generate, mode, recorder=None, *, version=6, system_prompt=None):
        self.system_prompt = system_prompt
        if mode not in ("LIVE", "MOCK"): raise ValueError("mode")
        self.catalog, self.generate, self.mode, self.recorder = catalog, generate, mode, recorder
        self.version = version
        self.indexes = {k: BookEvidenceIndex(b) for k, b in catalog.books.items()} if version == 7 else {}
        self.sessions = {}
        self.lock = threading.Lock()

    def view(self, token, s):
        return {"mode":self.mode, "session":token, "case_id":s.case["id"], "page_id":s.page,
                "visit_id":s.visit, "state":s.state, **({"version": 7} if self.version == 7 else {})}

    def _get(self, token):
        if type(token) is not str or token not in self.sessions:
            raise ValueError("无效合成会话")
        return self.sessions[token]

    def start(self, case_id):
        if type(case_id) is not str or case_id not in self.catalog.cases:
            raise ValueError("请选择合成剧本")
        case = self.catalog.cases[case_id]
        page = case["turns"][0]["page"]
        s = Session(case, self.catalog.books[case["book_id"]], page, read={page})
        with self.lock:
            if len(self.sessions) >= 256:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(24)
            self.sessions[token] = s
            return self.view(token, s)

    def page(self, token, page):
        with self.lock:
            s = self._get(token)
            number = len(s.receipts)
            if (type(page) is not str or number >= len(s.case["turns"])
                    or page != s.case["turns"][number]["page"]):
                raise ValueError("只能进入下一轮合成发言所在的书页")
            if page != s.page:
                s.page, s.state, s.pending = page, "CONTINUE", None
                s.visit += 1
                s.used.clear()
                s.read.add(page)
            return self.view(token, s)

    def turn(self, token, number):
        started = perf_counter()
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError
        try:
            s = self._get(token)
            if type(number) is not int or not 0 <= number < len(s.case["turns"]):
                raise ValueError("无效轮次")
            if number < len(s.receipts):
                receipt = s.receipts[number]
                if receipt["visit_id"] != s.visit: raise ValueError("上一访问的轮次")
                return receipt  # No new model call, evidence or quota side effects.
            if number != len(s.receipts) or s.case["turns"][number]["page"] != s.page:
                raise ValueError("请按顺序提交；跨页须先明确翻页")
            child = s.case["turns"][number]["child"]
            question = s.pending
            if not child.strip():
                # Empty input does not count as a child utterance and does not consume a question.
                receipt = {**self.view(token,s),"number":number,"child":child,"actual_question":question,
                           "kind":"NO_SUBMISSION","spoken":None,"draft":None,"independent_review":"NOT_PERFORMED",
                           "provider_status":"NOT_CALLED","fallback_reason":None,"server_ms":0.0}
                if self.recorder:
                    self.recorder.add("server_receipt",token,version=self.version,case_id=s.case["id"],page_id=s.page,
                                      visit_id=s.visit,turn_number=number,receipt=receipt)
                s.receipts.append(receipt)
                return receipt
            messages = (make_messages_v7(s, child, self.indexes[s.book["id"]], quality_v72=True, system_prompt=self.system_prompt) if self.version == 7
                        else make_messages(s, child))
            meta = dict(version=self.version,case_id=s.case["id"],book_id=s.book["id"],page_id=s.page,
                        visit_id=s.visit,turn_number=number)
            if self.recorder:
                self.recorder.add("request",token,**meta,child=child,actual_question=question,
                                  model_id=PRIMARY_MODEL if self.mode=="LIVE" else None,
                                  book_text=next(p["text"] for p in s.book["pages"] if p["id"]==s.page),
                                  messages=messages, **({"book_page_order": list(self.indexes[s.book["id"]].pages),
                                      "visited_pages": sorted(s.read)} if self.version == 7 else {}))
                def observe(upstream):
                    self.recorder.add("upstream",token,**meta,provider_result=upstream)
                result = self.generate(messages, observe=observe)
            else:
                result = self.generate(messages)
            decision = {}
            spoken, draft, reason = (project_v72(result, s.used, decision) if self.version == 7
                                     else project(result, s.used))
            s.pending = spoken["followup_question"]
            if s.pending: s.used.append(s.pending.strip())
            s.state = "PAUSE" if reason else STATES[spoken["next_action"]]
            kind = "FALLBACK" if reason else ("MOCK" if self.mode == "MOCK" else "MODEL")
            s.history.append({"页码":s.page,"访问序号":s.visit,"孩子":child,"当时的问题":question,
                              "芽芽实际说":spoken["reply"],"另列追问":s.pending,"交流状态":s.state})
            receipt = {**self.view(token,s),"number":number,"child":child,"actual_question":question,
                       "kind":kind,"spoken":spoken,"draft":draft,"independent_review":"NOT_PERFORMED",
                       "provider_status":result.get("status"),"fallback_reason":reason,
                       **({"display_decision":decision} if self.version == 7 else {}),
                       "server_ms":round((perf_counter()-started)*1000,1)}
            s.receipts.append(receipt)
            if self.recorder: self.recorder.add("server_receipt",token,**meta,receipt=receipt)
            return receipt
        finally:
            self.lock.release()
