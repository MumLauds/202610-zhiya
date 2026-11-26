"""Create a deterministic per-file freeze manifest; never include credentials or run evidence."""
import hashlib
import json
from pathlib import Path
from coreading import SYSTEM_V72
from provider import PRIMARY_MODEL

ROOT = Path(__file__).resolve().parent
files = {}
for p in sorted(ROOT.rglob("*")):
    name = p.relative_to(ROOT).as_posix()
    if (p.is_file() and p.suffix in {".py",".js",".mjs",".json",".html",".css",".md",".txt"}
            and not any(i.startswith(".") or i in {"__pycache__","node_modules","runs"} for i in Path(name).parts)
            and not name.startswith("docs/")
            and name not in {"v71-run-manifest.json","v72-run-manifest.json","v7-run-manifest.json","integrate_v7.py","v6-run-manifest.json"}):
        files[name] = hashlib.sha256(p.read_bytes()).hexdigest()
doc = {"version":"7.2","model":PRIMARY_MODEL,"region":"cn-beijing","protocol":"B",
       "system_prompt_sha256":hashlib.sha256(SYSTEM_V72.encode()).hexdigest(),
       "question_format":"Separately check reply and follow-up; preserve unchanged standalone replies when only follow-up fails",
       "temperature":0.6,"max_tokens":512,"enable_thinking":False,"provider_timeout_s":60,
       "files":files}
with (ROOT/"v72-run-manifest.json").open("x",encoding="utf-8") as out:
    json.dump(doc,out,ensure_ascii=False,indent=2)
    out.write("\n")

