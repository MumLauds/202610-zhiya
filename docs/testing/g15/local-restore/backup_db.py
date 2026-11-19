"""Copy a consistent SQLite snapshot to a new file; never replace a database."""

import argparse
import hashlib
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path


def snapshot(source: Path, destination: Path, *, timeout: float = 30) -> None:
    source = source.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Destination must be a new file")
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError("Source must be a non-empty SQLite database")
    if timeout <= 0:
        raise ValueError("Timeout must be positive")

    deadline = time.monotonic() + timeout

    def progress(_status, _remaining, _total):
        if time.monotonic() >= deadline:
            raise TimeoutError("SQLite backup deadline exceeded")

    # Same directory permits publishing the checked snapshot without overwriting.
    fd, name = tempfile.mkstemp(prefix=".zhiya-backup-", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as copied:
                original.backup(copied, pages=256, progress=progress, sleep=0.05)
                if copied.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise sqlite3.IntegrityError("Snapshot integrity check failed")
                if copied.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise sqlite3.IntegrityError("Snapshot foreign key check failed")
        # link fails if another process created destination during the backup.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, help="New file in an existing directory")
    options = parser.parse_args()
    try:
        snapshot(options.source, options.destination)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"Snapshot failed: {exc}\n")
    with options.destination.open("rb") as saved:
        digest = hashlib.file_digest(saved, "sha256").hexdigest()
    print(f"Snapshot verified; sha256={digest}")


if __name__ == "__main__":
    main()
