"""Accept only this public repository's validated push/manual main run."""
import json
import sys
from urllib.request import Request, urlopen

repo = "MumLauds/202610-zhiya"
api = f"https://api.github.com/repos/{repo}/actions"
workflow = ".github/workflows/deploy-hk.yml"
sha = sys.argv[1]


def get(path):
    with urlopen(Request(api + path, headers={"User-Agent": "zhiya-hk-deploy"}), timeout=15) as response:
        return json.load(response)


runs = get(f"/runs?branch=main&head_sha={sha}&per_page=30")
for run in runs["workflow_runs"]:
    if (run["path"] != workflow or run["event"] not in ("push", "workflow_dispatch")
        or run["head_sha"] != sha or run["head_branch"] != "main"
        or (run.get("head_repository") or {}).get("full_name") != repo):
        continue
    # Only the newest matching attempt can authorize a switch. A completed
    # or cancelled run must not fall through to an older successful run.
    if run["status"] != "in_progress" or run["conclusion"] is not None:
        break
    jobs = get(f"/runs/{run['id']}/attempts/{run['run_attempt']}/jobs")
    if any(j["name"] == "Validate release" and j["conclusion"] == "success" for j in jobs["jobs"]):
        print(f"{run['id']}-{run['run_attempt']}")
    break
