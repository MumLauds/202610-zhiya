
"""Re-project historical LIVE drafts only; never generate new model text."""
from collections import Counter
import argparse
import json
from pathlib import Path
from hashlib import sha256
from coreading import project_v72


def replay(blob, version):
    requests, upstream, receipts, used, rows = {}, {}, {}, {}, []
    for line in blob.splitlines():
        e = json.loads(line)
        if e["mode"] != "LIVE":
            raise ValueError("Expected an original LIVE journal")
        index = {"request":requests,"upstream":upstream,"server_receipt":receipts}.get(e["event"])
        if index is not None:
            key = (e["session_id"], e["turn_number"])
            if key in index:
                raise ValueError("Duplicate event")
            index[key] = e
    for key, sr in receipts.items():
        r = sr["receipt"]
        prior = used.setdefault((sr["session_id"],sr["visit_id"]), [])
        if key in requests and key in upstream:
            req,u = requests[key],upstream[key]
            available = u if u["time_utc"] <= sr["time_utc"] else {"status":r["provider_status"]}
            decision = {}
            body, draft, reason = project_v72(available, prior, decision)
            rows.append(dict(case_id=r["case_id"],turn=r["number"]+1,request_id=u.get("request_id"),
                prompt_hash=req["system_prompt_sha256"],context_hash=sha256(req["messages"][-1]["content"].encode()).hexdigest(),
                saved_raw_text=u.get("text"), original=r,
                replay_v73=dict(draft=draft,decision=decision,spoken=body,fallback_reason=reason)))
        q=(r["spoken"] or {}).get("followup_question")
        if q: prior.append(q.strip())
    return dict(version=version, source_sha256=sha256(blob).hexdigest(), count=len(rows), excluded=len(receipts)-len(rows),
                original_format=sum(p["original"]["fallback_reason"]=="QUESTION_FORMAT" for p in rows),
                replay_format=sum(p["replay_v73"]["fallback_reason"]=="QUESTION_FORMAT" for p in rows),
                decisions=dict(Counter(p["replay_v73"]["decision"]["kind"] for p in rows)),rows=rows)


if __name__ == "__main__":
    from zipfile import ZipFile
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--v71-evidence-zip",type=Path,required=True)
    p.add_argument("--v72-journal",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    with ZipFile(a.v71_evidence_zip) as z:
        batches=[replay(z.read(f"history-v71/{scope}/original/v7-evidence.jsonl"),"v7.1-"+scope)
                 for scope in ("development","special","holdout")]
    batches.append(replay(a.v72_journal.read_bytes(),"v7.2"))
    with a.out.open("x",encoding="utf-8") as f:
        json.dump({"kind":"OFFLINE_HISTORICAL_NO_MODEL_CALLS","batches":batches},f,ensure_ascii=False,indent=2)
    print(json.dumps([{k:v for k,v in b.items() if k!="rows"} for b in batches],indent=2))
