"""Regression checks for the local evidence collector and the cold-volume guard."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from collect_restore import check_family_probes, check_first_round, check_restored_family, check_restored_login_time


HERE = Path(__file__).resolve().parent


class RestoreChecks(unittest.TestCase):
    def setUp(self):
        family = json.loads((HERE / "restore-result.json").read_bytes())["independentFamily"]
        self.source = dict(family["restoredReading"], guardianLogins=1)
        self.target = dict(self.source, guardianLogins=2)
        self.browser = json.loads((HERE / "browser-result.json").read_bytes())
        self.fixture = family["illustration"]
        self.report = {"call": "report", "status": 200, "visitedPages": [1]}

    def check(self, restored_calls=()):
        check_restored_family(self.source, self.target, self.browser, self.fixture, [self.report], restored_calls)

    def test_first_round_counts_match_cold_and_running_volumes(self):
        baseline = json.loads((HERE.parent / "local-https/deployment-result.json").read_bytes())["afterBrowserReadback"]
        cold = json.loads((HERE / "restore-result.json").read_bytes())["previousTestVolume"]["restoredReading"]
        running = {"database": baseline["database"], "counts": baseline["counts"]}
        check_first_round(baseline, cold, running)

        for key in cold["counts"]:
            with self.subTest(cold_table=key):
                modified = dict(cold["counts"])
                modified[key] += 1
                with self.assertRaisesRegex(ValueError, "Cold volume"):
                    check_first_round(baseline, dict(cold, counts=modified), running)
        for key, value in (("reports", 2), ("events", 2), ("executions", 3), ("sessionsByStatus", {"CREATED": 1})):
            with self.subTest(running_count=key):
                modified = dict(running["counts"], **{key: value})
                with self.assertRaisesRegex(ValueError, "Running restored volume"):
                    check_first_round(baseline, cold, dict(running, counts=modified))

    def test_new_guardian_login_only_on_restored_volume(self):
        self.check()
        for source_count, target_count in [(2, 2), (1, 1), (1, 3)]:
            with self.subTest(source=source_count, target=target_count):
                self.source["guardianLogins"] = source_count
                self.target["guardianLogins"] = target_count
                with self.assertRaisesRegex(ValueError, "guardian login"):
                    self.check()

    def test_archived_first_round_can_verify_its_known_start_page_on_a_new_probe(self):
        baseline = json.loads((HERE.parent / "local-https/deployment-result.json").read_bytes())["afterBrowserReadback"]
        cold = json.loads((HERE / "restore-result.json").read_bytes())["previousTestVolume"]["restoredReading"]
        event = cold["session"]["events"][0]
        for page in (1, 2):
            session = dict(cold["session"], events=[dict(event, pageNumber=page)])
            running = {"database": session, "counts": baseline["counts"]}
            with self.subTest(page=page):
                if page == 1:
                    check_first_round(baseline, dict(cold, session=session), running, legacy_event_page=True)
                else:
                    with self.assertRaisesRegex(ValueError, "Restored session"):
                        check_first_round(baseline, dict(cold, session=session), running, legacy_event_page=True)
                with self.assertRaisesRegex(ValueError, "Restored session"):
                    check_first_round(baseline, dict(cold, session=session), running)

    def test_family_source_and_restored_live_counts_must_match_each_stopped_volume(self):
        counts = {"sessionsByStatus": {"FINISHED": 1}, "reports": 1, "events": 1, "executions": 2}
        probes = [{"database": cold["session"], "counts": counts, "loginSessions": cold["guardianLogins"]}
                  for cold in (self.source, self.target)]
        check_family_probes(self.source, self.target, *probes)
        for index, label in enumerate(("source", "restored")):
            for key, value in (("reports", 2), ("events", 2), ("executions", 3),
                               ("sessionsByStatus", {"FINISHED": 2}), ("sessionsByStatus", {"CREATED": 1})):
                with self.subTest(probe=label, counts={key: value}):
                    changed = probes.copy()
                    changed[index] = dict(probes[index], counts=dict(counts, **{key: value}))
                    with self.assertRaisesRegex(ValueError, f"{label} family's live reading counts"):
                        check_family_probes(self.source, self.target, *changed)

    def test_any_restored_supplier_request_fails(self):
        for attempt in [self.report, {"call": "report", "status": 422}]:
            with self.subTest(status=attempt["status"]), self.assertRaisesRegex(ValueError, "supplier requests"):
                self.check([attempt])

    def test_restored_login_uses_server_date_even_when_the_browser_clock_is_wrong(self):
        start = datetime(2026, 11, 19, 13, 0, 0, 720000, timezone.utc)
        stop = start.replace(minute=2, microsecond=0)
        self.browser["verifiedAtUtc"] = "2026-11-20T13:01:00+00:00"
        self.browser["restored"]["loginHttpDate"] = "Thu, 19 Nov 2026 13:00:00 GMT"
        self.assertEqual(check_restored_login_time(self.browser, start, stop), "loginHttpDate")
        self.browser["verifiedAtUtc"] = "2026-11-19T13:01:00+00:00"
        for wrong in ("Thu, 19 Nov 2026 12:59:59 GMT", "Thu, 19 Nov 2026 13:02:01 GMT"):
            with self.subTest(date=wrong), self.assertRaisesRegex(ValueError, "during"):
                self.browser["restored"]["loginHttpDate"] = wrong
                check_restored_login_time(self.browser, start, stop)
        del self.browser["restored"]["loginHttpDate"]
        with self.assertRaisesRegex(ValueError, "missing"):
            check_restored_login_time(self.browser, start, stop)
        self.assertEqual(check_restored_login_time(self.browser, start, stop, legacy_browser_time=True),
                         "legacyBrowserClock")

    def test_new_baseline_rejects_legacy_browser_clock_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = root / "deployment-result.json"
            baseline.write_text(json.dumps({"browserReadback": {"spkiSha256": "current-run-pin"}}))
            result = subprocess.run([sys.executable, "-B", HERE / "collect_restore.py", root, "run", root / "out.json",
                                     "--browser", root / "browser.json", "--baseline", baseline, "--legacy-browser-time"],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("only applies to archived baselines", result.stderr)
            self.assertFalse((root / "out.json").exists())

    def test_matching_incorrect_pixels_do_not_pass(self):
        for stage in ("source", "restored"):
            self.browser[stage]["pageImage"]["rgbaSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "image"):
            self.check()

    @unittest.skipUnless(os.name == "posix", "restore.sh runs on the Linux Docker host")
    def test_fresh_baseline_and_failed_writer_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calls = root / "calls"
            baseline = {"images": {role: {"id": f"sha256:{digit * 64}"} for role, digit in
                                   [("backend", "1"), ("web", "2")]},
                        "containers": {"backend": {"persistentSqliteVolume": "fresh-first-round"}}}
            (root / "baseline.json").write_text(json.dumps(baseline))
            docker = root / "docker"
            docker.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_DOCKER_CALLS"\n'
                'case "$1 $2" in\n'
                ' "image inspect") printf "%s\\n" "$5";;\n'
                ' "volume inspect") test "$3" = fresh-first-round;;\n'
                ' "ps -q") exit "$TEST_PS_STATUS";;\n'
                ' "run --pull") echo /test/certifi;;\n'
                'esac\n'
            )
            docker.chmod(0o700)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                       TEST_DOCKER_CALLS=str(calls), G15_BASELINE=str(root / "baseline.json"),
                       G15_RESTORE_RUN="fresh-run", G15_TEST_TLS=temp, G15_EVIDENCE=temp)
            for ps_status in (1, 0):
                calls.write_text("")
                run = subprocess.run(["sh", HERE / "restore.sh", "backup-a"],
                                     env=dict(env, TEST_PS_STATUS=str(ps_status)), capture_output=True, text=True)
                commands = calls.read_text()
                self.assertIn("ps -q --filter volume=fresh-first-round", commands)
                if ps_status:
                    self.assertNotEqual(run.returncode, 0)
                    self.assertNotIn("volume create", commands)
                else:
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertIn("source=fresh-first-round,target=/src,readonly", commands)


if __name__ == "__main__":
    unittest.main()
