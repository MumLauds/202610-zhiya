"""Read-only bookshelf for guardians and scoped child grants."""

import json
import sqlite3
import time
import unicodedata
from typing import Literal

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.v1.auth import Principal, authorized_db, error
from app.services.reading_agent import SENSITIVE
from app.services.story_media import (
    MediaDependencyError,
    cover_ref_from_manifest,
    ensure_media_dependencies,
    freeze_approval_page_media,
    page_image_ref_for,
    page_image_ref_from_manifest,
    pin_version_media,
)

router = APIRouter(prefix="/api/v1")
MAX_ID = 9007199254740991
AgeBand = Literal["AGE_3_4", "AGE_4_5", "AGE_5_6"]
AGES = {"AGE_3_4": (3, 4), "AGE_4_5": (4, 5), "AGE_5_6": (5, 6)}
AGE_LABELS = {"AGE_3_4": "3～4 岁", "AGE_4_5": "4～5 岁", "AGE_5_6": "5～6 岁"}


def page_object(db: sqlite3.Connection, page_row, *, manifest: dict | None = None) -> dict:
    image_ref = (
        page_image_ref_from_manifest(page_row["version_id"], page_row["page_no"], manifest)
        if manifest is not None
        else page_image_ref_for(db, page_row["version_id"], page_row["page_no"])
    )
    return {
        "pageNumber": page_row["page_no"],
        "text": page_row["body_text"],
        "imageRef": image_ref,
        "defaultNextPageNumber": page_row["default_next_page_number"],
    }


def version_object(db, row, principal: Principal):
    pages = db.execute(
        "SELECT * FROM story_pages WHERE version_id = ? ORDER BY page_no", (row["id"],)
    ).fetchall()
    branches = db.execute(
        "SELECT * FROM story_branches WHERE version_id = ? AND review_status = 'APPROVED' ORDER BY id",
        (row["id"],),
    ).fetchall()
    source_type = row["storybook_source_type"] if "storybook_source_type" in row.keys() else (
        db.execute("SELECT source_type FROM storybooks WHERE id = ?", (row["storybook_id"],)).fetchone()["source_type"]
    )
    manifest = pin_version_media(db, row, source_type=source_type, pages=pages)
    data = {
        "storybookId": row["storybook_id"], "storyVersionId": row["id"],
        "versionNumber": row["version_no"], "title": row["title"], "status": row["status"],
        "coverRef": cover_ref_from_manifest(row["id"], manifest),
        "pages": [page_object(db, p, manifest=manifest) for p in pages],
        "branches": [{"branchId": b["id"], "fromPageNumber": b["from_page_no"],
                      "nextPageNumber": b["next_page_no"], "choiceLabel": b["choice_label"],
                      "reviewStatus": b["review_status"]} for b in branches],
    }
    if principal.role == "GUARDIAN" and row["source_session_id"] is not None:
        data["sourceStoryVersionId"] = row["source_version_id"]
        data["continuation"] = {
            "sourceReadingSessionId": row["source_session_id"],
            "sourceStoryVersionId": row["source_version_id"],
            "sourceRevision": row["source_revision"],
            "sourcePageNumber": row["source_page_no"],
            "sourceVisitedPageNumbers": json.loads(row["source_visited_pages_json"]),
            "resumePageNumber": row["resume_page_no"],
        }
    return data


class VersionApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    versionNumber: int = Field(ge=1, le=MAX_ID, strict=True)
    reviewedAllBranches: bool = Field(strict=True)


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    childProfileId: int = Field(ge=1, le=MAX_ID, strict=True)
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    minutes: int | None = Field(default=None, ge=1, le=30, strict=True)

    @field_validator("topic", mode="before")
    @classmethod
    def clean_topic(cls, value):
        return value.strip() if isinstance(value, str) else value


# PUBLISHED is public to authenticated guardians; private versions require ownership.
# Retired versions are available only as reviewed private originals belonging to the guardian.
VISIBLE = """(v.status = 'PUBLISHED' OR
    (v.status = 'APPROVED_PRIVATE' AND b.owner_guardian_id = ?) OR
    (? AND v.status = 'RETIRED' AND b.source_type = 'ORIGINAL'
       AND b.owner_guardian_id = ? AND v.reviewed_by_id IS NOT NULL AND v.reviewed_at IS NOT NULL))"""


@router.get("/admin/story-versions")
def list_admin_story_versions(request: Request, page: int = Query(default=1, ge=1, le=MAX_ID),
                              page_size: int = Query(default=20, ge=1, le=50, alias="pageSize")):
    """List library versions for editorial review, including drafts and withdrawn versions."""
    try:
        with authorized_db(request, allow_admin=True) as (db, principal, failure):
            if failure:
                return failure
            if principal.role != "ADMIN":
                return error(request, 403, "FORBIDDEN", "仅限内容管理员")
            versions = db.execute(
                """SELECT v.* FROM story_versions v JOIN storybooks b ON b.id = v.storybook_id
                   WHERE b.source_type = 'LIBRARY' ORDER BY v.storybook_id, v.version_no DESC LIMIT ? OFFSET ?""",
                (page_size, (page - 1) * page_size),
            )
            items = []
            for version in versions:
                pages = db.execute(
                    """SELECT page_no AS pageNumber, body_text AS text FROM story_pages
                       WHERE version_id = ? ORDER BY page_no""", (version["id"],))
                branches = db.execute(
                    """SELECT id AS branchId, from_page_no AS fromPageNumber, next_page_no AS nextPageNumber,
                              choice_label AS choiceLabel, review_status AS reviewStatus
                       FROM story_branches WHERE version_id = ? ORDER BY id""", (version["id"],))
                items.append({
                    "storybookId": version["storybook_id"], "storyVersionId": version["id"],
                    "versionNumber": version["version_no"], "title": version["title"], "status": version["status"],
                    "topics": json.loads(version["topics_json"]), "licenseNote": version["license_note"],
                    "pages": [dict(row) for row in pages], "branches": [dict(row) for row in branches],
                })
            return {"data": {"items": items, "page": page, "pageSize": page_size}, "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "暂时无法读取绘本版本", retryable=True)


@router.get("/storybooks")
def list_storybooks(request: Request, age_band: AgeBand | None = Query(default=None, alias="ageBand"),
                    topic: str | None = Query(default=None, min_length=1, max_length=100),
                    scope: Literal["mine"] | None = None,
                    page: int = Query(default=1, ge=1, le=9007199254740991),
                    page_size: int = Query(default=20, ge=1, le=50, alias="pageSize")):
    try:
        with authorized_db(request) as (db, principal, failure):
            if failure:
                return failure
            if principal.role == "CHILD" and scope == "mine":
                return error(request, 403, "FORBIDDEN", "儿童模式不支持查看个人历史书架")
            conditions = [VISIBLE]
            params = [principal.guardian_id, scope == "mine", principal.guardian_id]
            if principal.role == "CHILD":
                conditions.append("EXISTS (SELECT 1 FROM child_grant_books gb WHERE gb.grant_id = ? AND gb.version_id = v.id)")
                params.append(principal.grant_id)
            if age_band:
                low, high = AGES[age_band]
                conditions.append("v.age_min <= ? AND v.age_max >= ?")
                params.extend((high, low))
            if topic is not None:
                conditions.append("EXISTS (SELECT 1 FROM json_each(v.topics_json) WHERE value = ?)")
                params.append(topic)
            # Avoid exceeding SQLite's signed 64-bit integer range for very large pages.
            offset = min((page - 1) * page_size, 9223372036854775807)
            rows = db.execute(
                "SELECT v.*, b.source_type AS storybook_source_type FROM story_versions v "
                "JOIN storybooks b ON b.id = v.storybook_id WHERE "
                + " AND ".join(conditions) + " ORDER BY v.storybook_id, v.version_no DESC LIMIT ? OFFSET ?",
                (*params, page_size, offset),
            ).fetchall()
            ensure_media_dependencies()
            items = [version_object(db, row, principal) for row in rows]
            return {"data": {"items": items, "page": page, "pageSize": page_size}, "requestId": request.state.request_id}
    except (sqlite3.Error, MediaDependencyError):
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)


def readable_story_version(db, principal: Principal, version_id: int):
    """Apply the same live content permissions to detail, media, and cached page responses."""
    guardian = principal.guardian_id
    child = principal.role == "CHILD"
    grant_filter = (
        "AND EXISTS (SELECT 1 FROM child_grant_books gb WHERE gb.grant_id = ? AND gb.version_id = v.id) "
        if child else ""
    )
    active_session_filter = (
        "EXISTS (SELECT 1 FROM reading_sessions s WHERE s.version_id = v.id AND s.grant_id = ? "
        "AND s.profile_id = ? AND s.status IN ('CREATED', 'IN_PROGRESS'))"
        if child else
        "EXISTS (SELECT 1 FROM reading_sessions s JOIN child_profiles p ON p.id = s.profile_id "
        "WHERE s.version_id = v.id AND p.guardian_id = ? AND s.status IN ('CREATED', 'IN_PROGRESS'))"
    )
    params = ((version_id, principal.grant_id, guardian, True, guardian, principal.grant_id, principal.profile_id)
              if child else (version_id, guardian, True, guardian, guardian))
    return db.execute(
        "SELECT v.*, b.source_type AS storybook_source_type FROM story_versions v "
        "JOIN storybooks b ON b.id = v.storybook_id "
        f"WHERE v.id = ? {grant_filter}AND (" + VISIBLE +
        " OR (v.status = 'RETIRED' AND v.reviewed_by_id IS NOT NULL AND v.reviewed_at IS NOT NULL "
        f"AND b.source_type = 'LIBRARY' AND {active_session_filter}))",
        params,
    ).fetchone()


@router.get("/story-versions/{version_id}")
def get_story_version(request: Request, version_id: int = Path(ge=1, le=9007199254740991)):
    try:
        with authorized_db(request) as (db, principal, failure):
            if failure:
                return failure
            row = readable_story_version(db, principal, version_id)
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "绘本不可用")
            return {"data": version_object(db, row, principal), "requestId": request.state.request_id}
    except (sqlite3.Error, MediaDependencyError):
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)


@router.post("/recommendations")
def recommend_storybooks(payload: RecommendationRequest, request: Request):
    """API-010: age-band and topic recommendations across accessible current PUBLISHED/APPROVED_PRIVATE versions."""
    try:
        with authorized_db(request, csrf=True, guardian_only=True) as (db, principal, failure):
            if failure:
                return failure
            owner = principal.guardian_id
            profile = db.execute(
                "SELECT id, age_band, interests_json FROM child_profiles WHERE id = ? AND guardian_id = ?",
                (payload.childProfileId, owner),
            ).fetchone()
            if not profile:
                return error(request, 404, "RESOURCE_NOT_FOUND", "儿童档案不可用")
            low, high = AGES[profile["age_band"]]
            conditions = [
                VISIBLE,
                "v.age_min <= ? AND v.age_max >= ?",
                "EXISTS (SELECT 1 FROM story_pages p WHERE p.version_id = v.id AND p.page_no = 1)",
            ]
            params: list[object] = [owner, False, owner, high, low]
            if payload.topic is not None:
                conditions.append("EXISTS (SELECT 1 FROM json_each(v.topics_json) WHERE value = ?)")
                params.append(payload.topic)
            rows = db.execute(
                """SELECT v.id,
                          (SELECT topic.value FROM json_each(v.topics_json) AS topic
                           WHERE topic.value IN (SELECT value FROM json_each(?))
                           ORDER BY topic.key LIMIT 1) AS matched_interest
                   FROM story_versions v JOIN storybooks b ON b.id = v.storybook_id WHERE """
                + " AND ".join(conditions)
                + " ORDER BY matched_interest IS NULL, v.storybook_id, v.version_no DESC LIMIT 10",
                (profile["interests_json"], *params),
            ).fetchall()
            age_label = AGE_LABELS[profile["age_band"]]
            items = []
            for row in rows:
                if payload.topic is not None:
                    reason = f"符合 {age_label} 与主题「{payload.topic}」"
                elif row["matched_interest"] is not None:
                    reason = f"符合 {age_label} 与兴趣「{row['matched_interest']}」"
                else:
                    reason = f"适合 {age_label} 共读"
                items.append({"storyVersionId": row["id"], "reason": reason, "sources": []})
            return {"data": {"items": items}, "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)


def _source_path_matches(db, session, revision: int, prefix: list[int]) -> bool:
    if revision > session["revision"]:
        return False
    if revision == session["revision"]:
        return (session["status"] == "IN_PROGRESS" and session["current_page_no"] == prefix[-1]
                and json.loads(session["visited_pages_json"]) == prefix)

    # Branch choices and continuation starts save the effective path. Ordinary page turns save the new page.
    pages = []
    for event in db.execute(
        """SELECT COALESCE(b.next_page_no, e.page_no) AS page_no,
                  json_extract(e.response_json, '$.data.visitedPageNumbers') AS visited
           FROM interaction_events e LEFT JOIN story_branches b ON b.id = e.branch_id
           WHERE e.session_id = ? AND e.revision_after <= ?
             AND e.event_type IN ('STARTED', 'PAGE_CHANGED', 'BRANCH_CHOSEN')
           ORDER BY e.revision_after, e.id""",
        (session["id"], revision),
    ):
        if event["visited"] is None:
            pages.append(event["page_no"])
        else:
            pages = json.loads(event["visited"])
    return pages == prefix


def _verify_version_for_approval(db, version_row, owner: int) -> str | None:
    """Re-verify structure, safety, license, DAG path bounds (4-6 pages), branch rules and continuation snapshot."""
    title = (version_row["title"] or "").strip()
    if not title or not (version_row["license_note"] or "").strip():
        return "绘本标题或授权说明为空"
    if SENSITIVE.search(unicodedata.normalize("NFKC", title)):
        return "绘本标题未通过安全检查"
    pages = db.execute(
        "SELECT * FROM story_pages WHERE version_id = ? ORDER BY page_no", (version_row["id"],)
    ).fetchall()
    if not 4 <= len(pages) <= 6:
        return "绘本页数须为 4 至 6 页"
    page_map = {p["page_no"]: p for p in pages}
    if 1 not in page_map:
        return "缺少第 1 页"
    for p in pages:
        body = (p["body_text"] or "").strip()
        if not body:
            return "存在空白正文页面"
        if SENSITIVE.search(unicodedata.normalize("NFKC", body)):
            return "存在未通过安全检查的页面正文"
        nxt = p["default_next_page_number"]
        if nxt is not None and nxt not in page_map:
            return "存在指向不存在页面的默认下一页"

    branches = db.execute(
        "SELECT * FROM story_branches WHERE version_id = ? ORDER BY id", (version_row["id"],)
    ).fetchall()
    by_from: dict[int, list] = {}
    for b in branches:
        if b["review_status"] == "REJECTED":
            return "存在已驳回的分支选项"
        label = (b["choice_label"] or "").strip()
        if not label or SENSITIVE.search(unicodedata.normalize("NFKC", label)):
            return "存在空白或未通过安全检查的分支选项"
        by_from.setdefault(b["from_page_no"], []).append(b)
    if version_row["branching"] == 0:
        if branches:
            return "非分支版本不得包含故事分支"
    else:
        if not 1 <= len(by_from) <= 2 or any(
            len({b["next_page_no"] for b in options}) < 2 for options in by_from.values()
        ):
            return "分支版本须包含 1 至 2 个决策节点，每个节点至少有两个不同的后续页"

    reachable: set[int] = set()
    stack: list[tuple[int, set[int], int]] = [(1, {1}, 1)]
    while stack:
        node, path_seen, length = stack.pop()
        reachable.add(node)
        nexts: list[int] = []
        default_next = page_map[node]["default_next_page_number"]
        if default_next is not None:
            nexts.append(default_next)
        for b in by_from.get(node, ()):
            if b["next_page_no"] not in nexts:
                nexts.append(b["next_page_no"])
        if not nexts:
            if not 4 <= length <= 6:
                return "存在不符合 4 至 6 页长度的阅读路径"
            continue
        for nxt in nexts:
            if nxt in path_seen or nxt not in page_map:
                return "绘本阅读路径存在环路或无效跳转"
            stack.append((nxt, path_seen | {nxt}, length + 1))
    if reachable != set(page_map):
        return "存在不可达页面"

    if version_row["source_session_id"] is not None:
        if (version_row["source_version_id"] is None or version_row["source_revision"] is None
                or version_row["source_page_no"] is None or version_row["resume_page_no"] is None
                or version_row["source_visited_pages_json"] is None):
            return "续写版本缺少源会话快照"
        prefix = json.loads(version_row["source_visited_pages_json"])
        if (not isinstance(prefix, list) or not prefix
                or not all(isinstance(pno, int) and not isinstance(pno, bool) and pno >= 1 for pno in prefix)
                or prefix[0] != 1 or prefix[-1] != version_row["source_page_no"]
                or version_row["resume_page_no"] != len(prefix) + 1):
            return "续写前缀或继续页码不合法"
        src = db.execute(
            """SELECT s.id, s.status, s.revision, s.current_page_no, s.visited_pages_json FROM reading_sessions s
               JOIN child_profiles p ON p.id = s.profile_id
               JOIN story_versions sv ON sv.id = s.version_id
               WHERE s.id = ? AND p.guardian_id = ? AND s.version_id = ? AND sv.storybook_id = ?
                 AND s.mode = 'PARENT_CHILD' AND sv.status <> 'WITHDRAWN'""",
            (version_row["source_session_id"], owner, version_row["source_version_id"], version_row["storybook_id"]),
        ).fetchone()
        if not src:
            return "续写源会话与绘本不匹配或不可用"
        source_pages = {p["page_no"]: p for p in db.execute(
            "SELECT page_no, body_text, image_ref, default_next_page_number FROM story_pages WHERE version_id = ?",
            (version_row["source_version_id"],),
        )}
        for carried_page, source_page in enumerate(prefix, start=1):
            if source_page not in source_pages:
                return "续写源版本页面不存在"
            if carried_page not in page_map:
                return "续写前文页码不完整"
            if page_map[carried_page]["body_text"] != source_pages[source_page]["body_text"]:
                return "续写前文正文与源版本不一致"
            if page_map[carried_page]["image_ref"] != source_pages[source_page]["image_ref"]:
                return "续写前文配图与源版本不一致"
        source_branches = {
            (branch["from_page_no"], branch["next_page_no"]) for branch in db.execute(
                "SELECT from_page_no, next_page_no FROM story_branches WHERE version_id = ? AND review_status = 'APPROVED'",
                (version_row["source_version_id"],),
            )
        }
        for start, end in zip(prefix, prefix[1:]):
            if source_pages[start]["default_next_page_number"] != end and (start, end) not in source_branches:
                return "续写前文包含源版本中不可读的跳转"
        if not _source_path_matches(db, src, version_row["source_revision"], prefix):
            return "续写快照与源会话进度不一致"
        for pno in range(1, len(prefix)):
            if page_map[pno]["default_next_page_number"] != pno + 1:
                return "续写前文阅读路径不连续"
        resume_no = version_row["resume_page_no"]
        k = len(prefix)
        k_targets = {b["next_page_no"] for b in by_from.get(k, ())}
        if page_map[k]["default_next_page_number"] is not None:
            k_targets.add(page_map[k]["default_next_page_number"])
        if resume_no not in page_map or resume_no not in k_targets:
            return "续写继续页码不可达"
    return None


@router.post("/story-versions/{version_id}/approvals")
def approve_story_version(payload: VersionApproval, request: Request,
                          version_id: int = Path(ge=1, le=MAX_ID)):
    """API-026: re-verify and privately approve an owned ORIGINAL story version (retiring prior active version)."""
    try:
        with authorized_db(request, write=True, guardian_only=True) as (db, principal, failure):
            if failure:
                return failure
            owner = principal.guardian_id
            row = db.execute(
                """SELECT v.*, b.source_type AS storybook_source_type FROM story_versions v
                   JOIN storybooks b ON b.id = v.storybook_id
                   WHERE v.id = ? AND b.owner_guardian_id = ? AND b.source_type = 'ORIGINAL'""",
                (version_id, owner),
            ).fetchone()
            if not row or row["status"] == "WITHDRAWN":
                return error(request, 404, "RESOURCE_NOT_FOUND", "绘本草稿不可用")
            if not payload.reviewedAllBranches:
                return error(request, 422, "VALIDATION_ERROR", "请先完成全部页面与分支审核")
            if row["status"] != "PENDING_REVIEW" or row["version_no"] != payload.versionNumber:
                return error(request, 409, "SESSION_CONFLICT", "版本状态或版本号已变化")
            reason = _verify_version_for_approval(db, row, owner)
            if reason is not None:
                return error(request, 422, "VALIDATION_ERROR", reason)
            page_media = freeze_approval_page_media(db, row)
            now = int(time.time() * 1000)
            db.execute(
                """UPDATE story_versions SET status = 'RETIRED'
                   WHERE storybook_id = ? AND status IN ('PUBLISHED', 'APPROVED_PRIVATE') AND id <> ?""",
                (row["storybook_id"], version_id),
            )
            db.execute(
                "UPDATE story_branches SET review_status = 'APPROVED' WHERE version_id = ? AND review_status = 'PENDING'",
                (version_id,),
            )
            db.execute(
                """UPDATE story_versions SET status = 'APPROVED_PRIVATE', reviewed_by_id = ?, reviewed_at = ?,
                   approved_page_media_json = ?
                   WHERE id = ? AND status = 'PENDING_REVIEW'""",
                (owner, now, page_media, version_id),
            )
            updated = db.execute(
                """SELECT v.*, b.source_type AS storybook_source_type FROM story_versions v
                   JOIN storybooks b ON b.id = v.storybook_id WHERE v.id = ?""",
                (version_id,),
            ).fetchone()
            return {"data": version_object(db, updated, principal), "requestId": request.state.request_id}
    except MediaDependencyError:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
