import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from import_seed import SEED_DIR, SEED_PUBLISHER, import_seed
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
            self.assertEqual(db.execute("SELECT id, reviewed_by_id, reviewed_at FROM story_versions "
                                        "WHERE status='DRAFT'").fetchall(), [(1, None, None)])
            self.assertEqual(db.execute(
                "SELECT v.id FROM story_versions v JOIN accounts a ON a.id=v.reviewed_by_id "
                "WHERE v.status='PUBLISHED' AND v.reviewed_at > 0 AND a.account=? AND a.role='ADMIN' "
                "AND a.password_hash='!' ORDER BY v.id", (SEED_PUBLISHER,)
            ).fetchall(), [(i,) for i in range(2, 22)])
            self.assertEqual(
                db.execute("SELECT DISTINCT source_visited_pages_json FROM story_versions").fetchall(),
                [(None,)],
            )
            self.assertEqual(db.execute("SELECT DISTINCT status FROM knowledge_items").fetchall(), [("DRAFT",)])
            original_pages = db.execute(
                "SELECT v.storybook_id, v.title, p.page_no, p.image_ref "
                "FROM story_pages p JOIN story_versions v ON v.id = p.version_id "
                "WHERE v.storybook_id BETWEEN 2 AND 21"
            ).fetchall()
            self.assertEqual(len(original_pages), 104)
            for book_id, title, page_no, image_ref in original_pages:
                with self.subTest(book_id=book_id, page_no=page_no):
                    stem = f"PIC-B{book_id - 1:02}_{title}"
                    expected = f"knowledge-base_v2.5/图片/内页/{stem}/{stem}-P{page_no}.png"
                    self.assertEqual(image_ref, expected)
                    self.assertTrue((SEED_DIR.parents[1] / expected).is_file())
            self.assertEqual(
                db.execute("SELECT image_ref FROM story_pages WHERE version_id = 1 ORDER BY page_no").fetchall(),
                [
                    ("小兔子站在屋子中间，看着妈妈。",),
                    ("兔妈妈弯腰看着小兔子，微笑着。",),
                    ("小兔子想象自己变成了一条红色的鱼在水里游。",),
                    ("兔妈妈拿着钓鱼竿，鱼钩上挂着一根胡萝卜。",),
                    ("小兔子想象自己变成了一块高高的石头。",),
                    ("兔妈妈背着背包，拿着登山杖在爬山。",),
                ],
            )
            db.execute("UPDATE story_versions SET status='WITHDRAWN' WHERE id=2")
            db.execute("UPDATE knowledge_items SET status = 'APPROVED', reviewed_at = 1 WHERE id = 1")
        initialize(self.path)
        self.assertFalse(import_seed(self.path))
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT status FROM story_versions WHERE id=2").fetchone(), ("WITHDRAWN",))
            self.assertEqual(
                db.execute("SELECT status, reviewed_at FROM knowledge_items WHERE id = 1").fetchone(),
                ("APPROVED", 1),
            )

    def test_reimport_backfills_only_seed_library_draft_placeholders(self):
        placeholder = "/assets/placeholder.png"
        seed_dir = self.path.parent / "old_seed"
        shutil.copytree(SEED_DIR, seed_dir)
        versions = seed_dir / "story_versions.json"
        old_versions = json.loads(versions.read_text(encoding="utf-8"))
        for version in old_versions:
            version["status"] = "DRAFT"
        versions.write_text(json.dumps(old_versions, ensure_ascii=False), encoding="utf-8")
        pages = seed_dir / "story_pages.json"
        records = json.loads(pages.read_text(encoding="utf-8"))
        seed_images = {}
        for row in records:
            if row["version_id"] != 1:
                seed_images[row["version_id"], row["page_no"]] = row["image_ref"]
                row["image_ref"] = placeholder
        pages.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        books = seed_dir / "storybooks.json"
        book_records = json.loads(books.read_text(encoding="utf-8"))
        original_book = next(book for book in book_records if book["id"] == 5)
        original_book.update(source_type="ORIGINAL", owner_guardian_id=2)
        books.write_text(json.dumps(book_records), encoding="utf-8")
        with closing(connect(self.path)) as db, db:
            db.execute("INSERT INTO accounts VALUES (2, 'guardian', 'digest', 'GUARDIAN', 0)")
        self.assertTrue(import_seed(self.path, seed_dir))

        page_query = (
            "SELECT version_id, page_no, image_ref, body_text, image_license_note "
            "FROM story_pages ORDER BY version_id, page_no"
        )
        with closing(connect(self.path)) as db, db:
            db.execute("UPDATE story_pages SET image_ref = ? WHERE version_id = 1 AND page_no = 1", (placeholder,))
            db.execute("UPDATE story_pages SET image_ref = '/assets/manual.png' WHERE version_id = 2 AND page_no = 2")
            db.execute("UPDATE story_pages SET body_text = 'Edited text' WHERE version_id = 2 AND page_no = 3")
            db.execute("UPDATE story_pages SET image_license_note = NULL WHERE version_id = 2 AND page_no = 4")
            db.execute("INSERT INTO accounts VALUES (1, 'admin', 'digest', 'ADMIN', 0)")
            db.executemany(
                "UPDATE story_versions SET status = ?, reviewed_by_id = 1, reviewed_at = 1 WHERE id = ?",
                [("APPROVED_PRIVATE", 3), ("PUBLISHED", 4)],
            )
            db.execute("UPDATE story_versions SET source_ref = 'Edited source' WHERE id = 6")
            db.execute(
                "INSERT INTO story_pages (version_id, page_no, body_text, image_ref) VALUES (2, 99, 'extra', ?)",
                (placeholder,),
            )
            before = db.execute(page_query).fetchall()

        expected = []
        for version_id, page_no, image_ref, body, license_note in before:
            protected = version_id in (3, 4, 5, 6) or (version_id, page_no) in {(2, 2), (2, 3), (2, 4)}
            if not protected:
                image_ref = seed_images.get((version_id, page_no), image_ref)
            expected.append((version_id, page_no, image_ref, body, license_note))
        for _ in range(2):
            self.assertFalse(import_seed(self.path))
            with closing(connect(self.path)) as db:
                self.assertEqual(db.execute(page_query).fetchall(), expected)

    def test_missing_release_media_rolls_back_and_can_retry(self):
        from unittest.mock import patch
        from app.services.story_media import MediaDependencyError

        with patch.dict("os.environ", {"ZHIYA_MEDIA_ROOT": str(self.path.parent)}):
            with self.assertRaises(MediaDependencyError):
                import_seed(self.path)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM story_versions").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT count(*) FROM accounts").fetchone(), (0,))
        self.assertTrue(import_seed(self.path))

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
