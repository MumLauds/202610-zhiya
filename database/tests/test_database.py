import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from import_seed import SEED_DIR, import_seed
from init_db import SEED_TABLES, connect, initialize


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "test.sqlite3"
        initialize(self.path)

    def test_import_preserves_drafts_links_and_review_edits(self):
        self.assertTrue(import_seed(self.path))
        with closing(connect(self.path)) as db, db:
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone(), (1,))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            counts = [db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in SEED_TABLES]
            self.assertEqual(counts, [21, 21, 110, 20])
            self.assertEqual(db.execute("SELECT DISTINCT status FROM story_versions").fetchall(), [("DRAFT",)])
            self.assertEqual(
                db.execute("SELECT DISTINCT source_visited_pages_json FROM story_versions").fetchall(),
                [(None,)],
            )
            self.assertEqual(db.execute("SELECT DISTINCT status FROM knowledge_items").fetchall(), [("DRAFT",)])
            self.assertEqual(
                db.execute("SELECT count(*) FROM story_pages WHERE image_ref = ?", ("/assets/placeholder.png",))
                .fetchone(),
                (104,),
            )
            db.execute("UPDATE knowledge_items SET status = 'APPROVED', reviewed_at = 1 WHERE id = 1")
        initialize(self.path)
        self.assertFalse(import_seed(self.path))
        with closing(connect(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT status, reviewed_at FROM knowledge_items WHERE id = 1").fetchone(),
                ("APPROVED", 1),
            )

    def test_invalid_page_reference_rolls_back_import(self):
        seed_dir = self.path.parent / "seed"
        shutil.copytree(SEED_DIR, seed_dir)
        pages = seed_dir / "story_pages.json"
        data = json.loads(pages.read_text(encoding="utf-8"))
        data[0]["default_next_page_number"] = 9999
        pages.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(sqlite3.IntegrityError):
            import_seed(self.path, seed_dir)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM storybooks").fetchone(), (0,))

    def test_partial_database_is_left_alone(self):
        with closing(connect(self.path)) as db, db:
            db.execute("INSERT INTO storybooks VALUES (1, NULL, 'LIBRARY', 0)")
        with self.assertRaises(sqlite3.IntegrityError):
            import_seed(self.path)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM storybooks").fetchone(), (1,))
            self.assertEqual(db.execute("SELECT count(*) FROM story_versions").fetchone(), (0,))

    def test_schema_constraints(self):
        with closing(connect(self.path)) as db, db:
            business_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
            self.assertEqual(len(business_tables), 15)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO accounts VALUES (9007199254740992, 'a', 'digest', 'ADMIN', 0)")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO storybooks VALUES (1, NULL, 'ORIGINAL', 0)")
        import_seed(self.path)
        with closing(connect(self.path)) as db, db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE story_versions SET topics_json = '{}'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE story_versions SET status = 'PUBLISHED'")
            db.execute("UPDATE knowledge_items SET status = 'APPROVED', reviewed_at = 1 WHERE id = 1")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO knowledge_items "
                    "(item_key, version_no, title, source_title, source_ref, locator, age_band, "
                    "topics_json, task_types_json, content, status, reviewed_at, created_at) "
                    "SELECT item_key, 2, title, source_title, source_ref, locator, age_band, "
                    "topics_json, task_types_json, content, status, reviewed_at, created_at "
                    "FROM knowledge_items WHERE id = 1"
                )
