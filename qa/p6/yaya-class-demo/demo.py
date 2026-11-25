"""Loopback-only course demonstration; no arbitrary user text or production integration."""
import argparse
from concurrent.futures import ThreadPoolExecutor,TimeoutError
import json
import os
import re
from getpass import getpass, GetPassWarning
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sys
import threading
from time import perf_counter
import warnings

from provider import BailianProvider, Settings, PRIMARY_MODEL, message_digest
from evidence import EvidenceRecorder

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lab"))
from run_batch import SYSTEM_B, cases, make_prompt
from yaya2_lab.protocol import parse_draft

# Exactly two additions to the accepted protocol B prompt.
SYSTEM = SYSTEM_B + (
    "孩子问“书里真的写了吗”时，请回看本页证据中的原文并直接回答；"
    "未写明时只说明本页没有写，不把孩子的想法当原文。"
    "这一轮如果已经问过，就给孩子留出回答的空间；"
    "同一轮总共最多问一个问题，且只用followup_question。"
)
FIXTURE = {item["id"]:item for item in cases()}
CHOICE_CARD = "dev-06"
BOOK_TEXT = "一粒木纽扣安静地躺在井沿。"
DEMO_EVIDENCE = "本页没有描述木纽扣变成小船。"
CHOICES = {
    "1": "我想让这只小船飞上天。",
    "2": "等等，书里真的写它变成船了吗？",
}
PRESET = ({
    "actual_question": "你想到什么了？",
    "child": "我看着这粒纽扣，觉得它有点像一只小船。",
    "diagnostic_reply": "原来这粒纽扣让你想到了小船呀。",
    "diagnostic_followup": None,
},)
FALLBACK = "这次没能接上，我们先一起看看这一页吧。"
REPLAY = json.loads((HERE / "replay.json").read_text(encoding="utf-8"))


def draft_fields(raw):
    draft = parse_draft(raw, protocol="B")
    if draft["next_action"] == "HANDOFF":
        raise ValueError("HANDOFF")
    return dict(reply=draft["reply"], next_action=draft["next_action"],
                followup_question=draft.get("followup_question"))


class Demo:
    def __init__(self, generate, recorder=None):
        self.generate = generate
        self.recorder = recorder
        self.sessions = {}
        self.lock = threading.Lock()

    def start(self, case_id, arm_no):
        if not isinstance(case_id,str) or case_id not in FIXTURE or type(arm_no) is not int or arm_no not in (0,1):
            raise ValueError("请选择合成演示卡")
        if case_id == CHOICE_CARD and arm_no != 0:
            raise ValueError("请选择同页对照卡")
        with self.lock:
            if len(self.sessions) >= 256:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(24)
            history = [row.copy() for row in PRESET] if case_id == CHOICE_CARD else []
            self.sessions[token] = (case_id,arm_no,history)
        return {"session":token}

    def turn(self, token, number=None, *, choice_id=None):
        started = perf_counter()
        if type(token) is not str: raise ValueError("无效演示会话")
        # One in-flight request protects classroom cost and keeps one history in order.
        if not self.lock.acquire(blocking=False): raise BlockingIOError
        try:
            if token not in self.sessions: raise ValueError("请先选择演示卡")
            case_id, arm_no, history = self.sessions[token]
            case = FIXTURE[case_id]; arm = case["arms"][arm_no]
            if case_id == CHOICE_CARD:
                if number is not None or type(choice_id) is not str or choice_id not in CHOICES or len(history) != len(PRESET):
                    raise ValueError("请选择一条合成话语，或重新开始")
                child = CHOICES[choice_id]
            else:
                if choice_id is not None or type(number) is not int or not 0 <= number < len(arm["turns"]) or number != len(history):
                    raise ValueError("轮次已处理或无效，请重新选择演示卡")
                child = arm["turns"][number]
            if not child.strip(): raise ValueError("本轮为空，不调用模型")
            initial = arm["question"]
            question = history[-1]["diagnostic_followup"] if history else initial
            used = sum(h["diagnostic_followup"] is not None for h in history)
            prompt = make_prompt(
                {"age":case["age"],"title":case["title"],"page_evidence":{
                    k:case["page_evidence"][k] for k in arm["visible_evidence"]
                },"synthetic_child":child},
                {"actual_question":question,"history":history,"remaining":max(0,2-used)},
                protocol="B")
            messages = [{"role":"system","content":SYSTEM},{"role":"user","content":prompt["user"]}]
            record = self.recorder if case_id == CHOICE_CARD else None
            observe = None
            if record is not None:
                record.add("request",token,case_id=case_id,branch_id={"1":"A","2":"B"}[choice_id],
                           model_id=PRIMARY_MODEL if record.mode=="LIVE" else None,
                           book_text=BOOK_TEXT,demo_evidence=DEMO_EVIDENCE,
                           preset=PRESET,child=child,messages=messages)
                observe = lambda upstream: record.add("upstream",token,provider_result=upstream)
            result = self.generate(messages) if observe is None else self.generate(messages,observe=observe)
            raw = result.get("text")
            error = result.get("status")
            kind = "MODEL"
            reason = None
            if error != "RETURNED":
                reason = error if error in {"DEMO_DEADLINE","UPSTREAM_BUSY","TIMEOUT","API_ERROR","PARSE_ERROR"} else "API_ERROR"
            elif result.get("stop_type") not in (None,"stop"):
                reason = "INCOMPLETE_OUTPUT"
            else:
                try:
                    spoken = draft_fields(raw)
                    followup = spoken["followup_question"]
                    if followup and used >= 2:
                        raise ValueError("follow-up quota")
                    # Demo-only punctuation check. It cannot detect unpunctuated questions.
                    if re.search(r"[?？]", spoken["reply"]) or len(re.findall(r"[?？]+", followup or "")) > 1:
                        reason = "QUESTION_FORMAT"
                except (ValueError, RecursionError, TypeError):
                    reason = "INVALID_DRAFT"
            if reason:
                kind = "FALLBACK"
                spoken = dict(reply=FALLBACK,next_action="RESPOND",followup_question=None)
            history.append({"child":child,"actual_question":question,"diagnostic_reply":spoken["reply"],
                            "diagnostic_followup":spoken["followup_question"]})
            # HTML renders these strings as plain text. No credentials or envelopes.
            receipt = {"kind":kind,"actual_question":question,"child":child,"spoken":spoken,
                    "provider_status":"RETURNED" if error == "RETURNED" else reason,
                    "fallback_reason":reason,"server_ms":round((perf_counter()-started)*1000,1)}
            if record is not None:
                record.add("server_receipt",token,receipt=receipt)
            return receipt
        finally:
            self.lock.release()


def serve(port, demo, mode):
    def cards_for_page():
        cards = []
        for c in FIXTURE.values():
            for i, a in enumerate(c["arms"]):
                if c["id"] == CHOICE_CARD and i != 0:
                    continue
                card = {"id":c["id"],"title":c["title"],"arm":i,"question":a["question"],
                        "page":{k:c["page_evidence"][k] for k in a["visible_evidence"]},"turns":a["turns"]}
                if c["id"] == CHOICE_CARD:
                    card.update(turns=[], preset=PRESET, book_text=BOOK_TEXT,demo_evidence=DEMO_EVIDENCE,
                                choices=[{"id":key,"text":value} for key,value in CHOICES.items()])
                cards.append(card)
        return cards

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): return
        def response(self, status, content, content_type="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type",content_type)
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy","default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header("Content-Length",str(len(content)))
            try:
                self.end_headers()
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                print("演示：客户端在收取回复前断开。", file=sys.stderr, flush=True)
        def data(self, status, value):
            self.response(status,json.dumps(value,ensure_ascii=False).encode())
        def host_ok(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}")
        def do_GET(self):
            if not self.host_ok(): return self.data(403,{"error":"仅限本机演示"})
            static = {"/":("index.html","text/html"),"/app.js":("app.js","text/javascript"),"/style.css":("style.css","text/css")}
            if self.path in static:
                name, ct=static[self.path]
                return self.response(200,(HERE/name).read_bytes(),ct+"; charset=utf-8")
            if self.path=="/cards":
                return self.data(200,{"mode":mode,"cards":cards_for_page()})
            if self.path=="/replay": return self.data(200,REPLAY)
            self.data(404,{"error":"未找到"})
        def do_POST(self):
            if not self.host_ok() or self.headers.get("Origin") != "http://"+self.headers["Host"]:
                return self.data(403,{"error":"仅允许同源演示页面"})
            if self.path not in ("/start","/turn") or self.headers.get_content_type()!="application/json":
                return self.data(400,{"error":"无效演示请求"})
            try:
                length=int(self.headers.get("Content-Length","0"))
                if not 0 < length <= 512: raise ValueError
                data=json.loads(self.rfile.read(length))
                if self.path=="/start" and type(data) is dict and set(data)=={"id","arm"}:
                    result=demo.start(data["id"],data["arm"])
                elif self.path=="/turn" and type(data) is dict and set(data)=={"session","number"}:
                    result=demo.turn(data["session"],data["number"])
                elif self.path=="/turn" and type(data) is dict and set(data)=={"session","choice_id"}:
                    result=demo.turn(data["session"],choice_id=data["choice_id"])
                else: raise ValueError
            except BlockingIOError: return self.data(409,{"error":"正在处理一轮，请稍后再试"})
            except (ValueError,TypeError,RecursionError): return self.data(400,{"error":"仅接受固定合成卡和有效轮次"})
            return self.data(200,result)
    return ThreadingHTTPServer(("127.0.0.1",port),Handler)



class DeadlineGenerator:
    """Separate interaction cutoff; timed-out upstream calls never commit a turn."""
    def __init__(self, generate, seconds=4.1):
        self.generate = generate
        self.seconds = seconds
        self.pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix="bailian")
        self.slot = threading.BoundedSemaphore(1)

    def __call__(self,messages,*,observe=None):
        if not self.slot.acquire(blocking=False):
            return {"status":"UPSTREAM_BUSY"}
        def call():
            try: return self.generate(messages)
            finally: self.slot.release()
        pending = self.pool.submit(call)
        if observe is not None:
            def collect(done):
                try: result = done.result()
                except Exception: result = {"status":"API_ERROR","error_type":"WORKER_FAILURE"}
                observe(result)
            pending.add_done_callback(collect)
        try: return pending.result(timeout=self.seconds)
        except TimeoutError: return {"status":"DEMO_DEADLINE"}

    def close(self):
        self.pool.shutdown(wait=True)


def mock_generate(_):
    return {"status":"RETURNED","text":'{"reply":"这是本地模拟，不是模型回答。","next_action":"LISTEN"}'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mock",action="store_true",help="本地模拟，不发 API 请求")
    parser.add_argument("--prompt-key",action="store_true",help="从无回显提示为本进程设置环境变量")
    parser.add_argument("--record-dir",type=Path,help="为纽扣选择卡保存证据；目录必须不存在")
    args=parser.parse_args()
    if args.mock:
        run_server(args.port,mock_generate,"MOCK",args.record_dir,parser)
        return
    if args.prompt_key:
        if not sys.stdin.isatty(): parser.error("需在交互式终端无回显输入")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error",GetPassWarning)
                os.environ["DASHSCOPE_API_KEY"]=getpass("北京百炼演示 Key（无回显）：").strip()
        except (GetPassWarning, EOFError): parser.error("无法安全读取密钥")
    missing = [k for k in ("DASHSCOPE_API_KEY","DASHSCOPE_BASE_URL") if not os.environ.get(k,"").strip()]
    if missing:
        parser.error("缺少环境配置：" + "、".join(missing))
    try:
        cfg=Settings.from_env()
        if cfg.region != "cn-beijing" or cfg.timeout_s != 60:
            raise ValueError("仅支持北京工作空间与已验证的 60 秒调用设置")
    except ValueError as exc:
        parser.error(str(exc))
    # Permit only the next concrete request generated from fixed fixtures and server-held history.
    permitted=set()
    with BailianProvider(cfg, allow_message=permitted.__contains__) as provider:
        def generate(messages):
            digest=message_digest(messages)
            permitted.add(digest)
            try: return provider.complete(messages,PRIMARY_MODEL)
            finally: permitted.discard(digest)
        run_server(args.port,generate,"LIVE",args.record_dir,parser)


def run_server(port,generate,mode,record_dir,parser):
    try:
        recorder=EvidenceRecorder(record_dir,mode) if record_dir is not None else None
    except OSError:
        parser.error("无法创建证据目录；请指定全新的本地目录")
    bounded=DeadlineGenerator(generate) if mode=="LIVE" or recorder else None
    try:
        with serve(port,Demo(bounded or generate,recorder),mode) as app:
            print(f"{mode} · http://127.0.0.1:{port}",flush=True); app.serve_forever()
    finally:
        if bounded: bounded.close()
        if recorder: recorder.close()


if __name__=="__main__":
    try: main()
    except KeyboardInterrupt: pass
