"""Create a private online SQLite backup and verify its integrity before cutover."""
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

os.umask(0o077)
live = Path(os.environ["ZHIYA_DB_PATH"]).resolve()
saved = Path(tempfile.mkdtemp(prefix=".before-release-", dir=live.parent)) / "before.sqlite3"
with closing(sqlite3.connect(live.as_uri()+"?mode=ro", uri=True)) as src, closing(sqlite3.connect(saved)) as dst:
    src.backup(dst)
    if dst.execute("pragma integrity_check").fetchone() != ("ok",) or dst.execute("pragma foreign_key_check").fetchone():
        raise RuntimeError("Invalid backup")
print(saved)
