import sqlite3
from contextlib import closing
from pathlib import Path

BUSINESS_TABLES = frozenset({
    "accounts", "auth_sessions", "child_profiles", "storybooks", "story_versions",
    "story_pages", "story_branches", "child_grants", "child_grant_books", "reading_sessions",
    "interaction_events", "reading_reports", "request_executions", "knowledge_items", "event_evidence",
})


def database_ready(path: Path) -> bool:
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            tables = {
                name for (name,) in db.execute(
                    "SELECT name FROM sqlite_schema WHERE type = 'table'"
                )
            }
            return BUSINESS_TABLES <= tables
    except sqlite3.Error:
        return False
