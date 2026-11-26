"""Offline projection of v7 live drafts. Does not regenerate or change any history."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile

from coreading import project

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("v7_archive", type=Path)
p.add_argument("output", type=Path)
a = p.parse_args()
out = []
with ZipFile(a.v7_archive) as archive:
    for batch in ("development", "special", "holdout"):
        used, upstream = {}, {}
        with archive.open(f"experiments/yaya-v7-live/{batch}/record/v7-evidence.jsonl") as stream:
            for line in stream:
                row = json.loads(line)
                visit = row["session_id"], row["visit_id"]
                key = visit, row["turn_number"]
                if row["event"] == "upstream":
                    upstream[key] = row
                elif row["event"] == "server_receipt":
                    receipt = row["receipt"]
                    questions = used.setdefault(visit, [])
                    if receipt.get("fallback_reason") == "QUESTION_FORMAT":
                        model = upstream[key]
                        spoken, draft, reason = project(model, questions, reported_quotes=True)
                        out.append({"batch":batch, "case_id":row["case_id"], "turn_number":row["turn_number"],
                                    "request_id":model.get("request_id"), "raw_draft":model["text"],
                                    "actual_v7_receipt":receipt, "v71_counterfactual_reason":reason,
                                    "v71_counterfactual_display":spoken})
                    if receipt.get("kind") == "MODEL" and (q := (receipt.get("spoken") or {}).get("followup_question")):
                        questions.append(q)
with a.output.open("x", encoding="utf-8") as file:
    json.dump(out,file,ensure_ascii=False,indent=2)
    file.write("\n")
assert len(out) == 11, len(out)
print("v7 live drafts projected offline:", len(out), "reason changed:", sum(r["v71_counterfactual_reason"] != "QUESTION_FORMAT" for r in out))
