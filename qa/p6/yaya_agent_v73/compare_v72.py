"""Pair frozen v7.1 original events with replayed projection or new v7.2 events."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

from coreading import project_v72


def hash_json(obj):
    return sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                             separators=(",",":")).encode()).hexdigest()


def journal(paths):
    rows, hashes = {}, {}
    for path in paths:
        hashes[path.name if len(paths)==1 else f"{path.parent.parent.name}/{path.name}"] = sha256(path.read_bytes()).hexdigest()
        for b in path.read_bytes().splitlines():
            e = json.loads(b)
            if e["event"] not in ("request","upstream","server_receipt"):
                continue
            key = (e["case_id"], e["turn_number"])
            row = rows.setdefault(key, {})
            if e["event"] in row:
                raise ValueError(f"duplicate event for {key}")
            if row and e["session_id"] != next(iter(row.values()))["session_id"]:
                raise ValueError(f"mismatched session for {key}")
            row[e["event"]] = e
    return rows, hashes


def item(row):
    r = row["server_receipt"]["receipt"]
    req, answer = row.get("request",{}),row.get("upstream",{})
    messages = req.get("messages")
    return {"request_id":answer.get("request_id"),"http_status":answer.get("http_status"),
            "prompt_hash":req.get("system_prompt_sha256"),"context_hash":hash_json(json.loads(messages[-1]["content"])) if messages else None,
            "input_hash":hash_json(req.get("child")) if messages else None,
            "session_id":r["session"],"page":r["page_id"],"mode":r["mode"],"kind":r["kind"],"provider_status":r["provider_status"],
            "fallback_reason":r["fallback_reason"],"display_decision":r.get("display_decision"),
            "raw_model_text":answer.get("text"),"response_hash":answer.get("raw_body_sha256"),
            "draft":r["draft"],"display":r["spoken"],"usage":answer.get("usage"),
            "api_ms":answer.get("call_ms"),"server_ms":r["server_ms"]}


def summarize(items):
    counts=Counter(i["kind"] for i in items)
    lat=sorted(i["api_ms"] for i in items if isinstance(i["api_ms"], (int,float)))
    usage={key:sum((i["usage"] or {}).get(key,0) or 0 for i in items)
           for key in ("prompt_tokens","completion_tokens","total_tokens")}
    return {"kinds":dict(counts),"format_fallbacks":sum(i["fallback_reason"]=="QUESTION_FORMAT" for i in items),
            "reply_only":sum((i["display_decision"] or {}).get("kind")=="REPLY_ONLY" for i in items),
            "http_200":sum(i["http_status"]==200 for i in items),"usage":usage,
            "api_latency_ms":{"n":len(lat),"p50":median(lat) if lat else None,
                              "p95_nearest_rank_descriptive":lat[(95*len(lat)+99)//100-1] if lat else None}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old",nargs="+",type=Path,required=True)
    p.add_argument("--new",type=Path,help="new journal; omit for exact old-draft presentation replay")
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    old,old_hash=journal(args.old)
    fresh,fresh_hash=journal([args.new]) if args.new else ({},{})
    if args.new and (fresh.keys()-old.keys() or len(fresh)!=16):
        p.error("comparison requires the exact 16 selected case/turn pairs")
    used, rows = {},[]
    for key, records in old.items():
        base = item(records)
        receipt=records["server_receipt"]["receipt"]
        turn_used=used.setdefault((receipt["session"],receipt["visit_id"]),[])
        if not fresh and base["kind"]!="NO_SUBMISSION":
            decision={}
            spoken,draft,reason=project_v72(records.get("upstream",{}),turn_used,decision)
            candidate={**base,"kind":"FALLBACK" if reason else "MODEL",
                       "fallback_reason":reason,"display_decision":decision,"draft":draft,"display":spoken,
                       "mode":"REPLAY","http_status":None,"usage":None,"api_ms":None,"server_ms":None}
        elif key in fresh:
            candidate=item(fresh[key])
            if base["input_hash"] != candidate["input_hash"] or base["page"] != candidate["page"]:
                p.error(f"unmatched paired input {key}")
        else:
            candidate=None
        q=(base["display"] or {}).get("followup_question")
        if q:
            turn_used.append(q.strip())  # original visit state; not a counterfactual dialogue rollout
        if candidate:
            rows.append({"case_id":key[0],"turn":key[1]+1,"v71":base,"v72":candidate})
    report={"kind": "LIVE_PAIRED" if fresh and all(r["v72"]["mode"]=="LIVE" for r in rows)
            else ("MOCK_PAIRED_NOT_MODEL_QUALITY" if fresh else "HISTORICAL_DRAFT_REPLAY_NO_NEW_CALLS"),
            "v71_source_sha256":old_hash,"new_source_sha256":fresh_hash,
            "pair_count":len(rows),"v71_summary":summarize([r["v71"] for r in rows]),
            "v72_summary":summarize([r["v72"] for r in rows]),"pairs":rows}
    with args.out.open("x",encoding="utf-8") as out:
        json.dump(report,out,ensure_ascii=False,indent=2)
        out.write("\n")
    print(json.dumps({k:v for k,v in report.items() if k!="pairs"},ensure_ascii=False))


if __name__=="__main__":
    main()
