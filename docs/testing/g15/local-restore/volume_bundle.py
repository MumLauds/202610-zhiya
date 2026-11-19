"""Offline backup and restoration of a stopped G15 test volume, including version-pinned media."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from backup_db import snapshot as sqlite_backup

sys.path.insert(0, "/test")
from fixture import snapshot as reading_snapshot

DB = "zhiya.sqlite3"
PIN_DIRS = ("." + DB + ".media_pins", "." + DB + ".media_pins.bindings")
SRC = Path("/src")
DST = Path("/dst")
MANIFEST = "manifest.json"


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def hashes(root, all_files=False):
    entries = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not all_files and path.name != DB and relative.parts[0] not in PIN_DIRS:
            continue
        if path.is_symlink():
            raise ValueError(f"Unexpected symbolic link: {relative}")
        if path.is_file():
            entries[relative.as_posix()] = file_hash(path)
    return entries


def reading(root):
    with closing(sqlite3.connect((root / DB).as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Database integrity or foreign-key check failed")
        row = reading_snapshot(db)
        counts = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in ("reading_sessions", "interaction_events", "request_executions", "reading_reports")}
    return {"session": row, "counts": counts, "integrityCheck": "ok", "foreignKeyViolations": 0}


def gather(root):
    return {"files": hashes(root, all_files=True), "reading": reading(root)}


def clean_destination():
    if any(DST.iterdir()):
        raise ValueError("Backup or restore destination must be empty")


def copy_pins():
    for name in PIN_DIRS:
        if (SRC / name).exists():
            shutil.copytree(SRC / name, DST / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("probe", "backup", "restore"))
    operation = parser.parse_args().operation
    if not os.statvfs(SRC).f_flag & os.ST_RDONLY:
        raise ValueError("Source must be mounted read-only")
    if operation == "probe":
        return gather(SRC)
    clean_destination()
    if operation == "backup":
        source = gather(SRC)
        sqlite_backup(SRC / DB, DST / DB)
        copy_pins()
        saved = hashes(DST)
        expected_pins = {name: digest for name, digest in source["files"].items()
                         if Path(name).parts[0] in PIN_DIRS}
        if {k: v for k, v in saved.items() if k != DB} != expected_pins:
            raise ValueError("Pinned media copy differs from the source")
        manifest = {"files": saved, "directories": [name for name in PIN_DIRS if (SRC / name).is_dir()]}
        manifest_path = DST / MANIFEST
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if source != gather(SRC) or source["reading"] != reading(DST):
            raise ValueError("Source changed or backup reading differs")
        return {"source": source, "backup": manifest, "manifestSha256": file_hash(manifest_path)}
    manifest_path = SRC / MANIFEST
    manifest = json.loads(manifest_path.read_bytes())
    if manifest["files"] != hashes(SRC):
        raise ValueError("Backup checksum mismatch")
    shutil.copyfile(SRC / DB, DST / DB)
    copy_pins()
    for path in (DST, *DST.rglob("*")):
        os.chown(path, 10001, 10001)
    restored = hashes(DST)
    if restored != manifest["files"] or manifest["directories"] != [n for n in PIN_DIRS if (DST / n).is_dir()]:
        raise ValueError("Restored file differs from backup")
    return {"files": restored, "reading": reading(DST), "manifestSha256": file_hash(manifest_path)}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
