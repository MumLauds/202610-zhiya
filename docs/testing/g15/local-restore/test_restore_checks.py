"""Regression checks for the local evidence collector and the cold-volume guard."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from collect_restore import check_restored_family


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

    def test_new_guardian_login_only_on_restored_volume(self):
        self.check()
        for source_count, target_count in [(2, 2), (1, 1), (1, 3)]:
            with self.subTest(source=source_count, target=target_count):
                self.source["guardianLogins"] = source_count
                self.target["guardianLogins"] = target_count
                with self.assertRaisesRegex(ValueError, "guardian login"):
                    self.check()

    def test_any_restored_supplier_request_fails(self):
        for attempt in [self.report, {"call": "report", "status": 422}]:
            with self.subTest(status=attempt["status"]), self.assertRaisesRegex(ValueError, "supplier requests"):
                self.check([attempt])

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
