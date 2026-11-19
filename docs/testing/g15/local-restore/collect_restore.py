"""Check the cold restore evidence against the original G15 deployment and archive a summary."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def check_first_round(baseline, cold, running):
    if not baseline["database"] == cold["session"] == running["database"]:
        raise ValueError("Restored session differs from the first-round baseline")
    counts = baseline["counts"]
    if running["counts"] != counts:
        raise ValueError("Running restored volume has different reading counts from the first-round baseline")
    expected_tables = {"reading_sessions": sum(counts["sessionsByStatus"].values()),
                       "reading_reports": counts["reports"], "interaction_events": counts["events"],
                       "request_executions": counts["executions"]}
    if cold["counts"] != expected_tables:
        raise ValueError("Cold volume has different reading counts from the first-round baseline")


def check_restored_family(source, target, browser, fixture, source_calls, restored_calls):
    if source_calls != [{"call": "report", "status": 200, "visitedPages": [1]}] or restored_calls:
        raise ValueError("Expected one successful source report and no restored supplier requests")
    if (source["guardianLogins"], target["guardianLogins"]) != (1, 2):
        raise ValueError("Expected one source guardian login and a second login on the restored volume")
    if target["session"] != source["session"] or target["counts"] != source["counts"]:
        raise ValueError("Reading history changed during restored browser readback")
    session = target["session"]
    restored = browser["restored"]
    if not restored["newBrowser"] or restored["anonymousMediaHttp"] != 401 or restored["login"]["loginHttp"] != 201:
        raise ValueError("Missing anonymous media check or fresh browser login")
    if browser["profileId"] != session["profileId"] or browser["sessionId"] != session["sessionId"]:
        raise ValueError("Browser and database refer to different reading records")
    for key, value in browser["source"].items():
        if restored[key] != value:
            raise ValueError(f"Browser readback differs for {key}")
    detail = restored["detail"]
    if (detail["session"]["readingSessionId"] != session["sessionId"]
            or detail["session"]["visitedPageNumbers"] != session["pages"]
            or detail["report"]["summary"] != session["summary"]
            or detail["report"]["status"] != session["reportStatus"]):
        raise ValueError("Browser readback does not match the restored reading record")
    image = restored["pageImage"]
    if (image["http"] != 200 or image["pngSha256"] != fixture["sha256"]
            or image["rgbaSha256"] != fixture["rgbaSha256"]):
        raise ValueError("Browser image does not match the fixture")


def main():
    args = argparse.ArgumentParser()
    args.add_argument("evidence", type=Path)
    args.add_argument("run")
    args.add_argument("output", type=Path)
    args.add_argument("--browser", type=Path, required=True, help="This run's restored Chrome result")
    args.add_argument("--baseline", type=Path, default=os.environ.get("G15_BASELINE") or
                      Path(__file__).resolve().parents[1] / "local-https/deployment-result.json")
    parsed = args.parse_args()
    run = parsed.run

    def load(name):
        return json.loads((parsed.evidence / (name + ".json")).read_bytes())

    previous = json.loads(parsed.baseline.read_bytes())
    original = previous["containers"]["backend"]["persistentSqliteVolume"]
    image_ids = {role: v["id"] for role, v in previous["images"].items()}

    def service(label, volume):
        containers = {name: load(label + "-" + name) for name in ("edge", "backend", "provider")}
        for role, spec in containers.items():
            assert spec["image"] == image_ids["web" if role == "edge" else "backend"], label
            published = {k: v for k, v in spec["ports"].items() if v}
            assert published == ({"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18443"}]}
                                 if role == "edge" else {}), (label, role, published)
        assert containers["backend"]["volume"] == volume
        assert (parsed.evidence / (label + "-network.txt")).read_text().strip() == "true"
        assert load(label + "-health")["data"]["status"] == "ok"
        return {"health": "ok", "backendDataVolume": volume, "backendImage": containers["backend"]["image"],
                "webImage": containers["edge"]["image"], "loopbackOnly": True, "businessNetworkInternal": True}

    def case(letter, source_volume, deployment):
        backup = load(letter + "-backup")
        restored = load(letter + "-restore")
        assert backup["manifestSha256"] == restored["manifestSha256"]
        assert backup["backup"]["files"] == restored["files"]
        source = backup["source"]["reading"]
        if source != restored["reading"]:
            raise ValueError(f"Restored reading snapshot differs from the cold source for {letter}")
        assert source["session"] == load("probe-" + letter)["database"]
        assert source["integrityCheck"] == "ok" and source["foreignKeyViolations"] == 0
        dest = run + "-" + letter + "-restored-data"
        return {"sourceVolume": source_volume, "backupVolume": run + "-" + letter + "-backup",
                "restoredVolume": dest, "originalFileSha256": backup["source"]["files"],
                "backupFileSha256": backup["backup"]["files"], "manifestSha256": restored["manifestSha256"],
                "restoredBeforeStartupFileSha256": restored["files"], "restoredReading": source,
                "runningService": service(deployment, dest),
                "runningServiceSessionMatches": True}

    a = case("a", original, "a-service")
    assert load("a-backup")["source"] == load("original-after")
    check_first_round(previous["afterBrowserReadback"], a["restoredReading"], load("probe-a"))
    a.update(sourceRecheckedUnchanged=True, matchesPreviousDeployment=True)

    b = case("b", run + "-b-source-data", "b-restored-service")
    assert b["restoredReading"]["session"] == load("probe-b-source")["database"]
    b["sourceService"] = service("b-source-service", b["sourceVolume"])
    b["restoredSourceLookup"] = load("b-restored-origin")
    assert set(b["restoredSourceLookup"].values()) == {False}
    b["illustration"] = load("b-image")
    image = b["illustration"]["sha256"]
    assert b["backupFileSha256"][".zhiya.sqlite3.media_pins/blobs/" + image + ".png"] == image
    assert ".zhiya.sqlite3.media_pins.bindings/11.sha256" in b["backupFileSha256"]
    assert ".zhiya.sqlite3.media_pins/versions/11.json" in b["backupFileSha256"]
    assert "fixture-source/g15-restore-paper-boat.png" in b["originalFileSha256"]
    assert "fixture-source/g15-restore-paper-boat.png" not in b["backupFileSha256"]
    b["sourceSupplierCalls"] = [json.loads(line) for line in
                                (parsed.evidence / "probe-b-source-supplier.txt").read_text().splitlines() if line]
    b["restoredSupplierCalls"] = [json.loads(line) for line in
                                  (parsed.evidence / "probe-b-supplier.txt").read_text().splitlines() if line]
    if load("b-source-after") != load("b-backup")["source"]:
        raise ValueError("Source volume changed after backup")
    source = b["restoredReading"]
    target = load("b-target-after")["reading"]
    before_stop = load("probe-b-source")["loginSessions"]
    after_readback = load("probe-b")["loginSessions"]
    if before_stop != source["guardianLogins"] or after_readback != target["guardianLogins"]:
        raise ValueError("Live and stopped volumes disagree on the independent family's login counts")
    browser = json.loads(parsed.browser.read_bytes())
    if browser["origin"] != previous["origin"]:
        raise ValueError("Browser origin differs from the deployment")
    if browser["restored"]["backendDataVolume"] != b["restoredVolume"]:
        raise ValueError("Browser and backend refer to different restored volumes")
    started = datetime.fromisoformat(load("b-restored-service-backend")["startedAt"].replace("Z", "+00:00"))
    stopped = datetime.fromisoformat((parsed.evidence / "b-stopped-at.txt").read_text().strip().replace("Z", "+00:00"))
    verified = datetime.fromisoformat(browser["verifiedAtUtc"])
    if not started.replace(microsecond=0) <= verified <= stopped:
        raise ValueError("Browser result was not recorded during this restored deployment")
    check_restored_family(source, target, browser, b["illustration"], b["sourceSupplierCalls"], b["restoredSupplierCalls"])
    b["browserReadback"] = {"verifiedAtUtc": browser["verifiedAtUtc"], "historyCard": browser["restored"]["historyCard"],
                            "sourceLoginsBeforeStop": before_stop, "backupGuardianLogins": source["guardianLogins"],
                            "restoredGuardianLogins": target["guardianLogins"]}
    result = {"atUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "applicationCommit": previous["commit"],
              "origin": previous["origin"], "images": image_ids, "previousTestVolume": a, "independentFamily": b}
    with parsed.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
