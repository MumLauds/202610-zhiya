"""Read-only guardian bookshelf; child grants and media delivery are future work."""

import sqlite3
from contextlib import closing
from typing import Literal

from fastapi import APIRouter, Path, Query, Request

from app.api.v1.auth import connect, error
from app.api.v1.profiles import guardian

router = APIRouter(prefix="/api/v1")
AgeBand = Literal["AGE_3_4", "AGE_4_5", "AGE_5_6"]
AGES = {"AGE_3_4": (3, 4), "AGE_4_5": (4, 5), "AGE_5_6": (5, 6)}


def version_object(db, row):
    pages = db.execute(
        "SELECT * FROM story_pages WHERE version_id = ? ORDER BY page_no", (row["id"],)
    ).fetchall()
    branches = db.execute(
        "SELECT * FROM story_branches WHERE version_id = ? AND review_status = 'APPROVED' ORDER BY id",
        (row["id"],),
    ).fetchall()
    return {
        "storybookId": row["storybook_id"], "storyVersionId": row["id"],
        "versionNumber": row["version_no"], "title": row["title"], "status": row["status"],
        # No verified, version-bound media resolver exists yet. Never expose seed paths.
        "coverRef": None,
        "pages": [{"pageNumber": p["page_no"], "text": p["body_text"], "imageRef": None,
                   "defaultNextPageNumber": p["default_next_page_number"]} for p in pages],
        "branches": [{"branchId": b["id"], "fromPageNumber": b["from_page_no"],
                      "nextPageNumber": b["next_page_no"], "choiceLabel": b["choice_label"],
                      "reviewStatus": b["review_status"]} for b in branches],
    }


# PUBLISHED is public to authenticated guardians; private versions require ownership.
# Retired versions are available only as reviewed private originals belonging to the guardian.
VISIBLE = """(v.status = 'PUBLISHED' OR
    (v.status = 'APPROVED_PRIVATE' AND b.owner_guardian_id = ?) OR
    (? AND v.status = 'RETIRED' AND b.source_type = 'ORIGINAL'
       AND b.owner_guardian_id = ? AND v.reviewed_by_id IS NOT NULL AND v.reviewed_at IS NOT NULL))"""


@router.get("/storybooks")
def list_storybooks(request: Request, ageBand: AgeBand | None = None,
                    topic: str | None = Query(default=None, min_length=1, max_length=100),
                    scope: Literal["mine"] | None = None,
                    page: int = Query(default=1, ge=1, le=9007199254740991),
                    pageSize: int = Query(default=20, ge=1, le=50)):
    try:
        with closing(connect()) as db:
            db.execute("BEGIN")
            owner, failure = guardian(db, request, write=False)
            if failure:
                return failure
            conditions = [VISIBLE]
            params = [owner, scope == "mine", owner]
            if ageBand:
                low, high = AGES[ageBand]
                conditions.append("v.age_min <= ? AND v.age_max >= ?")
                params.extend((high, low))
            if topic is not None:
                conditions.append("EXISTS (SELECT 1 FROM json_each(v.topics_json) WHERE value = ?)")
                params.append(topic)
            # Avoid exceeding SQLite's signed 64-bit integer range for very large pages.
            offset = min((page - 1) * pageSize, 9223372036854775807)
            rows = db.execute(
                "SELECT v.* FROM story_versions v JOIN storybooks b ON b.id = v.storybook_id WHERE "
                + " AND ".join(conditions) + " ORDER BY v.storybook_id, v.version_no DESC LIMIT ? OFFSET ?",
                (*params, pageSize, offset),
            ).fetchall()
            items = [version_object(db, row) for row in rows]
            return {"data": {"items": items, "page": page, "pageSize": pageSize}, "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)


@router.get("/story-versions/{version_id}")
def get_story_version(request: Request, version_id: int = Path(ge=1, le=9007199254740991)):
    try:
        with closing(connect()) as db:
            db.execute("BEGIN")
            owner, failure = guardian(db, request, write=False)
            if failure:
                return failure
            row = db.execute(
                "SELECT v.* FROM story_versions v JOIN storybooks b ON b.id = v.storybook_id "
                "WHERE v.id = ? AND " + VISIBLE,
                (version_id, owner, True, owner),
            ).fetchone()
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "绘本不可用")
            return {"data": version_object(db, row), "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)
