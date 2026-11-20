import io
import json
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from backup import create_backup, verify_history
from receipt import check_receipt

SCRIPTS = Path(__file__).resolve().parent


class ReleaseTests(unittest.TestCase):
    def test_gate_requires_successful_job_on_trusted_main_attempt(self):
        sha = "a" * 40
        run = dict(path=".github/workflows/deploy-hk.yml", event="push",
                   head_sha=sha, head_branch="main", id=123, run_attempt=2, status="in_progress", conclusion=None,
                   head_repository={"full_name": "MumLauds/202610-zhiya"})
        for change, conclusion, expected in [
            ({}, "success", "123-2"),
            ({"event": "workflow_dispatch"}, "success", "123-2"),
            ({}, "failure", ""), ({}, None, ""),
            ({"status": "completed", "conclusion": "cancelled"}, "success", ""),
            ({"status": "completed", "conclusion": "timed_out"}, "success", ""),
            ({"status": "queued"}, "success", ""),
            ({"head_sha": "b" * 40}, "success", ""),
            ({"event": "pull_request"}, "success", ""),
            ({"head_repository": {"full_name": "fork/test"}}, "success", ""),
        ]:
            def respond(request, timeout):
                if "/runs?" in request.full_url:
                    data = {"workflow_runs": [run | change]}
                else:
                    self.assertTrue(request.full_url.endswith("/runs/123/attempts/2/jobs"))
                    data = {"jobs": [{"name": "Validate release", "conclusion": conclusion}]}
                return io.BytesIO(json.dumps(data).encode())

            with self.subTest(change=change, conclusion=conclusion), patch.object(sys, "argv", ["gate.py", sha]), \
                    patch("urllib.request.urlopen", side_effect=respond), redirect_stdout(io.StringIO()) as out:
                runpy.run_path(str(SCRIPTS / "gate.py"))
                self.assertEqual(out.getvalue().strip(), expected)

    def test_online_backup_includes_committed_wal_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.sqlite3"
            with closing(sqlite3.connect(live)) as conn:
                conn.execute("pragma journal_mode=WAL")
                conn.execute("create table samples(id integer primary key)")
                conn.execute("insert into samples values (42)")
                conn.commit()
                with closing(sqlite3.connect(create_backup(live))) as saved:
                    self.assertEqual(saved.execute("select id from samples").fetchall(), [(42,)])

    def test_failed_backup_removes_only_its_own_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.db"
            with closing(sqlite3.connect(live)) as db:
                db.executescript("create table p(id primary key); create table c(pid references p(id));")
                good = create_backup(live)
                db.executescript("insert into c values(1);")
                for _ in range(2):
                    with self.assertRaisesRegex(RuntimeError, "Invalid backup"):
                        create_backup(live)
                self.assertEqual(list(live.parent.glob(".before-release-*")), [good.parent])
                self.assertTrue(good.is_file())

    def test_verify_history_allows_new_rows_and_checks_every_original_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.db"
            with closing(sqlite3.connect(live)) as db:
                db.executescript("""
                    create table accounts(id, created_at);
                    create table child_profiles(id, guardian_id, created_at, nickname);
                    create table reading_sessions(id, profile_id, version_id, created_at);
                    create table interaction_events(id, session_id, version_id, page_no, event_type, created_at,
                                                    input_text, output_text, feedback, response_json);
                    create table reading_reports(session_id, status, summary, activities_json);
                    insert into accounts values (1,10);
                    insert into child_profiles values (2,1,20,'first');
                    insert into reading_sessions values (3,2,4,30);
                    insert into interaction_events values (5,3,4,1,'ASK',31,'question','answer',NULL,'{}');
                    insert into reading_reports values (3,'READY','original','[]'), (6,'PENDING',NULL,NULL);
                """)
                saved = create_backup(live)
                db.executescript("""
                    insert into accounts values (9,99);
                    update child_profiles set nickname='second';
                    update reading_reports set status='READY', summary='new' where session_id=6;
                """)
                verify_history(live, saved)
                for table in ("accounts", "child_profiles", "reading_sessions", "interaction_events", "reading_reports"):
                    with self.subTest(table=table):
                        db.execute(f"delete from {table}")
                        db.commit()
                        with self.assertRaisesRegex(RuntimeError, table):
                            verify_history(live, saved)
                        with closing(sqlite3.connect(saved)) as restore:
                            restore.backup(db)
                for change, expected in [
                    ("update interaction_events set output_text='changed'", "interaction_events"),
                    ("update interaction_events set session_id=7", "interaction_events"),
                    ("update reading_reports set status='FAILED',summary=NULL where session_id=3", "Completed"),
                    ("update reading_reports set summary='changed' where session_id=3", "Completed"),
                    ("update reading_reports set activities_json=NULL where session_id=3", "Completed"),
                ]:
                    with self.subTest(change=change):
                        db.execute(change)
                        db.commit()
                        with self.assertRaisesRegex(RuntimeError, expected):
                            verify_history(live, saved)
                        with closing(sqlite3.connect(saved)) as restore:
                            restore.backup(db)

    def test_receipt_is_withdrawn_on_drift(self):
        sha = "a" * 40
        api, web = ("sha256:" + digit * 64 for digit in "12")
        for frontend_state, keep in [(f"true {web} {sha}", True), (f"true {api} {sha}", False),
                                     (f"false {web} {sha}", False), (f"true {web} {'b' * 40}", False)]:
            with self.subTest(frontend_state=frontend_state), tempfile.TemporaryDirectory() as tmp:
                receipt = Path(tmp) / "current.json"
                private = Path(tmp) / "last-success.json"
                private.write_text(json.dumps(dict(revision=sha, backend=api, web=web)))
                receipt.write_bytes(private.read_bytes())
                with patch("receipt.subprocess.check_output", side_effect=[f"true {api} {sha}", frontend_state]):
                    check_receipt(receipt, "test", private)
                self.assertEqual(receipt.exists(), keep)

    def test_inspect_outage_then_recovery_republishes_only_matching_receipt(self):
        sha, api, web = "a" * 40, "sha256:" + "1" * 64, "sha256:" + "2" * 64
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "current.json"
            private = Path(tmp) / "last-success.json"
            private.write_text(json.dumps(dict(revision=sha, backend=api, web=web, run="12-3")))
            public.write_bytes(private.read_bytes())
            for states, visible in [
                ([subprocess.CalledProcessError(1, "docker inspect")], False),
                ([f"true {api} {sha}", f"true {api} {sha}"], False),
                ([f"true {api} {sha}", f"true {web} {sha}"], True),
            ]:
                with patch("receipt.subprocess.check_output", side_effect=states):
                    check_receipt(public, "test", private)
                self.assertEqual(public.exists(), visible)
            self.assertEqual(public.read_bytes(), private.read_bytes())


if __name__ == "__main__":
    unittest.main()
