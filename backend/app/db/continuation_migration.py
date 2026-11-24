"""Upgrade the original SQLite schema before accepting continuation writes."""

import sqlite3


# This is the event schema at the time of the migration. Subsequent upgrades run separately.
EVENT_TABLE = """CREATE TABLE migrated_events (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    session_id INTEGER NOT NULL,
    version_id INTEGER NOT NULL,
    request_key TEXT CHECK (length(request_key) BETWEEN 1 AND 64),
    request_digest TEXT NOT NULL,
    response_json TEXT NOT NULL CHECK (json_valid(response_json) AND json_type(response_json) = 'object'),
    page_no INTEGER NOT NULL,
    branch_id INTEGER,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'STARTED', 'PAGE_CHANGED', 'QUESTION', 'ANSWER', 'ASK', 'FEEDBACK', 'BRANCH_CHOSEN')),
    feedback TEXT CHECK (feedback IN ('UNDERSTOOD', 'NOT_UNDERSTOOD', 'SKIPPED')),
    input_text TEXT CHECK (length(trim(input_text)) BETWEEN 1 AND 200),
    output_text TEXT,
    strategy_type TEXT,
    result_status TEXT NOT NULL CHECK (result_status IN ('SUCCESS', 'FALLBACK', 'FAILED')),
    error_code TEXT,
    revision_after INTEGER NOT NULL CHECK (revision_after >= 0),
    created_at INTEGER NOT NULL,
    UNIQUE (session_id, request_key),
    CHECK (request_key IS NOT NULL OR event_type = 'STARTED'),
    FOREIGN KEY (session_id, version_id) REFERENCES reading_sessions(id, version_id),
    FOREIGN KEY (version_id, page_no) REFERENCES story_pages(version_id, page_no),
    FOREIGN KEY (branch_id, version_id, page_no) REFERENCES story_branches(id, version_id, from_page_no)
) STRICT"""


def _upgraded(db):
    return db.execute("""SELECT 1 FROM pragma_table_info('request_executions')
                         WHERE name = 'source_reading_session_id'""").fetchone() is not None


def migrate_continuations(db: sqlite3.Connection) -> None:
    if _upgraded(db):
        return
    # SQLite requires foreign keys off outside the transaction when rebuilding a referenced table.
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            if _upgraded(db):
                return
            db.execute("""ALTER TABLE story_versions ADD COLUMN approved_page_media_json TEXT CHECK (
                approved_page_media_json IS NULL OR
                (json_valid(approved_page_media_json) AND json_type(approved_page_media_json) = 'object'))""")
            db.execute("""ALTER TABLE request_executions ADD COLUMN source_reading_session_id INTEGER
                          REFERENCES reading_sessions(id) ON DELETE CASCADE""")
            indexes_and_triggers = db.execute("""SELECT sql FROM sqlite_schema WHERE tbl_name = 'interaction_events'
                AND type IN ('index', 'trigger') AND sql IS NOT NULL""").fetchall()
            db.execute(EVENT_TABLE)
            db.execute("INSERT INTO migrated_events SELECT * FROM interaction_events")
            db.execute("""UPDATE migrated_events SET request_key = NULL WHERE event_type = 'STARTED'
                          AND json_extract(response_json, '$.data.readingSessionId') = session_id""")
            db.execute("DROP TABLE interaction_events")
            db.execute("ALTER TABLE migrated_events RENAME TO interaction_events")
            for (statement,) in indexes_and_triggers:
                db.execute(statement)
            db.execute("""UPDATE request_executions SET source_reading_session_id =
                (SELECT id FROM reading_sessions WHERE id = json_extract(response_body, '$.data.sourceReadingSessionId'))
                WHERE scope_type = 'SESSION_CREATE' AND status = 'SUCCEEDED'""")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise sqlite3.IntegrityError("Continuation migration failed foreign key validation")
    finally:
        db.execute("PRAGMA foreign_keys = ON")
