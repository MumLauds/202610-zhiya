"""Transactional cleanup helpers for reading sessions, child profiles and cached derived audio."""

from collections.abc import Iterable


def delete_session_rows(db, session_id: int) -> None:
    """Remove all database rows owned by one reading session in foreign-key order."""
    db.execute(
        "DELETE FROM event_evidence WHERE event_id IN (SELECT id FROM interaction_events WHERE session_id = ?)",
        (session_id,),
    )
    db.execute("DELETE FROM interaction_events WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM reading_reports WHERE session_id = ?", (session_id,))
    db.execute(
        """DELETE FROM request_executions
           WHERE session_id = ? OR source_reading_session_id = ?
              OR (scope_type = 'SESSION_CREATE' AND json_extract(response_body, '$.data.readingSessionId') = ?)""",
        (session_id, session_id, session_id),
    )
    # Drafts need the source snapshot for approval. Reviewed versions already have frozen media.
    db.execute(
        """UPDATE story_versions
           SET status = CASE WHEN status IN ('DRAFT', 'PENDING_REVIEW') THEN 'CHECK_FAILED' ELSE status END,
           source_version_id = NULL, source_session_id = NULL,
           source_revision = NULL, source_page_no = NULL, resume_page_no = NULL,
           source_visited_pages_json = NULL WHERE source_session_id = ?""",
        (session_id,),
    )
    db.execute("DELETE FROM reading_sessions WHERE id = ?", (session_id,))


def purge_voice_media(app_state, session_ids: Iterable[int]) -> None:
    """Drop in-memory synthesized audio associated with deleted reading sessions."""
    targets = set(session_ids)
    if not targets:
        return
    store = getattr(app_state, "voice_media", None)
    if store is None:
        return
    purge = getattr(store, "purge_sessions", None)
    if purge is not None:
        purge(targets)
        return
    for key in [k for k, v in list(store.items.items()) if v.snapshot.session_id in targets]:
        store.items.pop(key, None)
