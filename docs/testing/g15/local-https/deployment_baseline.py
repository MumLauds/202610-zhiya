"""Compare snapshots bracketing the restart and fresh browser login, then publish the restore baseline."""
import argparse
import hashlib
import json
import ssl
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from browser_evidence import EDITED, TRANSCRIPT, check_detail, check_history, check_login
from evidence_io import capture_versions, publish_json


def verify_browser_readback(browser, row, spki, certificate_sha256, restarted_at, captured_at):
    if not spki or browser.get("spkiSha256") != spki or browser.get("certificateSha256") != certificate_sha256:
        raise ValueError("Browser report does not match this deployment's TLS certificate")
    # The HTTPS server and the database snapshot use the Docker host clock. HTTP Date has second precision.
    login_date = parsedate_to_datetime(browser["afterRestart"]["loginHttpDate"])
    if not restarted_at.replace(microsecond=0) <= login_date <= captured_at:
        raise ValueError("Browser login response falls outside this restart and final database snapshot")
    detail = browser["afterRestart"]["detail"]
    check_detail(detail, row, "post-restart")


def collect(state, browser, project, revision, spki):
    versions = capture_versions(state)
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
    before_browser, after_browser = browser["beforeRestart"], browser["afterRestart"]
    for record in (before_browser, after_browser):
        check_login(record)
        check_history(record["history"], browser["readerHash"], row["sessionId"])
    if any(before_browser[key] != after_browser[key] for key in ("frontendAsset", "frontendSha256")):
        raise ValueError("Frontend asset changed during restart")
    active = browser["readerRefresh"]
    if (active["readingSessionId"], active["storyVersionId"], active["status"], active["revision"],
            active["visitedPageNumbers"]) != (row["sessionId"], row["versionId"], "IN_PROGRESS", 1, [1]):
        raise ValueError("Refresh did not restore the active reading session")
    voice = browser.get("voice", {})
    if (voice.get("transcriptionHttp"), voice.get("recognized"), voice.get("confirmedInDialog")) != (200, TRANSCRIPT, EDITED):
        raise ValueError("Missing successful transcription and edited confirmation")
    asr, report = after["supplierCalls"]
    if (asr.get("hz"), asr.get("channels"), asr.get("bits")) != (16000, 1, 16) or not isinstance(asr.get("frames"), int) or asr["frames"] <= 0:
        raise ValueError("Unexpected ASR recording format or frame count")
    if report.get("visitedPages") != [1]:
        raise ValueError("Supplier report differs from the visited page")
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
    restarted_at = datetime.fromisoformat(result["containers"]["backend"]["startedAtAfterRestart"].replace("Z", "+00:00"))
    captured_at = datetime.fromisoformat(after["capturedAtUtc"])
    certificate = ssl.PEM_cert_to_DER_cert((state / "tls" / "cert.pem").read_text(encoding="ascii"))
    certificate_sha256 = hashlib.sha256(certificate).hexdigest()
    verify_browser_readback(browser, row, spki, certificate_sha256, restarted_at, captured_at)
    result["browserReadback"] = {"completedAtUtc": browser["atUtc"], "databaseSnapshotAtUtc": captured_at.isoformat(),
                                 "loginHttpDate": browser["afterRestart"]["loginHttpDate"],
                                 "spkiSha256": spki, "certificateSha256": certificate_sha256}
    if capture_versions(state) != versions:
        raise ValueError("Capture changed while reading the evidence, retry the collector")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("browser", type=Path)
    parser.add_argument("project")
    parser.add_argument("revision")
    parser.add_argument("spki", help="SHA-256 public key pin calculated from this deployment's TLS certificate")
    args = parser.parse_args()
    report = collect(args.state, json.loads(args.browser.read_bytes()), args.project, args.revision, args.spki)
    publish_json(args.state / "deployment-result.json", report)
    print("Verified post-readback deployment-result.json")
