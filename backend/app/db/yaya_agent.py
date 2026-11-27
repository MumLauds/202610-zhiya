"""Additive migration and durable, expiring model-call reservations."""

import hmac
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from app.db.connection import connect
from app.services.yaya_agent_config import AGENT_VERSION, YayaSettings

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS yaya_agent_sessions (
       session_id INTEGER PRIMARY KEY REFERENCES reading_sessions(id) ON DELETE CASCADE,
       agent_version TEXT NOT NULL CHECK (agent_version = '2.0')
    )""",
    """CREATE TABLE IF NOT EXISTS yaya_agent_calls (
       call_id TEXT PRIMARY KEY, day TEXT NOT NULL, guardian_tag TEXT NOT NULL, session_tag TEXT NOT NULL,
       ceiling_micros INTEGER NOT NULL CHECK (ceiling_micros > 0), lease_until INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_yaya_calls_day ON yaya_agent_calls(day)",
)


def install():
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA:
            db.execute(statement)


def pin_new_session(db, settings: YayaSettings, session_id: int, guardian_id: int):
    if settings.enrolls(guardian_id):
        db.execute("INSERT INTO yaya_agent_sessions (session_id, agent_version) VALUES (?, ?)", (session_id, AGENT_VERSION))


def pinned(db, session_id: int) -> bool:
    # Older databases can continue serving with the feature off before an approved migration.
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='yaya_agent_sessions'").fetchone():
        return False
    return db.execute("SELECT 1 FROM yaya_agent_sessions WHERE session_id=?", (session_id,)).fetchone() is not None


def purge_expired(db, settings: YayaSettings, today: date):
    db.execute("DELETE FROM yaya_agent_calls WHERE day < ?", ((today - timedelta(days=settings.retention_days - 1)).isoformat(),))


def reserve(settings: YayaSettings, call_id: str, guardian_id: int, session_id: int, ceiling_micros: int) -> bool:
    now = time.time()
    today = datetime.fromtimestamp(now, timezone.utc).date()
    guardian = hmac.digest(settings.ledger_secret.encode(), f"guardian:{guardian_id}".encode(), "sha256").hex()
    session = hmac.digest(settings.ledger_secret.encode(), f"session:{session_id}".encode(), "sha256").hex()
    with closing(connect(timeout=0.2)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        purge_expired(db, settings, today)
        usage = db.execute(
            """SELECT COALESCE(SUM(ceiling_micros), 0),
                      COUNT(*) FILTER (WHERE guardian_tag = ?),
                      COUNT(*) FILTER (WHERE session_tag = ?)
               FROM yaya_agent_calls WHERE day = ?""",
            (guardian, session, today.isoformat()),
        ).fetchone()
        active, session_active = db.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE session_tag = ?) FROM yaya_agent_calls WHERE lease_until > ?",
            (session, int(now * 1000)),
        ).fetchone()
        if (usage[0] + ceiling_micros > settings.daily_micros or usage[1] >= settings.guardian_calls
                or usage[2] >= settings.session_calls or active >= settings.concurrency or session_active):
            return False
        db.execute("INSERT INTO yaya_agent_calls VALUES (?, ?, ?, ?, ?, ?)",
                   (call_id, today.isoformat(), guardian, session, ceiling_micros, int(now * 1000) + 5500))
        return True


def release(call_id: str):
    # Retain the conservative daily reservation even on cancellation or missing upstream usage.
    with closing(connect(timeout=0.2)) as db, db:
        db.execute("UPDATE yaya_agent_calls SET lease_until=0 WHERE call_id=?", (call_id,))


def expire(settings: YayaSettings):
    with closing(connect(timeout=0.2)) as db, db:
        if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='yaya_agent_calls'").fetchone():
            purge_expired(db, settings, datetime.now(timezone.utc).date())
