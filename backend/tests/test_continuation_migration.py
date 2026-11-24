import sqlite3
from contextlib import closing

import pytest

from app.db.continuation_migration import migrate_continuations


@pytest.fixture
def legacy_db():
    with closing(sqlite3.connect(":memory:")) as db:
        # Keep the legacy event column order. Other tables only need their referenced keys.
        db.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE story_versions (id INTEGER PRIMARY KEY);
            CREATE TABLE reading_sessions (
                id INTEGER PRIMARY KEY, version_id INTEGER, UNIQUE (id, version_id)
            );
            CREATE TABLE story_pages (version_id INTEGER, page_no INTEGER, PRIMARY KEY (version_id, page_no));
            CREATE TABLE story_branches (
                id INTEGER, version_id INTEGER, from_page_no INTEGER,
                PRIMARY KEY (id, version_id, from_page_no)
            );
            CREATE TABLE interaction_events (
                id INTEGER PRIMARY KEY, session_id INTEGER, version_id INTEGER, request_key TEXT NOT NULL,
                request_digest TEXT, response_json TEXT, page_no INTEGER, branch_id INTEGER, event_type TEXT,
                feedback TEXT, input_text TEXT, output_text TEXT, strategy_type TEXT, result_status TEXT,
                error_code TEXT, revision_after INTEGER, created_at INTEGER,
                UNIQUE (session_id, request_key)
            );
            CREATE INDEX idx_interaction_events_session_created
                ON interaction_events(session_id, created_at, id);
            CREATE TABLE knowledge_items (id INTEGER PRIMARY KEY);
            CREATE TABLE event_evidence (
                event_id INTEGER REFERENCES interaction_events(id),
                knowledge_item_id INTEGER REFERENCES knowledge_items(id), rank_no INTEGER
            );
            CREATE TABLE request_executions (
                id INTEGER PRIMARY KEY, scope_type TEXT, status TEXT, response_body TEXT
            );

            INSERT INTO story_versions VALUES (10), (20);
            INSERT INTO reading_sessions VALUES (1, 10), (2, 20);
            INSERT INTO story_pages VALUES (10, 1), (20, 3);
            INSERT INTO interaction_events
                (id, session_id, version_id, request_key, request_digest, response_json,
                 page_no, event_type, result_status, revision_after, created_at)
            VALUES
                (11, 1, 10, 'started-by-client', 'digest1', '{"data":{"interactionEventId":11,"revision":1}}',
                 1, 'STARTED', 'SUCCESS', 1, 101),
                (12, 2, 20, 'switch-request', 'digest2', '{"data":{"readingSessionId":2,"sourceReadingSessionId":1}}',
                 3, 'STARTED', 'SUCCESS', 1, 102),
                (13, 2, 20, 'question-request', 'digest3', '{}', 3, 'QUESTION', 'SUCCESS', 2, 103);
            INSERT INTO knowledge_items VALUES (21);
            INSERT INTO event_evidence VALUES (13, 21, 1);
            INSERT INTO request_executions VALUES
                (31, 'SESSION_CREATE', 'SUCCEEDED', '{"data":{"readingSessionId":2,"sourceReadingSessionId":1}}'),
                (32, 'SESSION_CREATE', 'SUCCEEDED', '{"data":{"readingSessionId":1}}');
        """)
        yield db


def test_migrate_legacy_continuation_preserves_history(legacy_db):
    db = legacy_db
    events = db.execute("SELECT * FROM interaction_events ORDER BY id").fetchall()
    switched = events[1]
    events[1] = (*switched[:3], None, *switched[4:])
    requests = db.execute("SELECT * FROM request_executions ORDER BY id").fetchall()

    for _ in range(2):
        migrate_continuations(db)

        assert db.execute("SELECT * FROM interaction_events ORDER BY id").fetchall() == events
        assert db.execute("SELECT * FROM event_evidence").fetchall() == [(13, 21, 1)]
        assert db.execute("SELECT * FROM reading_sessions ORDER BY id").fetchall() == [(1, 10), (2, 20)]
        assert db.execute("SELECT * FROM request_executions ORDER BY id").fetchall() == [
            (*requests[0], 1), (*requests[1], None),
        ]
        assert db.execute("SELECT approved_page_media_json FROM story_versions").fetchall() == [(None,), (None,)]
        assert db.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("""
            SELECT tbl_name FROM sqlite_schema WHERE name = 'idx_interaction_events_session_created'
        """).fetchone() == ("interaction_events",)


def test_migrate_legacy_continuation_rolls_back_on_dangling_evidence(legacy_db):
    db = legacy_db
    db.executescript("""
        PRAGMA foreign_keys = OFF;
        INSERT INTO event_evidence VALUES (999, 21, 1);
        PRAGMA foreign_keys = ON;
    """)
    before = list(db.iterdump())

    with pytest.raises(sqlite3.IntegrityError, match="foreign key validation"):
        migrate_continuations(db)

    assert list(db.iterdump()) == before
    assert db.execute("PRAGMA foreign_keys").fetchone() == (1,)
