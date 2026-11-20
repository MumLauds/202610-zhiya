"""准备 P6 验收/演示用的本地数据。

做三件事：
1. 初始化仓库种子，导入课程书架；
2. 审核发布绘本与知识条目，使书架、推荐和 Agent 检索可用；
3. 创建演示家长账号和幼儿档案（已存在则复用）。

只操作本地 SQLite，不调用任何模型或语音供应商。生产库请勿直接运行。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.v1.auth import password_hash  # noqa: E402

sys.path.insert(0, str(ROOT / "database"))
from import_seed import SEED_DIR, SEED_PUBLISHER, import_seed, load_seed  # noqa: E402

SCHEMA = ROOT / "database" / "schema.sql"
DEFAULT_DB = Path(__file__).resolve().parent / "instance" / "zhiya.sqlite3"
DEFAULT_ACCOUNT = "p6demo"
DEFAULT_PASSWORD = "Zhiya-demo-2026"


def connect(path: Path, *, create: bool = False) -> sqlite3.Connection:
    mode = "rwc" if create else "rw"
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def ensure_database(path: Path) -> bool:
    """在脚本认领的新库或空库中导入种子，保留标记直到演示数据事务提交。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path, create=True)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'")}
        if "p6_demo_setup_pending" not in tables:
            if tables and not {"accounts", "storybooks"}.issubset(tables):
                return False
            for table in tables:
                name = table.replace('"', '""')
                if db.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone():
                    return False
            db.execute("CREATE TABLE p6_demo_setup_pending (id INTEGER PRIMARY KEY)")
    with closing(connect(path)) as db, db:
        db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA.read_text(encoding="utf-8"))
    import_seed(path)
    return True


def ensure_account(db: sqlite3.Connection, account: str, password: str, role: str) -> int:
    row = db.execute("SELECT id, role FROM accounts WHERE account = ?", (account,)).fetchone()
    now = int(time.time() * 1000)
    if row:
        if row["role"] != role:
            raise SystemExit(f"账号 {account} 的现有角色为 {row['role']}，准备演示数据需要 {role}。请更换演示账号。")
        return int(row["id"])
    cursor = db.execute(
        "INSERT INTO accounts (account, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        (account, password_hash(password), role, now),
    )
    return int(cursor.lastrowid)


def ensure_branch(db: sqlite3.Connection, version_id: int) -> bool:
    """为验收准备一条已审核分支（第 1 页 -> 第 2 页）。

    种子没有 story_branches 记录；没有分支就无法验证 API-018。这里只在本地
    演示库插入一条明确标注的测试分支，不修改仓库种子文件。
    """
    if db.execute("SELECT 1 FROM story_branches WHERE version_id = ? AND review_status = 'APPROVED'",
                  (version_id,)).fetchone():
        return False
    if not db.execute("SELECT 1 FROM story_pages WHERE version_id = ? AND page_no = 2",
                      (version_id,)).fetchone():
        return False
    return db.execute(
        """INSERT INTO story_branches (version_id, from_page_no, next_page_no, choice_label, review_status)
           VALUES (?, 1, 2, 'P6测试分支', 'APPROVED')
           ON CONFLICT(version_id, from_page_no, choice_label) DO NOTHING""",
        (version_id,),
    ).rowcount == 1


def matches_seed(db: sqlite3.Connection, table: str, record: dict) -> bool:
    # 导入时间由数据库填写，修改过的正文须重新审核。
    fields = {key: value for key, value in record.items() if key != "created_at"}
    where = " AND ".join(f"{key} IS ?" for key in fields)
    return db.execute(f"SELECT 1 FROM {table} WHERE {where}", tuple(fields.values())).fetchone() is not None


def approve_content(db: sqlite3.Connection, reviewer_id: int, limit: int | None) -> dict:
    seed = load_seed(SEED_DIR)
    books = {row["id"]: row for row in seed["storybooks"]}
    published_ids = []
    branches_created = 0
    now = int(time.time() * 1000)
    course_versions = [v for v in seed["story_versions"] if v["status"] == "PUBLISHED"]
    for version in course_versions[:limit]:
        version_id = version["id"]
        book = books[version["storybook_id"]]
        if book["source_type"] != "LIBRARY" or not matches_seed(db, "storybooks", book):
            continue
        already_published = matches_seed(db, "story_versions", version)
        if not already_published and not matches_seed(db, "story_versions", {**version, "status": "DRAFT"}):
            continue
        pages = [row for row in seed["story_pages"] if row["version_id"] == version_id]
        count = db.execute("SELECT COUNT(*) FROM story_pages WHERE version_id=?", (version_id,)).fetchone()[0]
        if count != len(pages) or not all(matches_seed(db, "story_pages", page) for page in pages):
            continue
        if not already_published:
            db.execute("UPDATE story_versions SET status='PUBLISHED', reviewed_by_id=?, reviewed_at=? WHERE id=?",
                       (reviewer_id, now, version_id))
            published_ids.append(version_id)
        if ensure_branch(db, version_id):
            branches_created += 1
    knowledge = 0
    for item in seed["knowledge_items"]:
        if matches_seed(db, "knowledge_items", item):
            knowledge += db.execute(
                "UPDATE knowledge_items SET status='APPROVED', reviewed_at=? WHERE item_key=? AND version_no=? AND status='DRAFT'",
                (now, item["item_key"], item["version_no"]),
            ).rowcount
    return {"storyVersionIds": published_ids, "publishedCount": len(published_ids),
            "demoBranchesCreated": branches_created, "knowledgeApproved": knowledge}


def ensure_profile(db: sqlite3.Connection, guardian_id: int, nickname: str) -> dict:
    row = db.execute(
        "SELECT * FROM child_profiles WHERE guardian_id = ? ORDER BY id LIMIT 1", (guardian_id,)
    ).fetchone()
    if row:
        return {"childProfileId": row["id"], "ageBand": row["age_band"],
                "nickname": row["nickname"], "created": False}
    now = int(time.time() * 1000)
    cursor = db.execute(
        """INSERT INTO child_profiles
           (guardian_id, nickname, age_band, interests_json, preferred_minutes, created_at, updated_at)
           VALUES (?, ?, 'AGE_4_5', ?, 10, ?, ?)""",
        (guardian_id, nickname, json.dumps(["动物", "友谊"], ensure_ascii=False), now, now),
    )
    return {"childProfileId": int(cursor.lastrowid), "ageBand": "AGE_4_5",
            "nickname": nickname, "created": True}


def check(path: Path) -> dict:
    with closing(connect(path)) as db:
        counts = {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("accounts", "child_profiles", "story_versions", "story_pages", "knowledge_items")
        }
        published = db.execute(
            "SELECT count(*) FROM story_versions WHERE status = 'PUBLISHED'"
        ).fetchone()[0]
        approved = db.execute(
            "SELECT count(*) FROM knowledge_items WHERE status = 'APPROVED'"
        ).fetchone()[0]
        return {**counts, "publishedVersions": published, "approvedKnowledge": approved}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 数据库路径")
    parser.add_argument("--account", default=DEFAULT_ACCOUNT, help="演示家长账号")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="演示家长密码")
    parser.add_argument("--nickname", default="小芽", help="演示幼儿昵称")
    parser.add_argument("--limit", type=int, default=12, help="最多准备多少本原创绘本用于演示分支；0 表示全部")
    parser.add_argument("--yes", action="store_true", help="确认修改数据库")
    parser.add_argument("--if-empty", action="store_true", help="一键启动时仅准备新库或未导入数据的空库")
    parser.add_argument("--check-only", action="store_true", help="只查看当前数据库状态")
    args = parser.parse_args()

    if args.limit < 0:
        parser.error("--limit 必须大于等于 0")
    path = args.db.resolve()
    if not path.exists() and not args.yes:
        print(json.dumps({"error": "数据库不存在；首次准备请加 --yes",
                          "path": str(path)}, ensure_ascii=False, indent=2))
        return 2
    if args.check_only:
        print(json.dumps({"path": str(path), **check(path)}, ensure_ascii=False, indent=2))
        return 0
    if not args.yes:
        print(json.dumps({"error": "该命令会审核发布种子内容；确认请加 --yes",
                          "path": str(path)}, ensure_ascii=False, indent=2))
        return 2

    existed = path.exists()
    initialized = ensure_database(path)
    if args.if_empty and not initialized:
        with closing(connect(path)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            if {"accounts", "story_versions"} <= tables and db.execute(
                    "SELECT NOT EXISTS(SELECT 1 FROM accounts WHERE account <> ?) AND EXISTS(SELECT 1 FROM story_versions)",
                    (SEED_PUBLISHER,)).fetchone()[0]:
                print("已有种子库缺少演示账号。确认它用于演示后，请用 --db 指定该库并加 --yes 单独准备。",
                      file=sys.stderr)
                return 2
        print(f"复用已有数据库：{path}")
        return 0
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if args.if_empty and not db.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='p6_demo_setup_pending'").fetchone():
            return 0  # 并发准备已由另一进程完成。
        admin_id = ensure_account(db, f"{args.account}-admin", args.password, "ADMIN")
        guardian_id = ensure_account(db, args.account, args.password, "GUARDIAN")
        approved = approve_content(db, admin_id, args.limit or None)
        profile = ensure_profile(db, guardian_id, args.nickname)
        db.execute("DROP TABLE IF EXISTS p6_demo_setup_pending")

    result = {
        "path": str(path),
        "databaseCreated": not existed,
        "demoAccount": args.account,
        "guardianId": guardian_id,
        "profile": profile,
        **approved,
        "check": check(path),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
