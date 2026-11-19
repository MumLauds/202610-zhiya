import hashlib
import json
import os
import signal
import ssl
import subprocess
import tempfile
import time
from copy import deepcopy
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
import unittest
from unittest.mock import patch

from deployment_baseline import collect, verify_browser_readback
from evidence_io import capture_versions, publish_json
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
    def test_partial_write_can_retry_and_complete_baseline_is_exclusive(self):
        def fail_write(pending, *_args, **_kwargs):
            pending.write_bytes(b"{")
            raise OSError("simulated disk full")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deployment-result.json"
            with patch.object(Path, "write_text", fail_write), self.assertRaises(OSError):
                publish_json(path, {"sequence": 1})
            self.assertFalse(path.exists())
            publish_json(path, {"sequence": 1})
            with self.assertRaises(FileExistsError):
                publish_json(path, {"sequence": 2})
            with patch.object(Path, "write_text", fail_write), self.assertRaises(OSError):
                publish_json(path, {"sequence": 2}, replace=True)
            self.assertEqual(json.loads(path.read_bytes()), {"sequence": 1})
            publish_json(path, {"sequence": 2}, replace=True)
            self.assertEqual(json.loads(path.read_bytes()), {"sequence": 2})

    @unittest.skipUnless(os.name == "posix", "shell publication runs on Linux")
    def test_interrupted_multi_file_publication_requires_a_complete_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.json").write_text("old")
            (root / "b.json").write_text("old")
            command = """
                set -eu
                . "$1"
                begin_capture "$2" probe-b
                printf new > "$capture_dir/a.json"
                printf new > "$capture_dir/b.json"
                mv() {
                    if [ "$FAIL" = yes ] && [ "$3" = "$capture_dir/b.json" ]; then return 1; fi
                    command mv "$@"
                }
                publish_capture
            """
            cmd = ["sh", "-c", command, "check", HERE.parent / "evidence_stage.sh", root]
            broken = subprocess.run(cmd, env=dict(os.environ, FAIL="yes"), capture_output=True)
            self.assertNotEqual(broken.returncode, 0)
            self.assertEqual((root / "a.json").read_text(), "new")
            self.assertEqual((root / "b.json").read_text(), "old")
            with self.assertRaisesRegex(ValueError, "Incomplete capture"):
                capture_versions(root)
            subprocess.run(cmd, env=dict(os.environ, FAIL="no"), check=True)
            self.assertEqual((root / "b.json").read_text(), "new")
            self.assertIn(".capture-status-probe-b", capture_versions(root))

    @unittest.skipUnless(os.name == "posix", "stack.sh runs on the Linux Docker host")
    def test_capture_lock_can_be_reacquired_after_sigkill(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "build-complete").write_text("project commit\n")
            docker = state / "docker"
            docker.write_text('#!/bin/sh\necho entered >> "$G15_STATE/calls"\n'
                              'if [ -f "$G15_STATE/wait" ]; then exec sleep 30; fi\nexit 42\n')
            docker.chmod(0o700)
            wait = state / "wait"
            wait.touch()
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"],
                       G15_PROJECT="project", G15_COMMIT="commit", G15_STATE=tmp)
            cmd = ["sh", HERE / "stack.sh", "baseline"]
            calls = state / "calls"
            with subprocess.Popen(cmd, env=env, start_new_session=True) as first:
                try:
                    deadline = time.monotonic() + 5
                    while not calls.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(calls.exists())
                    busy = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=5)
                    self.assertNotEqual(busy.returncode, 0)
                    self.assertIn("Cannot acquire capture lock", busy.stderr)
                finally:
                    os.killpg(first.pid, signal.SIGKILL)
            wait.unlink()
            retry = subprocess.run(cmd, env=env, capture_output=True, timeout=5)
            self.assertEqual(retry.returncode, 42)
            self.assertEqual(calls.read_text().splitlines(), ["entered", "entered"])

    @unittest.skipUnless(os.name == "posix", "stack.sh runs on the Linux Docker host")
    def test_published_baseline_is_preserved_before_contacting_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "build-complete").write_text("project commit\n")
            files = ("deployment-result.json", "afterBrowserReadback-db.json", "beforeRestart-db.json")
            for name in files:
                (state / name).write_bytes(b"archived")
            env = dict(os.environ, G15_PROJECT="project", G15_COMMIT="commit", G15_STATE=tmp)
            for action in ("baseline", "restart"):
                run = subprocess.run(["sh", HERE / "stack.sh", action], env=env, text=True, capture_output=True)
                self.assertNotEqual(run.returncode, 0)
                self.assertIn("Baseline already published", run.stderr)
                self.assertFalse((state / ".capture-lock").exists())
                for name in files:
                    self.assertEqual((state / name).read_bytes(), b"archived")

    def test_failed_install_cannot_publish_existing_assets(self):
        revision = json.loads((HERE / "deployment-result.json").read_bytes())["commit"]
        actual_run = subprocess.run

        def fail_install(command, **kwargs):
            if command[:2] == ["npm", "ci"]:
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
            for field, value in (("voice", {}), ("readerRefresh", dict(browser["readerRefresh"], status="FINISHED"))):
                with self.subTest(browser_field=field), self.assertRaises(ValueError):
                    collect(state, dict(browser, **{field: value}), *args[2:])
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
