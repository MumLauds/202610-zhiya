"""Frozen synthetic fixture, immutable prompt and conversation projection helpers from the offline experiment."""
from hashlib import sha256
import json
from pathlib import Path
import re

from yaya2_lab.protocol import parse_draft, review

LAB = Path(__file__).resolve().parent
FIXTURE = LAB / "fixtures/smoke-12.json"
FIXTURE_HASH = "596e88ba67be1b54329344942d4bf0f13d512772ece43808d93472fe3ba54f18"
CASES = ("dev-04", "dev-06", "dev-10", "dev-11")
SYSTEM = """你是陪孩子读绘本的芽芽。自然承接最新表达；区分原文、孩子的想法和想象。不必每轮提问，可倾听、陪看或自然收束话题，不操作翻页。只输出一个JSON对象。reply是简短中文，不藏问题。next_action取RESPOND、LISTEN、ASK_FOLLOWUP、CLOSE_TOPIC、HANDOFF。仅ASK_FOLLOWUP给出followup_question（一句新问题）；其余省略此字段。claims为数组，没有主张可为空；每项为{target,span,mode,evidence_refs}。target是reply或followup_question；span必须从target指定的你自己写的展示文本逐字截取且只出现一次，不是从书页或孩子原话摘抄。mode取BOOK_FACT、BOOK_INFERENCE（两者引书证ID）、CHILD_VIEW、HYPOTHETICAL（后两者证据数组为空）。勿将自己猜的心情归给孩子。"""
SYSTEM_B = SYSTEM.split("claims为数组", 1)[0] + "勿将自己猜的心情归给孩子。"
SYSTEMS = {"A": SYSTEM, "B": SYSTEM_B}
# This is an experiment-only saved receipt's spoken text, not a replacement production rule engine.
FALLBACK = "我在这里陪你。"
VERSION = "paired-visit/5.0"


def canon(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(s):
    return sha256(s.encode("utf-8")).hexdigest()


def cases():
    data = FIXTURE.read_bytes()
    if sha256(data).hexdigest() != FIXTURE_HASH:
        raise ValueError("frozen synthetic fixture digest mismatch")
    fixture = json.loads(data)
    if fixture.get("synthetic_only") is not True or fixture.get("split") != "development_smoke":
        raise ValueError("development synthetic data only")
    lookup = {i["id"]: i for i in fixture["cases"]}
    return [lookup[k] for k in CASES]


def tracking(history, initial, child):
    if not history:
        q, origin, branch = initial, "fixture_initial_question", "ANSWER_INITIAL"
    else:
        prev = history[-1]
        q = prev["diagnostic_followup"]
        origin = f'turn-{prev["turn"]}:followup_question' if q else None
        if not q and prev["embedded_question"]:
            q = prev["embedded_question"]
            origin = f'turn-{prev["turn"]}:reply/diagnostic_unstructured_question'
        if prev["next_action"] in ("CLOSE_TOPIC","HANDOFF"):
            q, origin, branch = None, None, "VOLUNTARY_REOPEN_AFTER_CLOSE"
        elif q is not None:
            branch = "CHILD_AFTER_ACTUAL_QUESTION"
        else:
            branch = "VOLUNTARY_COMMENT_NO_QUESTION"
    if not child.strip():
        branch = "EMPTY_NO_SUBMISSION"
    return q, origin, branch


def make_prompt(inp, ctx, protocol="A"):
    hist = [{"孩子":h["child"],"当时的问题":h["actual_question"],
             "芽芽实际说":h["diagnostic_reply"],"另列追问":h["diagnostic_followup"]}
            for h in ctx["history"]]
    data = {"年龄":inp["age"],"虚构绘本":inp["title"],"本页证据":inp["page_evidence"],
            "实际交流历史":hist,"此刻实际待答问题":ctx["actual_question"],
            "本轮孩子说":inp["synthetic_child"],"剩余可追问次数":ctx["remaining"]}
    return {"system":SYSTEMS[protocol],"user":canon(data)}


def projection(raw, status, refs, remaining, used, protocol="A"):
    if status == "EMPTY_INPUT":
        return None, {"status":"NO_SUBMISSION","reason":"empty_input"}
    why = None
    try:
        if status != "GENERATED":
            raise ValueError(status.lower())
        d = parse_draft(raw, protocol=protocol)
        if any(x["mode"] in ("BOOK_FACT","BOOK_INFERENCE") and (
                not x["evidence_refs"] or not refs.issuperset(x["evidence_refs"])) for x in d.get("claims", [])):
            raise ValueError("out_of_scope_or_missing_reference")
        if d["next_action"] == "HANDOFF":
            raise ValueError("local_handoff")
    except (ValueError, RecursionError) as exc:
        why = str(exc)
    if why is not None:
        return ({"reply":FALLBACK,"followup_question":None,"next_action":"RESPOND","lane":"SAVED_FALLBACK",
                 "projection_reason":why,"embedded_question":None},
                {"status":"SAVED_FALLBACK","reason":why})
    q = d.get("followup_question")
    action = d["next_action"]
    drop = q is not None and (remaining <= 0 or q.strip() in (s.strip() for s in used))
    if drop:
        q, action = None, "RESPOND"
    # Diagnostic only: a visible interrogative in reply was actually spoken even when
    # the structured question is missing. Punctuation is a transcript cue, NOT a semantic validator.
    hits = re.findall(r"[^。！？!?]+[？?]", d["reply"])
    strict = review(raw, context_id="synthetic-unreviewed", allowed_evidence=refs, protocol=protocol)
    return ({"reply":d["reply"],"next_action":action,"followup_question":q,"embedded_question":hits[-1] if hits else None,
             "lane":"DRAFT_SHADOW_ONLY","projection_reason":"quota_or_exact_repeat" if drop else "structural_projection_only"},
            {"status":strict.status,"reason":strict.reason})


