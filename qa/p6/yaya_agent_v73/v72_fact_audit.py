"""Build reproducible v7.2 offline review pack from frozen diagnostics. Never imported by demo."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import argparse, csv, json

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "docs" / "v72_fact_material"
ISSUE = {"WRONG_ATTRIBUTE","WRONG_ACTOR","EVENT_ORDER","UNREAD_EVENT","UNSUPPORTED_FACT",
         "UNSUPPORTED_PREMISE","MISATTRIBUTED_VIEW","IMAGINATION_AS_FACT"}
INTERACTION = {"HIDDEN_QUESTION","UNWANTED_QUESTION","DUPLICATE_QUESTION"}
def load(name):
    return json.loads((DATA/name).read_text(encoding="utf-8"))
def lines(name):
    return [json.loads(l) for l in (DATA/name).read_text(encoding="utf-8").splitlines() if l.strip()]
def evidence(fixture):
    rows = load("v72_fact_evidence_review.json")
    original = {(b["id"],f'{p["id"]}.e{i}'):(e,p["text"]) for b in fixture["books"] for p in b["pages"]
                for i,e in enumerate(p["story_events"],1)}
    assert len(rows)==len(original)==28 and len({(r["book"],r["ref"]) for r in rows})==28
    for r in rows:
        row,page = original[r["book"],r["ref"]]
        assert row==r["original"] and page==r["page_text"] and row[4] in page and r["human_signoff"] is None
        if r["candidate"] is not None:
            assert row!=r["candidate"] and r["candidate"][4] in page
    return rows
def valid_prediction(p, req):
    messages=req["messages"]
    actual=sha256((messages[0]["content"]+"\n"+messages[1]["content"]).encode()).hexdigest()
    if p["input_sha256"] != req["input_sha256"] or actual != req["input_sha256"]: raise ValueError("source mismatch "+p["id"])
    draft=json.loads(req["messages"][1]["content"])["draft"]
    finds=p["findings"]; decision=p["decision"]
    if decision not in {"ACCEPT","REJECT","UNSURE"} or not isinstance(finds,list): raise ValueError("judgment")
    if (decision=="REJECT" and not finds) or (decision=="ACCEPT" and finds): raise ValueError("finding count")
    for f in finds:
        if f["code"] not in (ISSUE|INTERACTION) or f["target"] not in {"reply","followup_question"} or not isinstance(f["span"],str) or not f["span"].strip() or f["span"] not in (draft[f["target"]] or ""): raise ValueError("unanchored or unknown finding")
    return decision, {f["code"] for f in finds}
def score(*, confirmations=None, prediction_rows=None):
    reqs={r["id"]:r for r in lines("v72_fact_blind_inputs.jsonl")}
    refs={r["id"]:r for r in load("v72_fact_triage.json")}
    pred=lines("v72_fact_blind_desk.jsonl") if prediction_rows is None else prediction_rows
    if len(set(r["id"] for r in pred))!=len(pred) or any(r["id"] not in reqs for r in pred): raise ValueError("unknown or duplicate prediction")
    got={r["id"]:valid_prediction(r,reqs[r["id"]]) for r in pred}
    checked={}
    if confirmations:
        with Path(confirmations).open(encoding="utf-8-sig",newline="") as f:
            for row in csv.DictReader(f):
                ident=row["id"]
                if ident in checked or ident not in reqs or row["input_sha256"]!=reqs[ident]["input_sha256"]: raise ValueError("invalid signoff binding")
                if row["human_decision"] not in {"", "USE_PROPOSED", "USE_EDITED", "UNSURE"}: raise ValueError("human decision")
                if row["human_decision"] in {"USE_PROPOSED","USE_EDITED"}:
                    if not row["reviewer"].strip(): raise ValueError("unsigned")
                    if row["human_decision"]=="USE_EDITED" and not row["verified_issue_codes"].strip(): raise ValueError("SPECIFY NO_ISSUE or edit the codes")
                    codes=(refs[ident]["codes"] if row["human_decision"]=="USE_PROPOSED" else ([] if row["verified_issue_codes"]=="NO_ISSUE" else row["verified_issue_codes"].split("|")))
                    if set(codes)-(ISSUE|INTERACTION):raise ValueError("invalid human codes")
                    checked[ident]=set(codes)
                else: checked[ident]=None
    def measures(labels):
        groups={k:{"n":0,"errors":[],"excluded":[]} for k in ["fact_miss","safe_false_reject","attribute_miss","actor_miss","timeline_miss","imagination_false_reject","natural_false_reject","hidden_question_miss"]}
        for ident,ref in refs.items():
            gold=labels.get(ident)
            if gold is None: continue
            is_fact=bool(gold & ISSUE)
            eligible={}
            if is_fact:eligible["fact_miss"]=ISSUE
            if "WRONG_ATTRIBUTE" in gold:eligible["attribute_miss"]={"WRONG_ATTRIBUTE"}
            if "WRONG_ACTOR" in gold:eligible["actor_miss"]={"WRONG_ACTOR"}
            if gold & {"EVENT_ORDER","UNREAD_EVENT"}:eligible["timeline_miss"]={"EVENT_ORDER","UNREAD_EVENT"}
            if "HIDDEN_QUESTION" in gold:eligible["hidden_question_miss"]={"HIDDEN_QUESTION"}
            if not gold:
                eligible["safe_false_reject"]=None
                if ref["category"]=="imagination":eligible["imagination_false_reject"]=None
                if ref["category"] in {"natural_listening","natural_quotation"}:eligible["natural_false_reject"]=None
            for key,target in eligible.items():
                m=groups[key]; ret=got.get(ident)
                if ret is None or ret[0]=="UNSURE":m["excluded"].append(ident);continue
                decision,codes=ret;m["n"]+=1
                if ((decision=="REJECT") if target is None else not (decision=="REJECT" and codes&target)):m["errors"].append(ident)
        return {k:{"numerator":len(m["errors"]),"denominator":m["n"],"rate":len(m["errors"])/m["n"] if m["n"] else None,"error_ids":m["errors"],"abstained_or_missing_ids":m["excluded"]} for k,m in groups.items()}
    return {"review_type":"independent model desk audit, not Qwen or human verification","prediction_count":len(got),"prediction_status":dict(Counter(d for d,c in got.values())), "human_signed_count":sum(c is not None for c in checked.values()),"human_reference_metrics":measures(checked),"desk_vs_provisional_only":measures({i:set(r["codes"]) for i,r in refs.items()})}
if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--human",type=Path);args=p.parse_args()
    evidence(json.loads((DATA/"v72_fact_book_sources.json").read_text(encoding="utf8")))
    print(json.dumps(score(confirmations=args.human),ensure_ascii=False,indent=2))
