"""Offline-only: reconcile synthetic v6 evidence; never call a model or score prose."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import html
import json
import math
from pathlib import Path
from random import SystemRandom
from secrets import token_hex
from statistics import mean, median

RUBRIC = ("理解能力", "自然程度", "上下文承接", "绘本准确性", "引导质量", "倾听能力")
CHECKS = ("接住了孩子刚说的话", "出现绘本事实错误", "机械重复了问题", "尊重了孩子当时的意愿")
RATING_HELP = ("理解孩子实际在说什么", "像在和这个年龄的孩子说话", "记得前面实际说过什么",
               "没有把猜想、想象或旧页当成本页原文", "如果追问，它有必要；不问也可得高分", "在孩子想停一停时给了空间")
UNKNOWN = "无法判断"
RUBRIC_V7 = ("绘本理解", "跨页记忆", "时间线", "自然程度", "上下文承接", "倾听能力")
HELP_V7 = ("角色、动作和对象与书文一致；不把想象当事实", "正确关联已经读过的页面；没有相关场景可选无法判断",
           "没有把猜测、准备当成已经发生，不提前说未来结局", "像在与这个年龄的孩子自然交谈",
           "接住孩子这轮表达和改口，而不只是重复原词", "尊重停问意愿，也能接住孩子恢复交流")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def dump(path, obj):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, allow_nan=False)


def timing(values):
    xs = sorted(v for v in values if type(v) in (float, int) and math.isfinite(v) and v >= 0)
    return dict(n=len(xs), mean_ms=round(mean(xs), 2) if xs else None, p50_ms=median(xs) if xs else None,
                p95_ms=xs[math.ceil(.95*len(xs))-1] if len(xs) >= 20 else None)


def reconcile(events, exports, rates=None):
    require(events, "无 v6 证据")
    mode = events[0]["mode"]
    version = events[0]["version"]
    require(version in (6, 7), "未知的证据版本")
    require(mode in {"LIVE", "MOCK"}, "未知运行模式")
    parts = {}
    session_cases = {}
    for e in events:
        name, sid, n = e["event"], e["session_id"], e["turn_number"]
        require(e["mode"] == mode and e["version"] == version, "服务器模式或版本混用")
        require(isinstance(sid, str) and sid and type(n) is int and n >= 0, "无效会话或轮号")
        require(session_cases.setdefault(sid, e["case_id"]) == e["case_id"], "会话对应不同案例")
        t = parts.setdefault((sid, n), {})
        require(name in ("request", "upstream", "server_receipt") and name not in t, "未知/重复服务器事件")
        t[name] = e
    for export in exports:
        require(export["schema"] == f"yaya-v{version}-page-timings-v1" and export["mode"] == mode, "页面模式或版本不一致")
        for p in export["timings"]:
            k = (export["session_id"], p["turn_number"])
            require(type(k[1]) is int and k in parts and "page" not in parts[k], "页面记录不属于本批次、轮号错误或重复")
            require(p["session_id"] == k[0] and p["number"] == k[1] and p["case_id"] == export["case_id"]
                    and p["mode"] == mode, "页面导出与行标识不同")
            parts[k]["page"] = p
    rows = []
    skipped = []
    prior_by_session = defaultdict(list)
    system_hashes, parameter_hashes = set(), set()
    for key, t in sorted(parts.items()):
        req = t.get("request")
        if req is None and t.get("server_receipt", {}).get("receipt", {}).get("kind") == "NO_SUBMISSION":
            e = t["server_receipt"]
            sr = e["receipt"]
            require(not sr["child"].strip() and "upstream" not in t and sr["provider_status"]=="NOT_CALLED"
                    and sr.get("spoken") is None and sr.get("mode")==mode and sr.get("session")==key[0]
                    and sr.get("number")==key[1], "无输入记录不可拥有模型请求或回复")
            if "page" in t:
                no = t["page"]
                require(no["kind"]==no["category"]=="NO_SUBMISSION" and no["child"]==sr["child"]
                        and no["case_id"]==e["case_id"] and no["page_id"]==e["page_id"] and no["visit_id"]==e["visit_id"]
                        and no["server_receipt"]==sr and no["displayed_reply"] is None, "空输入页面证据不符")
            skipped.append(dict(session_id=key[0], turn_number=key[1], receipt=sr, final_display=t.get("page")))
            continue
        require(req, "存在无对应请求的事件")
        for e in t.values():
            require(all(req[k] == e[k] for k in ("case_id","page_id","visit_id")), "事件的案例、书页或访问不一致")
        messages = req["messages"]
        require(messages and messages[0]["role"] == "system" and sha(messages[0]["content"].encode()) ==
                req["system_prompt_sha256"], "系统提示词指纹不符")
        require(len(messages)==2 and messages[1]["role"]=="user", "缺少实际生成上下文")
        context = json.loads(messages[1]["content"])
        require(context["本轮孩子说"]==req["child"] and context["当前书页"]=={"页码":req["page_id"],"原文":req["book_text"]}
                and context["此刻实际待答问题"]==req.get("actual_question"), "请求上下文与当前书页、原话或待答问题不符")
        system_hashes.add(req["system_prompt_sha256"])
        prior=prior_by_session[key[0]]
        expected = prior[-4:]
        if version == 7:
            order = req["book_page_order"]
            visited = set(req["visited_pages"])
            allowed = set(order[:order.index(req["page_id"]) + 1]) & visited
            permitted = {req["page_id"]} | {p["页码"] for p in context["已读相关前页"]}
            texts = {p["页码"]: p["原文"] for p in context["已读相关前页"]}
            texts[req["page_id"]] = req["book_text"]
            require(req["page_id"] in visited and permitted <= allowed and
                    all(e["页码"] in permitted and e["证据"].startswith(e["页码"] + ".e") and e["原文片段"] in texts[e["页码"]]
                        for e in context["有原文依据的事件时间线"]), "检索或时间线包含不可见的页")
            expected = [x for x in expected if x["页码"] in allowed]
        require(context["实际交流历史"]==expected, "上下文历史与本会话前面实际服务回执不同或缺失")
        up = t.get("upstream") or {}
        if up.get("raw_body") is not None:
            require(sha(up["raw_body"].encode("utf-8"))==up["raw_body_sha256"], "API 原始响应指纹不符")
            if up.get("status")=="RETURNED":
                body=json.loads(up["raw_body"])
                require(body["choices"][0]["message"]["content"]==up["text"] and body.get("usage")==up.get("usage"),
                        "草稿或用量与原始响应不符")
        if up.get("parsed_model_json") is not None:
            require(json.loads(up["text"])==up["parsed_model_json"], "解析草稿与模型原始内容不符")
        sent = up.get("request_body")
        if sent is not None:
            parameter_hashes.add(sha(canonical({k:v for k,v in sent.items() if k!="messages"})))
            require(sent["messages"] == messages, "请求记录与上游实际 messages 不符")
            if req.get("model_id"):
                require(req["model_id"] == sent["model"], "请求模型不一致")
        sr = t.get("server_receipt", {}).get("receipt")
        page = t.get("page")
        if sr is not None:
            require(sr.get("mode")==mode and sr.get("session")==key[0] and sr.get("number")==key[1]
                    and all(sr.get(k)==req[k] for k in ("case_id","page_id","visit_id")), "服务端回执身份不符")
            require(sr.get("kind") in ("MODEL" if mode=="LIVE" else "MOCK", "FALLBACK"), "服务端回执模式不符")
            if "child" in sr:
                require(sr["child"] == req["child"], "回执中的孩子原话不一致")
            prior.append({"页码":req["page_id"],"访问序号":req["visit_id"],"孩子":req["child"],
                          "当时的问题":req.get("actual_question"),"芽芽实际说":sr["spoken"]["reply"],
                          "另列追问":sr["spoken"].get("followup_question"),"交流状态":sr["state"]})
        if page is not None:
            require(page["child"] == req["child"], "页面孩子原话与请求不符")
            ms = page["ms"]
            require(type(ms) in (int, float) and math.isfinite(ms) and ms >= 0 and page["kind"] in ("MODEL" if mode=="LIVE" else "MOCK","FALLBACK"),
                    "页面展示类型/计时无效")
            category = "OVER_5S" if ms > 5000 else page["kind"] + "_WITHIN_5S"
            require(page["category"] in (category, "BACKGROUND_EXCLUDED"), "页面时间分段不符")
            if page["server_receipt"] is not None:
                require(sr is not None and page["server_receipt"] == sr, "页面与服务端回执不符")
                require(page["kind"]==sr["kind"] and page["displayed_reply"]==sr["spoken"]["reply"]
                        and page.get("displayed_followup")==sr["spoken"].get("followup_question")
                        and page.get("fallback_reason")==sr.get("fallback_reason"), "页面声称显示的文字/类型与回执不同")
            else:
                require(page["kind"] == "FALLBACK", "无服务回执的页面不能声明模型正文")
        rows.append(dict(session_id=key[0], turn_number=key[1], case_id=req["case_id"], page_id=req["page_id"],
                         visit_id=req["visit_id"], book_text=req["book_text"], child=req["child"],
                         actual_question=req.get("actual_question"), messages=messages, model_id=req.get("model_id"),
                         upstream_status=up.get("status"), parsed_draft=up.get("parsed_model_json"),
                         raw_model_content=up.get("text"), usage=up.get("usage"), api_ms=up.get("call_ms"),
                         request_id=up.get("request_id"), observed_live_call=mode=="LIVE" and sent is not None,
                         independent_review=sr.get("independent_review", "UNKNOWN") if sr else "UNKNOWN",
                         server_receipt=sr, final_display=page, upstream_observed=bool(up)))
    require(len(system_hashes)<=1 and len(parameter_hashes)<=1, "同批次混用了系统提示词或生成参数")
    actual = [r for r in rows if r["observed_live_call"]]
    observed = [r for r in rows if r["upstream_observed"]]
    receipts = [r["server_receipt"] for r in rows if r["server_receipt"] is not None]
    pages = [r["final_display"] for r in rows if r["final_display"] is not None]
    usage = {}
    for k in ("prompt_tokens","completion_tokens"):
        vals = [(r["usage"] or {}).get(k) for r in actual]
        vals = [v for v in vals if type(v) is int and v>=0]
        usage[k] = {"observed_sum": sum(vals) if vals else None, "reported_calls": len(vals)}
    est = None
    if rates is not None:
        require(len(rates)==2 and all(p.is_finite() and p>=0 for p in rates), "单价须为有限非负美元数")
        if actual and all(v["reported_calls"]==len(actual) for v in usage.values()):
            est = str(sum(Decimal(usage[k]["observed_sum"])*p for k,p in zip(usage, rates))/Decimal(10**6))
    statuses = Counter(r["upstream_status"] for r in actual)
    reasons = Counter(r["fallback_reason"] for r in receipts if r["kind"]=="FALLBACK")
    return dict(mode=mode, version=version, scope="UNCLASSIFIED_BATCH" if version == 7 else "DEVELOPMENT_ONLY", rows=rows, no_submission=skipped, summary=dict(
        sessions=len(session_cases), requested_turns=len(rows), skipped_empty=len(skipped), cases_observed=sorted(set(session_cases.values())),
        observed_live_api_calls=len(actual), mock_upstream_results=len(observed) if mode=="MOCK" else 0,
        no_upstream_result=len(rows)-len(observed), live_api_status_counts=dict(statuses),
        api_errors=statuses["API_ERROR"], upstream_timeouts=statuses["TIMEOUT"], server_fallbacks=sum(reasons.values()),
        server_fallback_reasons=dict(reasons), invalid_format_server_fallbacks=sum(reasons[k] for k in
           ("PARSE_ERROR","INVALID_DRAFT","INCOMPLETE_OUTPUT","QUESTION_FORMAT")), missing_server_receipts=len(rows)-len(receipts),
        missing_page_observations=len(rows)-len(pages), page_categories=dict(Counter(p["category"] for p in pages)),
        page_fallbacks=sum(p["kind"]=="FALLBACK" for p in pages), usage=usage, estimated_usd_for_observed_live_calls=est,
        usd_per_million_tokens=[str(p) for p in rates] if rates else None, actual_invoice_usd=None,
        api_latency=timing(r["api_ms"] for r in actual), server_latency=timing(r.get("server_ms") for r in receipts),
        foreground_page_latency=timing(p["ms"] for p in pages if p["category"]!="BACKGROUND_EXCLUDED")))

def esc(text):
    return html.escape(str(text), quote=True)


def review_forms(report, out):
    """Anonymous reviewers see no IDs, model, expected answers, mode labels or scores from one another."""
    require(report["mode"]=="LIVE", "MOCK 不生成质量盲评表")
    require(any(isinstance(r["parsed_draft"],dict) and r["parsed_draft"].get("reply") for r in report["rows"]),
            "没有可评的真实生成草稿")
    groups=defaultdict(list)
    for row in report["rows"]:
        groups[row["session_id"]].append(row)
    digest=sha(canonical([sha(canonical(report["rows"])), token_hex(16)]))
    dimensions, help_text = (RUBRIC_V7, HELP_V7) if report.get("version") == 7 else (RUBRIC, RATING_HELP)
    key=dict(batch=digest, reviewers={})
    if report.get("version") == 7:
        key.update(version=7, scope=report["scope"])
    for reviewer in ("甲","乙"):
        ids=list(groups)
        SystemRandom().shuffle(ids)
        mapping={f"{i+1:02}":s for i,s in enumerate(ids)}
        key["reviewers"][reviewer]=mapping
        blocks=[]
        for label, sid in mapping.items():
            rows=groups[sid]
            chunks=[]
            for r in rows:
                parsed=r["parsed_draft"]
                if isinstance(parsed,dict) and isinstance(parsed.get("reply"),str):
                    draft=parsed["reply"]+(" ／ "+str(parsed["followup_question"]) if parsed.get("followup_question") else "")
                else:
                    draft="没有可识别的结构化草稿；不要把备用句算成模型回答。"
                sr=r["server_receipt"]
                page=r["final_display"]
                visible=(" ／ ".join(str(x) for x in [page["displayed_reply"],page.get("displayed_followup")] if x)
                         if page else "无页面记录，不能推定孩子看见了什么")
                n=r["turn_number"]+1
                turn=f'<div class="turn" data-turn="{n}"><strong>第 {n} 次说话 · 页 {esc(r["page_id"])}</strong>'
                if report.get("version") == 7:
                    prior = json.loads(r["messages"][1]["content"])["已读相关前页"]
                    if prior:
                        turn += '<details><summary>查看已读前文</summary>' + ''.join(
                            f'<p>{esc(p["页码"])}：{esc(p["原文"])}</p>' for p in prior) + '</details>'
                turn+=f'<p>书上写：{esc(r["book_text"])}</p><p>当时待答：{esc(r["actual_question"] or "没有待答问题")}</p>'
                turn+=f'<p>孩子：{esc(r["child"])}</p><p><b>芽芽草稿：</b>{esc(draft)}</p>'
                turn+=f'<details><summary>查看系统实际上返回了什么</summary><p>服务端：{esc(sr["kind"] if sr else "未知")} · '
                turn+=esc((" ／ ".join(str(x) for x in [sr["spoken"]["reply"],sr["spoken"].get("followup_question")] if x)) if sr else "无记录")
                turn+=f'</p><p>页面：{esc(visible)}</p></details><div class="checks">'
                for q in CHECKS:
                    turn+=f'<label>{q}<select><option value="">未标记</option><option>是</option><option>否</option><option>{UNKNOWN}</option></select></label>'
                chunks.append(turn+'</div></div>')
            context=json.loads(rows[0]["messages"][1]["content"])
            title=esc(context.get("虚构绘本","虚构绘本"))
            age=esc(context.get("年龄","年龄未记录"))
            scores="".join(f'<label title="{esc(h)}">{esc(q)} <select required><option value="">请选择</option>' +
                           ''.join(f'<option>{k}</option>' for k in (1,2,3,4,5,UNKNOWN))+
                           f'</select><small>{esc(h)}</small></label>' for q,h in zip(dimensions,help_text))
            blocks.append(f'<details class="case" data-id="{label}"><summary>对话 {label} · {title} · {age}</summary>'+
                          ''.join(chunks)+f'<div class="scores"><b>通读后给整段草稿打分</b>{scores}</div>'+
                          '<label>依据（可选，写轮次及原句）<textarea></textarea></label></details>')
        document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>芽芽｜独立阅读打分</title><style>body{max-width:54rem;margin:2rem auto;font:17px/1.5 system-ui;padding:0 1rem;color:#20362f}summary,button,select{cursor:pointer}summary{padding:8px 0}p{margin:.4rem 0}select,button,textarea{font:inherit}label{display:block}small{display:inline-block;margin-left:1em;color:#485b50}.case{border-top:2px solid #ddd;padding:8px 0}.turn{border-left:3px solid #adcbbb;padding:0 1rem;margin:1em 0}.scores{padding:1rem;background:#f2f6f0}.scores label{margin:8px 0}.checks{font-size:14px;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.4rem}.checks select{margin-left:.5rem}textarea{width:95%}button{margin:1rem 0}</style>
<h1>芽芽有没有接住孩子的话？</h1><p>独立阅读，不与另一位评审讨论。每段对话只打一次六项分。1 很差，3 基本做到，5 做得很好；没有可评的证据选“无法判断”。逐轮标记可留空，不当作“否”。</p>
<p>评的是<b>模型草稿</b>。折叠栏里是那时系统返回的文字和观众实际看见的文字，可能不同；它们只用来解释下一轮的上下文。页面不保存草稿，请完成后导出。</p>'''
        script='''<button id="save">导出我的评分</button><script>
document.getElementById('save').onclick=()=>{
 const cases={};
 for(const node of document.querySelectorAll('.case')){
  const scores=[...node.querySelectorAll('.scores select')].map(s=>s.value);
  if(scores.includes('')){node.open=true;node.scrollIntoView();alert('这段对话还有未选的分数。可以选“无法判断”。');return;}
  const turns={};for(const t of node.querySelectorAll('.turn'))turns[t.dataset.turn]=[...t.querySelectorAll('select')].map(s=>s.value||null);
  cases[node.dataset.id]={scores,turns,note:node.querySelector('textarea').value};
 }
 const blob=new Blob([JSON.stringify({batch:BATCH,reviewer:REVIEWER,cases},null,2)],{type:'application/json'});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='芽芽评分-'+REVIEWER+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);
};</script></html>'''
        # Do not inject fixture/model strings into JavaScript.
        script=script.replace("BATCH",json.dumps(digest)).replace("REVIEWER",json.dumps(reviewer))
        with (out/f"盲评-{reviewer}.html").open("x",encoding="utf-8") as f:
            f.write(document+''.join(blocks)+script)
    key["turns"]={sid:[str(r["turn_number"]+1) for r in group] for sid,group in groups.items()}
    dump(out/"blind-key-private.json",key)


def merge_votes(key, votes):
    require(len(votes)==2 and {v["reviewer"] for v in votes}=={"甲","乙"}, "需要甲、乙两张不同的评分表")
    rubric = RUBRIC_V7 if key.get("version") == 7 else RUBRIC
    per=defaultdict(dict)
    for v in votes:
        mapping=key["reviewers"][v["reviewer"]]
        require(v["batch"]==key["batch"] and set(v["cases"])==set(mapping), "批次或完成的对话不一致")
        for blind_id,item in v["cases"].items():
            sid=mapping[blind_id]
            scores=item["scores"]
            require(type(scores) is list and len(scores)==6 and all(s in ("1","2","3","4","5",UNKNOWN) for s in scores),"无效分数")
            require(set(item["turns"])==set(key["turns"][sid]),"逐轮观察不能漏行或增行")
            for vals in item["turns"].values():
                require(type(vals) is list and len(vals)==4 and all(x in (None,"是","否",UNKNOWN) for x in vals), "无效逐轮观察")
            per[sid][v["reviewer"]]={**item,"scores":dict(zip(rubric,scores)),
                                      "turns":{n:dict(zip(CHECKS,xs)) for n,xs in item["turns"].items()}}
    metrics={}
    for dim in rubric:
        pairs=[tuple(item[a]["scores"][dim] for a in ("甲","乙")) for item in per.values()]
        good=[(int(a),int(b)) for a,b in pairs if a!=UNKNOWN and b!=UNKNOWN]
        metrics[dim]={"both_scored":len(good),"all_sessions":len(pairs),"equal_scores":sum(a==b for a,b in good),
                      "two_or_more_apart":sum(abs(a-b)>=2 for a,b in good),
                      "mean_of_pairs":mean((a+b)/2 for a,b in good) if good else None}
    return {"batch":key["batch"],"scope":key.get("scope","DEVELOPMENT_ONLY"),
            "evaluation_target":"标注范围内的合成模型草稿，不是生产安全认证",
            "human_rubric":metrics,"by_session":dict(per)}


def main():
    p=argparse.ArgumentParser(description="合成共读 v6：离线整理证据与匿名评审。绝不调用模型。")
    sub=p.add_subparsers(dest="cmd",required=True)
    run=sub.add_parser("report")
    run.add_argument("evidence",type=Path); run.add_argument("out",type=Path)
    run.add_argument("--page",action="append",type=Path,default=[],help="每个会话下载的页面 JSON，可重复")
    run.add_argument("--usd-per-million",nargs=2,type=Decimal,metavar=("INPUT","OUTPUT"))
    run.add_argument("--blind",action="store_true",help="仅对真实草稿生成两份可离线填写的匿名表")
    m=sub.add_parser("merge");m.add_argument("key",type=Path);m.add_argument("first",type=Path)
    m.add_argument("second",type=Path);m.add_argument("out",type=Path)
    a=p.parse_args()
    try:
        if a.cmd=="merge":
            dump(a.out,merge_votes(read(a.key),[read(a.first),read(a.second)]));return
        paths=[a.evidence,*a.page]
        blobs=[f.read_bytes() for f in paths]
        records=[json.loads(l) for l in blobs[0].decode("utf-8-sig").splitlines() if l.strip()]
        report=reconcile(records,[json.loads(b.decode("utf-8-sig")) for b in blobs[1:]],a.usd_per_million)
        require(not a.blind or report["mode"]=="LIVE","MOCK 不生成质量盲评表")
        report["source_files"]=[{"path":str(f),"sha256":sha(b)} for f,b in zip(paths,blobs)]
        a.out.mkdir()
        dump(a.out/"report.json",report)
        if a.blind:review_forms(report,a.out)
        print(json.dumps(report["summary"],ensure_ascii=False,indent=2))
    except (OSError,ValueError,TypeError,KeyError,IndexError,ArithmeticError) as e:
        p.error(str(e))


if __name__=="__main__":
    main()
