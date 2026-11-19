"""Seed the disposable G15 database or print its completed reading record."""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

TITLE = "纸船停在哪里"


def seed(db):
    if (db.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
            or db.execute("SELECT 1 FROM storybooks LIMIT 1").fetchone()):
        raise ValueError("The G15 fixture requires a newly initialized, empty database")
    db.execute("INSERT INTO accounts VALUES (9000, 'fixture-reviewer', 'no-login', 'ADMIN', 1)")
    db.execute("INSERT INTO storybooks VALUES (11, NULL, 'LIBRARY', 1)")
    db.execute("""INSERT INTO story_versions
        (id,storybook_id,version_no,title,topics_json,age_min,age_max,license_note,status,
         reviewed_by_id,reviewed_at,created_at,branching)
        VALUES (11,11,1,?,'["自然"]',3,6,'本地部署预演合成绘本','PUBLISHED',9000,1,1,0)""", (TITLE,))
    db.execute("""INSERT INTO story_pages (version_id,page_no,body_text,image_ref)
        VALUES (11,1,'小园把一只纸船放进水里。','none')""")


def snapshot(db):
    rows = db.execute("""SELECT s.id AS sessionId, s.profile_id AS profileId, s.version_id AS versionId,
        s.status, s.end_reason AS endReason, s.revision, s.visited_pages_json AS pages,
        s.ended_at AS endedAt, r.status AS reportStatus, r.summary, r.updated_at AS reportUpdatedAt
        FROM reading_sessions s JOIN reading_reports r ON s.id=r.session_id
        WHERE s.version_id=11 AND s.status='FINISHED'""").fetchall()
    if len(rows) != 1:
        raise ValueError("Expected exactly one finished rehearsal session and report")
    row = dict(rows[0])
    row["pages"] = json.loads(row["pages"])
    row["events"] = [dict(e) for e in db.execute("""SELECT id, event_type AS eventType, revision_after AS revision
        FROM interaction_events WHERE session_id=? ORDER BY id""", (row["sessionId"],))]
    if (row["endReason"] != "USER_FINISH" or row["reportStatus"] != "READY" or row["pages"] != [1]
            or row["summary"] != "本次共读阅读了第 1 页。"):
        raise ValueError("Unexpected session or report in the rehearsal database")
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Foreign key check failed")
    return row


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seed", "snapshot"])
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    mode = "rw" if args.action == "seed" else "ro"
    with closing(sqlite3.connect(f"{args.database.resolve().as_uri()}?mode={mode}", uri=True)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        if args.action == "seed":
            seed(db)
        else:
            print(json.dumps(snapshot(db), ensure_ascii=False))
