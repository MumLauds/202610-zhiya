"""Back up live SQLite and check stable business identities after cutover."""
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

def read_only(path):
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


def create_backup(live):
    directory = Path(tempfile.mkdtemp(prefix=".before-release-", dir=live.parent))
    saved = directory / "before.sqlite3"
    try:
        with closing(read_only(live)) as src, closing(sqlite3.connect(saved)) as dst:
            src.backup(dst)
            if dst.execute("pragma integrity_check").fetchone() != ("ok",) or dst.execute("pragma foreign_key_check").fetchone():
                raise RuntimeError("Invalid backup")
    except BaseException:
        shutil.rmtree(directory)
        raise
    return saved


def verify_history(live, saved):
    # Mutable fields such as nicknames, session status and pending report
    # content can legitimately change while the backup and deploy run.
    keys = {
        "accounts": "id, created_at",
        "child_profiles": "id, guardian_id, created_at",
        "reading_sessions": "id, profile_id, version_id, created_at",
        "reading_reports": "session_id",
    }
    with closing(read_only(live)) as db:
        db.execute("attach database ? as prior", (saved.as_uri() + "?mode=ro",))
        db.execute("begin")
        for table, columns in keys.items():
            missing = db.execute(
                f"select {columns} from prior.{table} except select {columns} from main.{table} limit 1"
            ).fetchone()
            if missing is not None:
                raise RuntimeError(f"Pre-release record missing or reassigned in {table}")
        if db.execute("pragma main.foreign_key_check").fetchone():
            raise RuntimeError("Foreign key check failed after cutover")


if __name__ == "__main__":
    os.umask(0o077)
    live = Path(os.environ["ZHIYA_DB_PATH"]).resolve()
    if sys.argv[1:] == ["create"]:
        print(create_backup(live))
    elif len(sys.argv) == 3 and sys.argv[1] == "verify":
        verify_history(live, Path(sys.argv[2]).resolve())
    else:
        sys.exit("expected create or verify <backup>")
