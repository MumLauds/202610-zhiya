"""Check the cold restore evidence against the original G15 deployment and archive a summary."""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from backup_manifest import manifest_bytes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-https"))
from browser_evidence import check_detail, check_history, check_login
from evidence_io import capture_versions, publish_json


def check_pinned_media(expected, observed, stage):
    prefixes = (".zhiya.sqlite3.media_pins/", ".zhiya.sqlite3.media_pins.bindings/")
    expected_pins = {path: digest for path, digest in expected.items() if path.startswith(prefixes)}
    observed_pins = {path: digest for path, digest in observed.items() if path.startswith(prefixes)}
    if observed_pins != expected_pins:
        raise ValueError(f"{stage}: pinned media files or SHA-256 digests differ")


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

    if source_probe["counts"] != {"sessionsByStatus": {"FINISHED": 1}, "events": 1, "reports": 1, "executions": 2}:
        raise ValueError("Unexpected final reading counts for the independent family")


def check_restored_family(source, target, browser, fixture, source_calls, restored_calls, *, legacy_event_page=False):
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
    events = session["events"]
    if (legacy_event_page and session["pages"] == [1] and len(events) == 1
            and events[0]["eventType"] == "STARTED" and "pageNumber" not in events[0]):
        events = [dict(events[0], pageNumber=1)]
    if any("pageNumber" not in e for e in events):
        raise ValueError("Restored database events are missing pageNumber")
    row = dict(session, events=events)
    for stage in ("source", "restored"):
        record = browser[stage]
        for key in ("login", "historyCard", "detail", "pageImage"):
            if key not in record:
                raise ValueError(f"Missing {stage} browser {key}")
        check_login(record["login"])
        check_history(record["historyCard"], browser["readerHash"], session["sessionId"])
        detail = record["detail"]
        if not isinstance(detail, dict) or not isinstance(detail.get("events"), list):
            raise ValueError(f"Missing {stage} browser detail events")
        check_detail(detail, row, stage)
    for key, value in browser["source"].items():
        if restored[key] != value:
            raise ValueError(f"Browser readback differs for {key}")
    image = restored["pageImage"]
    if (image["http"] != 200 or image["path"] != "/api/v1/media/" + fixture["asset"]
            or image["mime"].split(";")[0] != "image/png" or image["bytes"] != fixture["bytes"]
            or (image["naturalWidth"], image["naturalHeight"]) != (fixture["width"], fixture["height"])
            or image["pngSha256"] != fixture["sha256"] or image["rgbaSha256"] != fixture["rgbaSha256"]):
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
    versions = capture_versions(parsed.evidence)
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
            if spec["image"] != image_ids["web" if role == "edge" else "backend"]:
                raise ValueError(f"{label} {role} image differs from the deployment baseline")
            published = {k: v for k, v in spec["ports"].items() if v}
            expected_ports = {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18443"}]} if role == "edge" else {}
            if published != expected_ports:
                raise ValueError(f"{label} {role} has unexpected published ports: {published}")
        if containers["backend"]["volume"] != volume:
            raise ValueError(f"{label} backend is using the wrong volume")
        if (parsed.evidence / (label + "-network.txt")).read_text().strip() != "true":
            raise ValueError(f"{label} network is not internal")
        if load(label + "-health")["data"]["status"] != "ok":
            raise ValueError(f"{label} service health check failed")
        return {"health": "ok", "backendDataVolume": volume, "backendImage": containers["backend"]["image"],
                "webImage": containers["edge"]["image"], "loopbackOnly": True, "businessNetworkInternal": True}

    def case(letter, source_volume, deployment):
        backup = load(letter + "-backup")
        restored = load(letter + "-restore")
        if backup["manifestSha256"] != restored["manifestSha256"]:
            raise ValueError(f"{letter}: restored manifest SHA-256 differs from the backup")
        for stage, manifest in (("backup", backup["backup"]), ("restored", restored)):
            files = manifest.get("files")
            digest = files.get("zhiya.sqlite3") if isinstance(files, dict) else None
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise ValueError(f"{letter}: missing or invalid {stage} zhiya.sqlite3 SHA-256")
        if backup["backup"]["files"] != restored["files"]:
            raise ValueError(f"{letter}: restored file SHA-256 digests differ from the backup")
        if hashlib.sha256(manifest_bytes(backup["backup"])).hexdigest() != backup["manifestSha256"]:
            raise ValueError(f"{letter}: backup manifest contents differ from the recorded SHA-256")
        original_files = backup["source"]["files"]
        if not re.fullmatch(r"[0-9a-fA-F]{64}", original_files.get("zhiya.sqlite3") or ""):
            raise ValueError(f"{letter}: missing or invalid original zhiya.sqlite3 SHA-256")
        check_pinned_media(original_files, restored["files"], f"{letter} cold restore")
        source = backup["source"]["reading"]
        if source != restored["reading"]:
            raise ValueError(f"Restored reading snapshot differs from the cold source for {letter}")
        if source["integrityCheck"] != "ok" or source["foreignKeyViolations"] != 0:
            raise ValueError(f"{letter}: SQLite integrity or foreign-key check failed")
        dest = run + "-" + letter + "-restored-data"
        return {"sourceVolume": source_volume, "backupVolume": run + "-" + letter + "-backup",
                "restoredVolume": dest, "originalFileSha256": backup["source"]["files"],
                "backupFileSha256": backup["backup"]["files"], "manifestSha256": restored["manifestSha256"],
                "restoredBeforeStartupFileSha256": restored["files"], "restoredReading": source,
                "runningService": service(deployment, dest),
                "runningServiceSessionMatches": True}

    a = case("a", original, "a-service")
    if load("a-backup")["source"] != load("original-after"):
        raise ValueError("Original A volume changed after backup")
    a_probe = load("probe-a")
    check_first_round(previous["afterBrowserReadback"], a["restoredReading"], a_probe,
                      legacy_event_page=historical)
    if (a_probe["loginSessions"] != a["restoredReading"]["guardianLogins"]
            or previous["afterBrowserReadback"].get("loginSessions", a_probe["loginSessions"]) != a_probe["loginSessions"]):
        raise ValueError("First-round guardian login count changed during backup or recovery")
    a.update(sourceRecheckedUnchanged=True, matchesPreviousDeployment=True)

    b = case("b", run + "-b-source-data", "b-restored-service")
    b["sourceService"] = service("b-source-service", b["sourceVolume"])
    b["restoredSourceLookup"] = load("b-restored-origin")
    if b["restoredSourceLookup"] != {"fixtureSourceExists": False, "imageAvailableInMediaRoot": False}:
        raise ValueError("Restored image source lookup does not confirm both source files are absent")
    b["illustration"] = load("b-image")
    image = b["illustration"]["sha256"]
    files = b["backupFileSha256"]
    if files.get(".zhiya.sqlite3.media_pins/blobs/" + image + ".png") != image:
        raise ValueError("Pinned image SHA-256 differs from the fixture")
    if (".zhiya.sqlite3.media_pins.bindings/11.sha256" not in files
            or ".zhiya.sqlite3.media_pins/versions/11.json" not in files):
        raise ValueError("Backup is missing the pinned image binding or version")
    original_image = "fixture-source/g15-restore-paper-boat.png"
    if b["originalFileSha256"].get(original_image) != image or original_image in files:
        raise ValueError("Expected the fixture source image only in the original volume")
    b["sourceSupplierCalls"] = [json.loads(line) for line in
                                (parsed.evidence / "probe-b-source-supplier.txt").read_text().splitlines() if line]
    b["restoredSupplierCalls"] = [json.loads(line) for line in
                                  (parsed.evidence / "probe-b-supplier.txt").read_text().splitlines() if line]
    if load("b-source-after") != load("b-backup")["source"]:
        raise ValueError("Source volume changed after backup")
    source = b["restoredReading"]
    stopped_target = load("b-target-after")
    check_pinned_media(b["backupFileSha256"], stopped_target["files"], "b stopped restored volume")
    target = stopped_target["reading"]
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
    check_restored_family(source, target, browser, b["illustration"], b["sourceSupplierCalls"], b["restoredSupplierCalls"],
                          legacy_event_page=parsed.legacy_browser_time)
    b["browserReadback"] = {"verifiedAtUtc": browser["verifiedAtUtc"], "loginTimeSource": time_source,
                            "loginHttpDate": browser["restored"].get("loginHttpDate"),
                            "historyCard": browser["restored"]["historyCard"],
                            "sourceLoginsBeforeStop": source_probe["loginSessions"], "backupGuardianLogins": source["guardianLogins"],
                            "restoredGuardianLogins": target["guardianLogins"]}
    result = {"atUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "applicationCommit": previous["commit"],
              "origin": previous["origin"], "images": image_ids, "previousTestVolume": a, "independentFamily": b}
    if capture_versions(parsed.evidence) != versions:
        raise ValueError("Capture changed while reading the evidence, retry the collector")
    publish_json(parsed.output, result)


if __name__ == "__main__":
    main()
