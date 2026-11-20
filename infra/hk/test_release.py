import io
import json
import os
import runpy
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent


class ReleaseTests(unittest.TestCase):
    def test_gate_requires_successful_job_on_trusted_main_attempt(self):
        sha = "a" * 40
        run = dict(path=".github/workflows/deploy-hk.yml", event="push",
                   head_sha=sha, head_branch="main", id=123, run_attempt=2,
                   head_repository={"full_name": "MumLauds/202610-zhiya"})
        for change, conclusion, expected in [
            ({}, "success", "123-2"),
            ({"event": "workflow_dispatch"}, "success", "123-2"),
            ({}, "failure", ""), ({}, None, ""),
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
                with patch.dict(os.environ, ZHIYA_DB_PATH=str(live)), redirect_stdout(io.StringIO()) as out:
                    previous_mask = os.umask(0o077)
                    try:
                        runpy.run_path(str(SCRIPTS / "backup.py"))
                    finally:
                        os.umask(previous_mask)
                with closing(sqlite3.connect(out.getvalue().strip())) as saved:
                    self.assertEqual(saved.execute("select id from samples").fetchall(), [(42,)])


if __name__ == "__main__":
    unittest.main()
