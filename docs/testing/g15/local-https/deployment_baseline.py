"""Compare snapshots bracketing the restart and fresh browser login, then publish the restore baseline."""
import argparse
import json
from pathlib import Path


def collect(state, browser, project, revision):
    phases = ("beforeRestart", "afterBrowserReadback")
    result = {"commit": revision, "project": project, "origin": "https://127.0.0.1:18443",
              "images": {}, "containers": {}}
    for phase in phases:
        probe = json.loads((state / f"{phase}-db.json").read_bytes())
        logs = (state / f"{phase}-provider.jsonl").read_text(encoding="utf-8").splitlines()
        probe["supplierCalls"] = [json.loads(line) for line in logs if line.strip()]
        result[phase] = probe
    before, after = (result[p] for p in phases)
    if before["database"] != after["database"] or before["counts"] != after["counts"]:
        raise ValueError("Reading rows or counts changed during restart and browser readback")
    if after["counts"] != {"sessionsByStatus": {"FINISHED": 1}, "reports": 1, "events": 1, "executions": 2}:
        raise ValueError("Unexpected final row counts")
    if (before["supplierCalls"] != after["supplierCalls"]
            or [(c["call"], c["status"]) for c in after["supplierCalls"]] != [("asr", 200), ("report", 200)]):
        raise ValueError("Expected one successful ASR and report before restart and no additional requests")
    row = after["database"]
    if (after["loginSessions"] != before["loginSessions"] + 1 or not browser["afterRestart"]["freshLogin"]
            or browser["origin"] != result["origin"] or browser["sessionId"] != row["sessionId"]
            or browser["profileId"] != row["profileId"]
            or browser["afterRestart"]["detail"] != browser["beforeRestart"]["detail"]):
        raise ValueError("Expected a fresh browser login and the same completed reading in this database")
    for service in ("backend", "edge", "provider"):
        old, new = (json.loads((state / f"{phase}-{service}.json").read_bytes()) for phase in phases)
        if (old["started"] == new["started"] or old["image"] != new["image"]
                or new["revision"] != revision or old["volume"] != new["volume"]):
            raise ValueError(f"{service} did not restart with the same image and volume")
        result["containers"][service] = {"image": new["image"], "startedAtBeforeRestart": old["started"],
                                         "startedAtAfterRestart": new["started"]}
        if service in ("backend", "edge"):
            name = "web" if service == "edge" else service
            result["images"][name] = {"id": new["image"], "reference": new["reference"], "revision": revision}
        if service == "backend":
            if new["volume"] != project + "_sqlite-data":
                raise ValueError("Unexpected source volume")
            result["containers"][service]["persistentSqliteVolume"] = new["volume"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("browser", type=Path)
    parser.add_argument("project")
    parser.add_argument("revision")
    args = parser.parse_args()
    report = collect(args.state, json.loads(args.browser.read_bytes()), args.project, args.revision)
    with (args.state / "deployment-result.json").open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print("Verified post-readback deployment-result.json")
