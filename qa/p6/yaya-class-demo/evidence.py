"""Optional local capture for the synthetic nut demo. No credentials or HTTP headers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from provider import _strict_json


class EvidenceRecorder:
    def __init__(self, directory, mode):
        directory.mkdir(mode=0o700)
        self.path = directory / "evidence.jsonl"
        self.mode = mode
        self.writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evidence")
        self.failed = False

    def add(self, event, session, **fields):
        if event == "request":
            fields["system_prompt_sha256"] = sha256(fields["messages"][0]["content"].encode()).hexdigest()
        if event == "upstream":
            result = fields.pop("provider_result")
            fields.update({k: result.get(k) for k in (
                "request_body","status","error_type","text","raw_body","raw_body_sha256","usage",
                "model_requested","model_returned","request_id","response_id","http_status",
                "stop_type","headers_ms","body_ms","parse_ms","call_ms")})
            try: fields["parsed_model_json"] = _strict_json(fields["text"])
            except (ValueError, TypeError, RecursionError): fields["parsed_model_json"] = None
        row = {"event":event,"session_id":session,"mode":self.mode,
               "time_utc":datetime.now(timezone.utc).isoformat(),**fields}
        self.writer.submit(self._write,row)

    def _write(self,row):
        try:
            with self.path.open("a",encoding="utf-8") as out:
                out.write(json.dumps(row,ensure_ascii=True)+"\n")
        except OSError:
            self.failed = True

    def close(self):
        self.writer.shutdown(wait=True)
        if self.failed:
            raise OSError("本次证据未完整写入，请检查本地目录")


def build_report(events, timings):
    if not events:
        raise ValueError("没有纽扣实验记录")
    mode=events[0]["mode"]
    if any(e["mode"]!=mode for e in events) or timings.get("mode")!=mode:
        raise ValueError("服务记录和页面导出的模式不一致")
    trials={}
    for event in events:
        trial=trials.setdefault(event["session_id"],{})
        name=event["event"]
        if name in trial:
            raise ValueError("同一会话的事件重复")
        trial[name]=event
    for row in timings["timings"]:
        if row["card"]!="dev-06": continue
        if row.get("session_id") not in trials:
            raise ValueError("页面记录不属于本批次")
        trial=trials[row["session_id"]]
        if "page" in trial: raise ValueError("页面会话重复")
        if row.get("choice_id") != {"A":"1","B":"2"}[trial["request"]["branch_id"]]:
            raise ValueError("页面选项与服务器记录不一致")
        trial["page"]=row

    rows=[]
    for session,trial in trials.items():
        req=trial["request"]
        upstream=trial.get("upstream",{})
        model_json=upstream.get("parsed_model_json")
        usage=upstream.get("usage") or {}
        page=trial.get("page")
        model_fields=model_json if isinstance(model_json,dict) else {}
        rows.append({
            "case_id":req["case_id"],"branch_id":req["branch_id"],"session_id":session,
            "model_id":req["model_id"],"request_id":upstream.get("request_id"),
            "book_text":req["book_text"],"demo_evidence":req["demo_evidence"],"preset":req["preset"],
            "child":req["child"],"system_prompt_sha256":req["system_prompt_sha256"],"messages":req["messages"],
            "provider_response":upstream or None,"parsed_model_json":model_json,
            **{k:model_fields.get(k) for k in ("reply","next_action","followup_question")},
            "input_tokens":usage.get("prompt_tokens"),"output_tokens":usage.get("completion_tokens"),
            "api_ms":upstream.get("call_ms"),"page_ms":page["ms"] if page else None,
            "server_receipt":trial.get("server_receipt",{}).get("receipt"),
            "final_display":page,"independent_review":{"status":"PENDING"},
        })
    coverage={b:sum(r["branch_id"]==b and r["provider_response"] is not None for r in rows) for b in ("A","B")}
    fingerprints=set()
    for row in rows:
        system,user=row["messages"]
        shared=_strict_json(user["content"])
        if shared.pop("本轮孩子说")!=row["child"]:
            raise ValueError("原话与请求不一致")
        sent=(row["provider_response"] or {}).get("request_body") or {}
        params={k:v for k,v in sent.items() if k!="messages"}
        fingerprints.add(json.dumps([system,shared,params],sort_keys=True,ensure_ascii=True))
    def total(key):
        values=[r[key] for r in rows if type(r[key]) is int]
        return {"sum":sum(values) if values else None,"reported_rows":len(values)}
    return {"mode":mode,"observed_upstream_results":coverage,
            "paired_context_and_parameters_match":len(fingerprints)==1 if all(coverage.values()) else None,
            "input_tokens":total("input_tokens"),"output_tokens":total("output_tokens"),"trials":rows}


def main():
    p=argparse.ArgumentParser(description="本地合并合成实验与浏览器的计时记录")
    p.add_argument("evidence",type=Path)
    p.add_argument("page_json",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    try:
        data=build_report([json.loads(line) for line in a.evidence.read_text(encoding="utf-8").splitlines()],
                          json.loads(a.page_json.read_text(encoding="utf-8-sig")))
        with a.output.open("x",encoding="utf-8") as out:
            json.dump(data,out,ensure_ascii=False,indent=2)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        p.error(str(exc))


if __name__=="__main__":
    main()
