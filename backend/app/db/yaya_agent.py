"""Additive migration and durable, expiring model-call reservations."""

import hmac
import logging
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from app.db.connection import connect
from app.services.yaya_agent_config import AGENT_VERSION, YayaSettings

logger = logging.getLogger("zhiya.yaya_agent")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS yaya_agent_input_consents (
       profile_id INTEGER PRIMARY KEY REFERENCES child_profiles(id) ON DELETE CASCADE,
       expires_at INTEGER NOT NULL CHECK (expires_at > 0)
    )""",
    """CREATE TABLE IF NOT EXISTS yaya_agent_budget_warnings (day TEXT PRIMARY KEY)""",
    """CREATE TABLE IF NOT EXISTS yaya_agent_sessions (
       session_id INTEGER PRIMARY KEY REFERENCES reading_sessions(id) ON DELETE CASCADE,
       agent_version TEXT NOT NULL CHECK (agent_version = '2.0')
    )""",
    """CREATE TABLE IF NOT EXISTS yaya_agent_calls (
       call_id TEXT PRIMARY KEY, day TEXT NOT NULL, guardian_tag TEXT NOT NULL, session_tag TEXT NOT NULL,
       ceiling_micros INTEGER NOT NULL CHECK (ceiling_micros > 0), lease_until INTEGER NOT NULL,
       settled_micros INTEGER CHECK (settled_micros >= 0),
       priced INTEGER NOT NULL DEFAULT 0 CHECK (priced IN (0,1))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_yaya_calls_day ON yaya_agent_calls(day)",
)


def install():
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA:
            db.execute(statement)
        columns = {col[1] for col in db.execute("PRAGMA table_info(yaya_agent_calls)")}
        if "settled_micros" not in columns:
            db.execute("ALTER TABLE yaya_agent_calls ADD COLUMN settled_micros INTEGER CHECK (settled_micros >= 0)")
        if "priced" not in columns:
            db.execute("ALTER TABLE yaya_agent_calls ADD COLUMN priced INTEGER NOT NULL DEFAULT 0 CHECK (priced IN (0,1))")


def pin_new_session(db, settings: YayaSettings, session_id: int, guardian_id: int):
    if settings.enrolls(guardian_id):
        db.execute("INSERT INTO yaya_agent_sessions (session_id, agent_version) VALUES (?, ?)", (session_id, AGENT_VERSION))


def pinned(db, session_id: int) -> bool:
    # Older databases can continue serving with the feature off before an approved migration.
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='yaya_agent_sessions'").fetchone():
        return False
    return db.execute("SELECT 1 FROM yaya_agent_sessions WHERE session_id=?", (session_id,)).fetchone() is not None


def grant_input_consent(guardian_id: int, profile_id: int, expires_at: int):
    """Register an already-approved, expiring consent on the owned profile."""
    if expires_at <= int(time.time() * 1000):
        raise ValueError("Input consent expiry must be in the future")
    with closing(connect()) as db, db:
        updated = db.execute(
            """INSERT INTO yaya_agent_input_consents(profile_id, expires_at)
               SELECT id, ? FROM child_profiles WHERE id=? AND guardian_id=?
               ON CONFLICT(profile_id) DO UPDATE SET expires_at=excluded.expires_at""",
            (expires_at, profile_id, guardian_id),
        )
        if updated.rowcount != 1:
            raise ValueError("Input consent requires an owned profile")


def revoke_input_consent(guardian_id: int, profile_id: int):
    with closing(connect()) as db, db:
        db.execute(
            """DELETE FROM yaya_agent_input_consents WHERE profile_id IN
               (SELECT id FROM child_profiles WHERE id=? AND guardian_id=?)""",
            (profile_id, guardian_id),
        )


def input_consent_active(db, profile_id: int) -> bool:
    return db.execute(
        "SELECT 1 FROM yaya_agent_input_consents WHERE profile_id=? AND expires_at>?",
        (profile_id, int(time.time() * 1000)),
    ).fetchone() is not None


def purge_expired(db, settings: YayaSettings, today: date):
    cutoff = (today - timedelta(days=settings.retention_days - 1)).isoformat()
    db.execute("DELETE FROM yaya_agent_calls WHERE day < ?", (cutoff,))
    db.execute("DELETE FROM yaya_agent_budget_warnings WHERE day < ?", (cutoff,))


def reserve_or_reason(settings: YayaSettings, call_id: str, guardian_id: int, session_id: int,
                      ceiling_micros: int) -> str | None:
    """Return a refusal reason, or None after atomically recording a reservation."""
    if ceiling_micros <= 0:
        return "BUDGET_LIMIT"
    guardian = hmac.digest(settings.ledger_secret.encode(), f"guardian:{guardian_id}".encode(), "sha256").hex()
    session = hmac.digest(settings.ledger_secret.encode(), f"session:{session_id}".encode(), "sha256").hex()
    with closing(connect(timeout=0.2)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        now = time.time()
        today = datetime.fromtimestamp(now, timezone.utc).date()
        purge_expired(db, settings, today)
        usage = db.execute(
            """SELECT COALESCE(SUM(COALESCE(settled_micros, ceiling_micros)) FILTER (WHERE priced = 1), 0),
                      COUNT(*) FILTER (WHERE guardian_tag = ?),
                      COUNT(*) FILTER (WHERE session_tag = ?),
                      COUNT(*) FILTER (WHERE priced = 0)
               FROM yaya_agent_calls WHERE day = ?""",
            (guardian, session, today.isoformat()),
        ).fetchone()
        active, session_active = db.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE session_tag = ?) FROM yaya_agent_calls WHERE lease_until > ?",
            (session, int(now * 1000)),
        ).fetchone()
        # Old ledger estimates used a different unit. Hold that UTC day without repricing them.
        if usage[3]:
            return "DEPENDENCY_UNAVAILABLE"
        committed_micros = usage[0] + ceiling_micros
        if committed_micros > settings.daily_micros:
            return "BUDGET_LIMIT"
        if usage[1] >= settings.guardian_calls:
            return "GUARDIAN_CALL_LIMIT"
        if usage[2] >= settings.session_calls:
            return "SESSION_CALL_LIMIT"
        if active >= settings.concurrency or session_active:
            return "AGENT_BUSY"
        db.execute("""INSERT INTO yaya_agent_calls (call_id, day, guardian_tag, session_tag, ceiling_micros, lease_until, priced)
                      VALUES (?, ?, ?, ?, ?, ?, 1)""",
                   (call_id, today.isoformat(), guardian, session, ceiling_micros, int(now * 1000) + 5500))
        warn = committed_micros * 5 >= settings.daily_micros * 4 and db.execute(
            "INSERT OR IGNORE INTO yaya_agent_budget_warnings(day) VALUES (?)", (today.isoformat(),),
        ).rowcount == 1
    if warn:
        logger.warning("yaya outcome=BUDGET_NEAR_LIMIT day=%s committed_micros=%d daily_micros=%d",
                       today.isoformat(), committed_micros, settings.daily_micros)
    return None


def reserve(settings: YayaSettings, call_id: str, guardian_id: int, session_id: int, ceiling_micros: int) -> bool:
    return reserve_or_reason(settings, call_id, guardian_id, session_id, ceiling_micros) is None


def settle(call_id: str, actual_micros: int | None):
    # Missing usage retains the reservation, while every outcome releases the concurrency lease.
    with closing(connect(timeout=0.2)) as db, db:
        db.execute("""UPDATE yaya_agent_calls SET lease_until=0, settled_micros=?
                      WHERE call_id=? AND lease_until>0""", (actual_micros, call_id))


def expire(settings: YayaSettings):
    with closing(connect(timeout=0.2)) as db, db:
        if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='yaya_agent_calls'").fetchone():
            purge_expired(db, settings, datetime.now(timezone.utc).date())
