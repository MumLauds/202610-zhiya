"""Provision an isolated synthetic-only picture book on the staging volume."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

DB = Path("/var/lib/zhiya/staging.sqlite3")
FIXTURE_ID = 901_000
MARKER = "synthetic-yaya-staging"


def seed():
    if os.getenv("YAYA_AGENT2_STAGE") != "staging" or os.getenv("ZHIYA_DB_PATH") != str(DB):
        raise SystemExit("Synthetic seed requires the dedicated staging SQLite path")
    with closing(sqlite3.connect(f"{DB.as_uri()}?mode=rw", uri=True)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT source_ref FROM story_versions WHERE id=?", (FIXTURE_ID,)).fetchone()
        if existing:
            if existing != (MARKER,):
                raise RuntimeError("Staging fixture ID belongs to a different book")
            return
        if db.execute("SELECT 1 FROM storybooks LIMIT 1").fetchone():
            raise RuntimeError("Synthetic seed requires an empty story catalogue")
        db.execute("INSERT INTO accounts VALUES (?, ?, ?, 'ADMIN', 1)", (FIXTURE_ID, MARKER, "!"))
        db.execute("INSERT INTO storybooks VALUES (?, NULL, 'LIBRARY', 1)", (FIXTURE_ID,))
        db.execute("""INSERT INTO story_versions
            (id, storybook_id, version_no, title, topics_json, age_min, age_max, source_ref, license_note,
             status, reviewed_by_id, reviewed_at, created_at, branching)
            VALUES (?, ?, 1, '松鼠的纸风车', '["观察"]', 3, 6, ?, '合成测试素材',
                    'PUBLISHED', ?, 1, 1, 0)""",
                   (FIXTURE_ID, FIXTURE_ID, MARKER, FIXTURE_ID))
        db.executemany("""INSERT INTO story_pages
            (version_id, page_no, body_text, image_ref, default_next_page_number) VALUES (?, ?, ?, '', ?)""", (
                (FIXTURE_ID, 1, '松鼠把信轻轻放进信箱。小兔拿起一片黄色叶子。小熊戴着蓝色帽子。小兔想象黄色叶子是一床小被子。', 2),
                (FIXTURE_ID, 2, '小熊把纸风车插进土里。一阵风吹来，纸风车慢慢转了起来。', 3),
                (FIXTURE_ID, 3, '松鼠后来取出信，带回了小屋。', None),
            ))


if __name__ == "__main__":
    seed()
