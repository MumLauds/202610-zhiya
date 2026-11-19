"""Check the cold restore evidence against the original G15 deployment and archive a summary."""
import argparse
import json
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


def table_counts(counts):
    return {"reading_sessions": sum(counts["sessionsByStatus"].values()),
            "reading_reports": counts["reports"], "interaction_events": counts["events"],
            "request_executions": counts["executions"]}


def check_first_round(baseline, cold, running, *, legacy_event_page=False):
    expected = baseline["database"]
    if legacy_event_page and expected != cold["session"] and expected["pages"] == [1] and len(expected["events"]) == 1:
        # The archived single-page run predates recording pageNumber on its sole STARTED event.
        event = expected["events"][0]
        if event["eventType"] == "STARTED" and "pageNumber" not in event:
            expected = dict(expected, events=[dict(event, pageNumber=1)])
    if not expected == cold["session"] == running["database"]:
        raise ValueError("Restored session differs from the first-round baseline")
    counts = baseline["counts"]
    if running["counts"] != counts:
        raise ValueError("Running restored volume has different reading counts from the first-round baseline")
    if cold["counts"] != table_counts(counts):
        raise ValueError("Cold volume has different reading counts from the first-round baseline")


def check_family_probes(source, target, source_probe, target_probe):
    for label, stopped, running in (("source", source, source_probe), ("restored", target, target_probe)):
        if running["database"] != stopped["session"] or running["loginSessions"] != stopped["guardianLogins"]:
            raise ValueError(f"The {label} family's live session or login count differs from the stopped volume")
        counts = running["counts"]
        if (table_counts(counts) != stopped["counts"]
                or counts["sessionsByStatus"] != {stopped["session"]["status"]: 1}):
            raise ValueError(f"The {label} family's live reading counts differ from the stopped volume or its single session")


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


def check_restored_login_time(browser, started, stopped, *, legacy_browser_time=False):
    http_date = browser["restored"].get("loginHttpDate")
    if http_date:
        verified = parsedate_to_datetime(http_date)
        source = "loginHttpDate"
    elif legacy_browser_time:
        verified = datetime.fromisoformat(browser["verifiedAtUtc"])
        source = "legacyBrowserClock"
    else:
        raise ValueError("Restored HTTPS login response is missing its Date header")
    # HTTP Date and the historical stop marker have second precision.
    if verified.tzinfo is None or not started.replace(microsecond=0) <= verified <= stopped:
        raise ValueError("Login did not occur during this restored deployment")
    return source


def main():
    args = argparse.ArgumentParser()
    args.add_argument("evidence", type=Path)
    args.add_argument("run")
    args.add_argument("output", type=Path)
    args.add_argument("--browser", type=Path, required=True, help="This run's restored Chrome result")
    args.add_argument("--legacy-browser-time", action="store_true", help="Replay historical evidence without a login HTTP Date")
    args.add_argument("--baseline", type=Path, default=os.environ.get("G15_BASELINE") or
                      Path(__file__).resolve().parents[1] / "local-https/deployment-result.json")
    parsed = args.parse_args()
    run = parsed.run

    def load(name):
        return json.loads((parsed.evidence / (name + ".json")).read_bytes())

    previous = json.loads(parsed.baseline.read_bytes())
    historical = "spkiSha256" not in previous.get("browserReadback", {})
    if parsed.legacy_browser_time and not historical:
        args.error("--legacy-browser-time only applies to archived baselines without a TLS pin")
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
    check_first_round(previous["afterBrowserReadback"], a["restoredReading"], load("probe-a"),
                      legacy_event_page=historical)
    a.update(sourceRecheckedUnchanged=True, matchesPreviousDeployment=True)

    b = case("b", run + "-b-source-data", "b-restored-service")
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
    source_probe = load("probe-b-source")
    check_family_probes(source, target, source_probe, load("probe-b"))
    browser = json.loads(parsed.browser.read_bytes())
    if browser["origin"] != previous["origin"]:
        raise ValueError("Browser origin differs from the deployment")
    if browser["restored"]["backendDataVolume"] != b["restoredVolume"]:
        raise ValueError("Browser and backend refer to different restored volumes")
    started = datetime.fromisoformat(load("b-restored-service-backend")["startedAt"].replace("Z", "+00:00"))
    stopped = datetime.fromisoformat((parsed.evidence / "b-stopped-at.txt").read_text().strip().replace("Z", "+00:00"))
    time_source = check_restored_login_time(browser, started, stopped, legacy_browser_time=parsed.legacy_browser_time)
    check_restored_family(source, target, browser, b["illustration"], b["sourceSupplierCalls"], b["restoredSupplierCalls"])
    b["browserReadback"] = {"verifiedAtUtc": browser["verifiedAtUtc"], "loginTimeSource": time_source,
                            "loginHttpDate": browser["restored"].get("loginHttpDate"),
                            "historyCard": browser["restored"]["historyCard"],
                            "sourceLoginsBeforeStop": source_probe["loginSessions"], "backupGuardianLogins": source["guardianLogins"],
                            "restoredGuardianLogins": target["guardianLogins"]}
    result = {"atUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "applicationCommit": previous["commit"],
              "origin": previous["origin"], "images": image_ids, "previousTestVolume": a, "independentFamily": b}
    with parsed.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
