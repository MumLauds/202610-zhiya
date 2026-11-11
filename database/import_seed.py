"""Import the repository's draft seed data in a single transaction."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from time import time_ns

from init_db import ROOT, SEED_TABLES, check_foreign_keys, connect, database_path

SEED_DIR = ROOT.parent / "data" / "seed"
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
            if table in ("story_versions", "knowledge_items") and row["status"] != "DRAFT":
                raise ValueError(f"{table}: seed records must be DRAFT")
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
            check_foreign_keys(db)
            return False

        for table, fields in FIELDS.items():
            columns = fields.split()
            placeholders = ", ".join(f":{column}" for column in columns)
            db.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                data[table],
            )
        check_foreign_keys(db)
        return True


if __name__ == "__main__":
    print("Seed data imported." if import_seed(database_path()) else "Seed identities already present.")
