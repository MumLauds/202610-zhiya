"""Regression checks for the local evidence collector and the cold-volume guard."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from collect_restore import (
    check_family_probes, check_first_round, check_pinned_media, check_restored_family, check_restored_login_time,
    stopped_supplier_calls,
)


HERE = Path(__file__).resolve().parent


class RestoreChecks(unittest.TestCase):
    def setUp(self):
        family = json.loads((HERE / "restore-result.json").read_bytes())["independentFamily"]
        self.source = dict(family["restoredReading"], guardianLogins=1)
        self.target = dict(self.source, guardianLogins=2)
        self.browser = json.loads((HERE / "browser-result.json").read_bytes())
        self.fixture = family["illustration"]
        self.report = {"call": "report", "status": 200, "visitedPages": [1]}

    def check(self, restored_calls=(), *, legacy_event_page=True):
        check_restored_family(self.source, self.target, self.browser, self.fixture, [self.report], restored_calls,
                              legacy_event_page=legacy_event_page)

    def test_family_events_match_database_even_when_both_browsers_agree(self):
        with self.assertRaisesRegex(ValueError, "pageNumber"):
            self.check(legacy_event_page=False)
        self.target["session"]["events"][0]["pageNumber"] = 1
        self.check(legacy_event_page=False)
        for key, wrong in (("interactionEventId", 101), ("eventType", "PAGE_CHANGED"), ("pageNumber", 2)):
            with self.subTest(key=key):
                events = [self.browser[stage]["detail"]["events"][0] for stage in ("source", "restored")]
                original = events[0][key]
                for event in events:
                    event[key] = wrong
                with self.assertRaisesRegex(ValueError, "Browser events"):
                    self.check(legacy_event_page=False)
                for event in events:
                    event[key] = original

    def test_agreeing_browsers_still_have_to_match_the_record_and_fixture(self):
        for key, value, section in (("storyVersionId", 99, "session"), ("readingSessionId", 2, "report"),
                                    ("naturalWidth", 0, "pageImage"), ("secureSessionCookie", False, "login")):
            with self.subTest(field=key):
                for stage in ("source", "restored"):
                    record = self.browser[stage]
                    target = record["detail"] if section in ("session", "report") else record
                    target[section][key] = value
                with self.assertRaises(ValueError):
                    self.check()
                self.browser = json.loads((HERE / "browser-result.json").read_bytes())

    def test_both_browsers_require_readback_details_and_events(self):
        for side in ("source", "restored"):
            for field in ("detail", "events"):
                with self.subTest(side=side, field=field):
                    record = self.browser[side] if field == "detail" else self.browser[side]["detail"]
                    saved = record.pop(field)
                    try:
                        with self.assertRaisesRegex(ValueError, side):
                            self.check()
                    finally:
                        record[field] = saved

    def test_matching_manifests_still_require_a_valid_database_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for files in ({}, {"zhiya.sqlite3": None}, {"zhiya.sqlite3": "0" * 63}, {"zhiya.sqlite3": "g" * 64}):
                with self.subTest(files=files):
                    (root / "a-backup.json").write_text(json.dumps({"manifestSha256": "m", "backup": {"files": files}}))
                    (root / "a-restore.json").write_text(json.dumps({"manifestSha256": "m", "files": files}))
                    out = root / "out.json"
                    run = subprocess.run([sys.executable, "-O", "-B", HERE / "collect_restore.py", root, "run", out,
                                          "--browser", HERE / "browser-result.json",
                                          "--baseline", HERE.parent / "local-https/deployment-result.json"],
                                         capture_output=True)
                    self.assertNotEqual(run.returncode, 0)
                    self.assertIn(b"zhiya.sqlite3 SHA-256", run.stderr)
                    self.assertFalse(out.exists())

    def test_stopped_volume_keeps_all_pinned_files_after_login(self):
        backup = json.loads((HERE / "restore-result.json").read_bytes())["independentFamily"]["backupFileSha256"]
        stopped = dict(backup, **{"zhiya.sqlite3": "0" * 64})
        check_pinned_media(backup, stopped, "final")
        for path in backup:
            if path == "zhiya.sqlite3":
                continue
            for change in ("missing", "changed"):
                with self.subTest(path=path, change=change):
                    files = stopped.copy()
                    if change == "missing":
                        del files[path]
                    else:
                        files[path] = "f" * 64
                    with self.assertRaisesRegex(ValueError, "final: pinned media"):
                        check_pinned_media(backup, files, "final")

    def test_wrong_restored_digest_fails_in_normal_and_optimized_python(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = {"zhiya.sqlite3": "1" * 64}
            (root / "a-backup.json").write_text(json.dumps({"manifestSha256": "m", "backup": {"files": files}}))
            (root / "a-restore.json").write_text(json.dumps({"manifestSha256": "m", "files": {"zhiya.sqlite3": "0" * 64}}))
            output = root / "summary.json"
            for flags in ([], ["-O"]):
                with self.subTest(flags=flags):
                    result = subprocess.run([sys.executable, *flags, "-B", HERE / "collect_restore.py", root,
                                             "run", output, "--browser", HERE / "browser-result.json",
                                             "--baseline", HERE.parent / "local-https/deployment-result.json"],
                                            capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b"restored file SHA-256", result.stderr)
                    self.assertFalse(output.exists())

    def test_matching_file_digests_must_match_recorded_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {"files": {"zhiya.sqlite3": "1" * 64}, "directories": []}
            digest = hashlib.sha256((json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()).hexdigest()
            manifest["files"]["zhiya.sqlite3"] = "0" * 64
            (root / "a-backup.json").write_text(json.dumps({"backup": manifest, "manifestSha256": digest}))
            (root / "a-restore.json").write_text(json.dumps({"files": manifest["files"], "manifestSha256": digest}))
            for flags in ([], ["-O"]):
                with self.subTest(flags=flags):
                    output = root / "summary.json"
                    result = subprocess.run([sys.executable, *flags, "-B", HERE / "collect_restore.py", root,
                                             "run", output, "--browser", HERE / "browser-result.json",
                                             "--baseline", HERE.parent / "local-https/deployment-result.json"],
                                            capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b"manifest contents", result.stderr)
                    self.assertFalse(output.exists())

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

    def test_source_report_must_already_be_present_at_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stopped = root / "b-source-stopped-supplier.txt"
            probe = root / "probe-b-source-supplier.txt"
            stopped.write_text(json.dumps(self.report) + "\n")
            probe.write_text("")
            with self.assertRaisesRegex(ValueError, "supplier requests differ"):
                stopped_supplier_calls(root, probe.name, stopped.name)
            probe.write_bytes(stopped.read_bytes())
            self.assertEqual(stopped_supplier_calls(root, probe.name, stopped.name), [self.report])

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
                                    capture_output=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(b"only applies to archived baselines", result.stderr)
            self.assertFalse((root / "out.json").exists())

    def test_matching_incorrect_pixels_do_not_pass(self):
        for stage in ("source", "restored"):
            self.browser[stage]["pageImage"]["rgbaSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "image"):
            self.check()

    @unittest.skipUnless(os.name == "posix", "restore.sh runs on the Linux Docker host")
    def test_stop_captures_late_supplier_calls_before_removing_containers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = {"images": {k: {"id": k} for k in ("web", "backend")},
                        "containers": {"backend": {"persistentSqliteVolume": "original"}}}
            (root / "baseline.json").write_text(json.dumps(baseline))
            docker = root / "docker"
            docker.write_text("""#!/bin/sh
case "$1" in
  image) echo "$5";;
  run|inspect) echo fixture;;
  compose) case " $* " in
    *" ps "*) [ -f "$STATE/removed" ] || echo container;;
    *" stop "*) touch "$STATE/stopped";;
    *" down "*) touch "$STATE/removed";;
  esac;;
  logs) [ -f "$STATE/stopped" ] && [ ! -f "$STATE/removed" ] && cat "$STATE/live";;
esac
""")
            docker.chmod(0o700)
            for action, side, probe in (("stop-b-source", "source", "probe-b-source"),
                                        ("down-b", "restored", "probe-b")):
                with self.subTest(side=side):
                    state = root / side
                    state.mkdir()
                    env = dict(os.environ, PATH=temp + os.pathsep + os.environ["PATH"], G15_RESTORE_RUN="run",
                               G15_TEST_TLS=temp, G15_BASELINE=str(root / "baseline.json"),
                               G15_EVIDENCE=str(state), STATE=str(state))

                    def stop():
                        return subprocess.run(["sh", HERE / "restore.sh", action], env=env, capture_output=True)

                    probe_name = probe + "-supplier.txt"
                    stopped_name = f"b-{side}-stopped-supplier.txt"
                    earlier = [self.report] if side == "source" else []
                    probe_lines = "".join(json.dumps(call) + "\n" for call in earlier)
                    (state / probe_name).write_text(probe_lines)
                    self.assertNotEqual(stop().returncode, 0)  # Log read fails while the container still exists.
                    self.assertFalse((state / "removed").exists())
                    self.assertFalse((state / stopped_name).exists())
                    late = {"call": "report", "status": 422}
                    (state / "live").write_text(probe_lines + json.dumps(late) + "\n")
                    result = stop()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue((state / "removed").exists())
                    self.assertEqual(stop().returncode, 0)
                    calls = [json.loads(line) for line in (state / stopped_name).read_bytes().splitlines()]
                    self.assertEqual(calls, earlier + [late])
                    with self.assertRaisesRegex(ValueError, "supplier requests differ"):
                        stopped_supplier_calls(state, probe_name, stopped_name)
                    (state / stopped_name).write_text("")
                    (state / probe_name).write_text(json.dumps(late))
                    with self.assertRaisesRegex(ValueError, "supplier requests differ"):
                        stopped_supplier_calls(state, probe_name, stopped_name)

    @unittest.skipUnless(os.name == "posix", "restore.sh runs on the Linux Docker host")
    def test_probe_preserves_both_files_on_either_capture_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = {"images": {k: {"id": k} for k in ("web", "backend")},
                        "containers": {"backend": {"persistentSqliteVolume": "original"}}}
            (root / "baseline.json").write_text(json.dumps(baseline))
            docker = root / "docker"
            docker.write_text('#!/bin/sh\ncase "$1" in\n'
                              'image) echo "$5";;\nrun) echo /certifi;;\n'
                              'compose) case " $* " in *" exec "*) step=exec;; *" logs "*) step=logs;; esac\n'
                              'echo "$step"; test "$step" != "$FAIL_AT";;\nesac\n')
            docker.chmod(0o700)
            env = dict(os.environ, PATH=temp + os.pathsep + os.environ["PATH"], G15_RESTORE_RUN="run",
                       G15_EVIDENCE=temp, G15_TEST_TLS=temp, G15_BASELINE=str(root / "baseline.json"))
            for action in ("probe-a", "probe-b", "probe-b-source"):
                db, logs = root / (action + ".json"), root / (action + "-supplier.txt")
                db.write_text("previous db")
                logs.write_text("previous logs")
                for failure in ("exec", "logs", ""):
                    with self.subTest(action=action, failure=failure):
                        run = subprocess.run(["sh", HERE / "restore.sh", action], env=dict(env, FAIL_AT=failure),
                                             text=True, capture_output=True)
                        self.assertEqual(run.returncode == 0, not failure, run.stderr)
                        self.assertEqual(db.read_text(), "previous db" if failure else "exec\n")
                        self.assertEqual(logs.read_text(), "previous logs" if failure else "logs\n")

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
                ' "volume create") printf "%s" "${4#g15.creation=}" > "$G15_EVIDENCE/mock-label";;\n'
                ' "volume inspect") if [ "$3" = --format ]; then cat "$G15_EVIDENCE/mock-label"; else test "$3" = fresh-first-round; fi;;\n'
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
            # Docker create is idempotent and may return a volume another run already owns.
            docker.write_text(docker.read_text().replace('cat "$G15_EVIDENCE/mock-label"', 'echo already-existed'))
            calls.write_text("")
            run = subprocess.run(["sh", HERE / "restore.sh", "backup-a"], env=dict(env, TEST_PS_STATUS="0"),
                                 capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("Expected a new volume", run.stderr)
            self.assertNotIn("target=/dst", calls.read_text())


if __name__ == "__main__":
    unittest.main()
