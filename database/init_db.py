"""Initialize and check the development database."""
import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / "schema.sql"
SEED_TABLES = ("storybooks", "story_versions", "story_pages", "knowledge_items")


def database_path() -> Path:
    return Path(os.environ.get("ZHIYA_DB_PATH", ROOT / "instance" / "zhiya.sqlite3")).resolve()


def connect(path: Path, *, create: bool = False) -> sqlite3.Connection:
    mode = "rwc" if create else "rw"
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path, create=True)) as db, db:
        db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA.read_text(encoding="utf-8"))
        check_foreign_keys(db)


def check_foreign_keys(db: sqlite3.Connection) -> None:
    problems = db.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise sqlite3.IntegrityError(f"Foreign key violations: {problems}")


def check(path: Path) -> None:
    with closing(connect(path)) as db:
        check_foreign_keys(db)
        tables = db.execute("PRAGMA table_list").fetchall()
        print("business tables:", sum(
            schema == "main" and kind == "table" and not name.startswith("sqlite_")
            for schema, name, kind, *_ in tables
        ))
        print("foreign_key_check: ok")
        for table in SEED_TABLES:
            count = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"{table}: {count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check an existing database.")
    options = parser.parse_args()
    path = database_path()
    if not options.check:
        initialize(path)
    check(path)
