import hashlib
import json
import ssl
import subprocess
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
import unittest
from unittest.mock import patch

from deployment_baseline import collect, verify_browser_readback
from prepare_frontend import prepare

HERE = Path(__file__).resolve().parent
PIN = "this-run-public-key"
CERTIFICATE = b"this-run-certificate"
CERTIFICATE_SHA256 = hashlib.sha256(CERTIFICATE).hexdigest()


def browser_evidence():
    browser = json.loads((HERE / "browser-result.json").read_bytes())
    browser.update(spkiSha256=PIN, certificateSha256=CERTIFICATE_SHA256)
    browser["afterRestart"]["loginHttpDate"] = format_datetime(datetime.fromisoformat(browser["atUtc"]), usegmt=True)
    return browser


class DeploymentTests(unittest.TestCase):
    def test_failed_install_cannot_publish_existing_assets(self):
        revision = json.loads((HERE / "deployment-result.json").read_bytes())["commit"]
        actual_run = subprocess.run

        def fail_install(command, **kwargs):
            if command == ["npm", "ci"]:
                raise subprocess.CalledProcessError(1, command)
            return actual_run(command, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "frontend-build"
            with patch("prepare_frontend.shutil.which", return_value="npm"), patch("subprocess.run", side_effect=fail_install):
                with self.assertRaises(subprocess.CalledProcessError):
                    prepare(revision, output, None)
            self.assertFalse((output / "commit").exists())
            self.assertFalse((output / "dist").exists())
            (output / "dist").mkdir()
            (output / "dist/index.html").write_text("stale")
            with self.assertRaises(FileExistsError):
                prepare(revision, output, None)

    def test_requires_post_login_snapshot_without_extra_reading_writes_or_provider_calls(self):
        archive = json.loads((HERE / "deployment-result.json").read_bytes())
        browser = browser_evidence()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "tls").mkdir()
            (state / "tls/cert.pem").write_text(ssl.DER_cert_to_PEM_cert(CERTIFICATE), encoding="ascii")
            for index, phase in enumerate(("beforeRestart", "afterBrowserReadback")):
                record = archive[phase]
                record["database"]["events"][0]["pageNumber"] = 1
                probe = {"database": record["database"], "counts": record["counts"], "loginSessions": 1 + index,
                         "capturedAtUtc": (datetime.fromisoformat(browser["atUtc"]) + timedelta(seconds=index)).isoformat()}
                (state / f"{phase}-db.json").write_text(json.dumps(probe))
                calls = [dict(call, status=200) for call in record["supplierCalls"]]
                (state / f"{phase}-provider.jsonl").write_text("\n".join(json.dumps(call) for call in calls) + "\n")
                for name, container in archive["containers"].items():
                    started = container["startedAtBeforeRestart" if index == 0 else "startedAtAfterRestart"]
                    value = {"image": container["image"], "reference": "fixture:" + name, "started": started,
                             "revision": archive["commit"], "volume": container.get("persistentSqliteVolume", "")}
                    (state / f"{phase}-{name}.json").write_text(json.dumps(value))
            args = (state, browser, archive["project"], archive["commit"], PIN)
            self.assertEqual(collect(*args)["afterBrowserReadback"]["counts"]["reports"], 1)
            dbfile = state / "afterBrowserReadback-db.json"
            original = dbfile.read_text()
            for key in ("loginSessions", "counts"):
                with self.subTest(key=key):
                    changed = json.loads(original)
                    if key == "loginSessions":
                        changed[key] -= 1
                    else:
                        changed[key]["executions"] += 1
                    dbfile.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        collect(*args)
            dbfile.write_text(original)
            with (state / "afterBrowserReadback-provider.jsonl").open("a") as log:
                log.write('{"call": "report", "status": 422}\n')
            with self.assertRaises(ValueError):
                collect(*args)

    def test_matching_certificate_and_server_login_survive_browser_clock_skew(self):
        browser = browser_evidence()
        row = json.loads((HERE / "deployment-result.json").read_bytes())["afterBrowserReadback"]["database"]
        row["events"][0]["pageNumber"] = 1
        at = datetime.fromisoformat(browser["atUtc"])
        args = (row, PIN, CERTIFICATE_SHA256, at - timedelta(seconds=1), at + timedelta(seconds=1))
        for skew in (-36, 36):
            with self.subTest(clock_skew_seconds=skew):
                browser["atUtc"] = (at + timedelta(seconds=skew)).isoformat()
                verify_browser_readback(browser, *args)
        for field in ("spkiSha256", "certificateSha256"):
            with self.subTest(different=field), self.assertRaisesRegex(ValueError, "TLS certificate"):
                verify_browser_readback(dict(browser, **{field: "other-run"}), *args)
        archived = json.loads((HERE / "browser-result.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "TLS certificate"):
            verify_browser_readback(archived, *args)

    def test_same_certificate_still_requires_login_during_this_restart(self):
        browser = browser_evidence()
        row = json.loads((HERE / "deployment-result.json").read_bytes())["afterBrowserReadback"]["database"]
        at = datetime.fromisoformat(browser["atUtc"])
        for start, end in ((at + timedelta(seconds=1), at + timedelta(minutes=1)),
                           (at - timedelta(minutes=1), at - timedelta(seconds=1))):
            with self.subTest(start=start, end=end), self.assertRaisesRegex(ValueError, "outside"):
                verify_browser_readback(browser, row, PIN, CERTIFICATE_SHA256, start, end)

    def test_browser_detail_must_match_the_persisted_session_and_events(self):
        browser = browser_evidence()
        row = json.loads((HERE / "deployment-result.json").read_bytes())["afterBrowserReadback"]["database"]
        row["events"][0]["pageNumber"] = 1
        at = datetime.fromisoformat(browser["atUtc"])
        for field, value in (("session", {"storyVersionId": 12}), ("report", {"summary": "another summary"}),
                             ("events", {"interactionEventId": 9}), ("events", {"pageNumber": 2})):
            with self.subTest(field=field):
                changed = deepcopy(browser)
                for phase in ("beforeRestart", "afterRestart"):
                    item = changed[phase]["detail"][field]
                    (item[0] if field == "events" else item).update(value)
                with self.assertRaisesRegex(ValueError, "differs|differ"):
                    verify_browser_readback(changed, row, PIN, CERTIFICATE_SHA256, at, at)


if __name__ == "__main__":
    unittest.main()
