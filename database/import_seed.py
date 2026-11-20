"""Import course seeds or backfill media snapshots for existing versions."""
import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from time import time_ns

from init_db import ROOT, SEED_TABLES, check_foreign_keys, clear_pin_store, connect, database_path

BACKEND_ROOT = ROOT.parent / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.story_media import SEED_COVER_SOURCE_REF, pin_database_versions  # noqa: E402

SEED_DIR = ROOT.parent / "data" / "seed"
SEED_PUBLISHER = "seed:course-originals"
IDENTITIES = {
    "storybooks": ("id",),
    "story_versions": ("id", "storybook_id", "version_no"),
    "story_pages": ("version_id", "page_no"),
    "knowledge_items": ("item_key", "version_no"),
}
FIELDS = {
    "storybooks": "id owner_guardian_id source_type created_at",
    "story_versions": (
        "id storybook_id version_no title topics_json age_min age_max source_ref "
        "license_note status created_at branching"
    ),
    "story_pages": "version_id page_no body_text image_ref audio_ref image_license_note default_next_page_number",
    "knowledge_items": (
        "item_key version_no title source_title source_ref locator age_band topics_json "
        "task_types_json content status reviewed_at created_at"
    ),
}


def load_seed(seed_dir: Path) -> dict:
    now = time_ns() // 1_000_000
    data = {}
    for table, fields in FIELDS.items():
        columns = set(fields.split())
        records = json.loads((seed_dir / f"{table}.json").read_text(encoding="utf-8"))
        for row in records:
            if table == "knowledge_items" and row["status"] != "DRAFT":
                raise ValueError("Knowledge seed records must be DRAFT")
            if table == "story_versions" and row["status"] != "DRAFT":
                if not (row["status"] == "PUBLISHED" and row["id"] == row["storybook_id"]
                        and 2 <= row["id"] <= 21 and row["version_no"] == 1
                        and row["source_ref"] == SEED_COVER_SOURCE_REF
                        and row["license_note"] == "团队原创，仅限课程学习与演示，禁止商用"):
                    raise ValueError("Only the reviewed course originals may be published by seed import")
            if table == "knowledge_items" and row["reviewed_at"] is not None:
                raise ValueError("Knowledge seed records must be unreviewed")
            if "created_at" in columns:
                row.setdefault("created_at", now)
            if table == "story_versions":
                row.setdefault("branching", 0)
            if row.keys() != columns:
                raise ValueError(f"{table}: seed fields do not match the import mapping")
            for column, value in row.items():
                if column.endswith("_json"):
                    row[column] = json.dumps(value, ensure_ascii=False)
        data[table] = records
    return data


def import_seed(path: Path, seed_dir: Path = SEED_DIR) -> bool:
    data = load_seed(seed_dir)
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for table in SEED_TABLES:
            keys = IDENTITIES[table]
            expected = {tuple(row[key] for key in keys) for row in data[table]}
            if len(expected) != len(data[table]):
                raise ValueError(f"{table}: duplicate seed identity")
        populated = any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in SEED_TABLES)
        if populated:
            for table, keys in IDENTITIES.items():
                predicate = " AND ".join(f"{key} = ?" for key in keys)
                for row in data[table]:
                    if not db.execute(
                        f"SELECT 1 FROM {table} WHERE {predicate}",
                        tuple(row[key] for key in keys),
                    ).fetchone():
                        raise sqlite3.IntegrityError(
                            f"{table}: database is populated but some seed identities are missing"
                        )
            db.executemany(
                """UPDATE story_pages SET image_ref = :image_ref
                   WHERE version_id = :version_id AND page_no = :page_no
                     AND image_ref = '/assets/placeholder.png'
                     AND body_text = :body_text AND image_license_note IS :image_license_note
                     AND EXISTS (
                         SELECT 1 FROM story_versions v JOIN storybooks b ON b.id = v.storybook_id
                         WHERE v.id = :version_id AND v.status = 'DRAFT'
                           AND b.source_type = 'LIBRARY' AND v.source_ref = :seed_source
                     )""",
                (dict(page, seed_source=SEED_COVER_SOURCE_REF) for page in data["story_pages"]
                 if page["image_ref"].startswith("knowledge-base_v2.5/图片/内页/")),
            )
            check_foreign_keys(db)
            pin_database_versions(db, db_path=path)
            return False

        released = {row["id"] for row in data["story_versions"] if row["status"] == "PUBLISHED"}
        if released:
            library_ids = {book["id"] for book in data["storybooks"] if book["source_type"] == "LIBRARY"}
            if not released <= library_ids or any(
                page["image_license_note"] != "NO-COMMERCIAL" for page in data["story_pages"]
                if page["version_id"] in released
            ):
                raise ValueError("Published course originals require licensed library pages")
            now = time_ns() // 1_000_000
            reviewer = db.execute(
                "INSERT INTO accounts (account, password_hash, role, created_at) VALUES (?, '!', 'ADMIN', ?)",
                (SEED_PUBLISHER, now),
            ).lastrowid
            for row in data["story_versions"]:
                row["reviewed_by_id"] = reviewer if row["id"] in released else None
                row["reviewed_at"] = now if row["id"] in released else None
        for table, fields in FIELDS.items():
            columns = fields.split()
            if table == "story_versions" and released:
                columns.extend(("reviewed_by_id", "reviewed_at"))
            placeholders = ", ".join(f":{column}" for column in columns)
            db.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                data[table],
            )
        check_foreign_keys(db)
        clear_pin_store(path)
        pin_database_versions(db, db_path=path, require_images_for=released)
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin-existing-media", action="store_true",
                        help="Pin existing versions using the original media, without importing seeds.")
    if parser.parse_args().pin_existing_media:
        path = database_path()
        with closing(connect(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            pin_database_versions(db, db_path=path)
        print("Existing version media snapshots checked.")
    else:
        print("Seed data imported." if import_seed(database_path()) else "Seed identities already present.")
