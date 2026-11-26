"""Create a deterministic per-file freeze manifest; never include credentials or run evidence."""
import hashlib
import json
from pathlib import Path
from coreading import SYSTEM_V73
from provider import PRIMARY_MODEL

ROOT = Path(__file__).resolve().parent
# Allow only the v7.2 release inventory and the v7.3 program/fixture files.
# Never recursively hash arbitrary evidence output, even when it is under ROOT.
base = json.loads((ROOT/"v72-run-manifest.json").read_text(encoding="utf-8"))
names = set(base["files"]) | {
    "v72-run-manifest.json", "run_v73.py", "freeze_v73.py",
    "fixtures_v73_diagnostics.json", "fixtures_v73_live.json"
}
names.update(p.name for pattern in ("*v73*.py", "*v73*.mjs") for p in ROOT.glob(pattern))
files = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(names)}
doc = {"version":"7.3","model":PRIMARY_MODEL,"region":"cn-beijing","protocol":"B",
       "system_prompt_sha256":hashlib.sha256(SYSTEM_V73.encode()).hexdigest(),
       "question_format":"Separately check reply and follow-up; preserve unchanged standalone replies when only follow-up fails",
       "temperature":0.6,"max_tokens":512,"enable_thinking":False,"provider_timeout_s":60,
       "files":files}
with (ROOT/"v73-run-manifest.json").open("x",encoding="utf-8", newline="\n") as out:
    json.dump(doc,out,ensure_ascii=False,indent=2)
    out.write("\n")
