"""Transactional editorial writes for the shared library and versioned knowledge base."""
import hashlib
import json
import sqlite3
import time
import unicodedata
from collections import deque
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.api.v1.auth import authorized_db, error
from app.api.v1.storybooks import AGES, MAX_ID, AgeBand, admin_story_object
from app.errors import ServiceError
from app.services.reading_agent import SENSITIVE
from app.services.story_media import (
    MediaDependencyError, SEED_COVER_SOURCE_REF, cover_ref_from_manifest, freeze_approval_page_media, pin_version_media,
)

router = APIRouter(prefix="/api/v1/admin")
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Id = Annotated[int, Field(strict=True, ge=1, le=MAX_ID)]
Tags = Annotated[list[Tag], Field(min_length=1, max_length=32)]
Tasks = Annotated[list[Literal["GUIDANCE", "RETRIEVAL", "SAFETY"]], Field(min_length=1, max_length=3)]
Status = Literal["DRAFT", "APPROVED", "RETIRED"]


class EditorialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoryPage(EditorialInput):
    pageNumber: Id
    text: Text
    imageRef: Text
    defaultNextPageNumber: Id | None = None


class StoryBranch(EditorialInput):
    fromPageNumber: Id
    nextPageNumber: Id
    choiceLabel: Tag


class LibraryDraft(EditorialInput):
    title: Tag
    ageBand: AgeBand
    topics: Tags
    pages: list[StoryPage] = Field(min_length=1, max_length=200)
    branches: list[StoryBranch] = Field(default_factory=list, max_length=400)
    licenseNote: Text
    storybookId: Id | None = None


class StoryStatus(EditorialInput):
    status: Literal["PUBLISHED", "WITHDRAWN"]


class KnowledgeCreate(EditorialInput):
    itemKey: Tag
    title: Tag
    sourceTitle: Tag
    sourceReference: Text
    locator: Text
    ageBand: AgeBand | None
    topicTags: Tags
    taskTypes: Tasks
    content: Text
    status: Literal["DRAFT", "APPROVED"] = "DRAFT"
    versionNumber: Literal[1] = 1


class KnowledgePatch(EditorialInput):
    title: Tag | None = None
    sourceTitle: Tag | None = None
    sourceReference: Text | None = None
    locator: Text | None = None
    ageBand: AgeBand | None = None
    topicTags: Tags | None = None
    taskTypes: Tasks | None = None
    content: Text | None = None
    status: Status | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        fields = self.model_fields_set
        if not fields or any(getattr(self, key) is None for key in fields - {"ageBand"}):
            raise ValueError("请提供有效的修订字段")
        if self.status == "RETIRED" and fields != {"status"}:
            raise ValueError("停用请求只能包含状态")
        return self


def admin_request(request, action, *, payload=None, created=False):
    """Keep authorization, optional idempotency receipt and business changes in one transaction."""
    try:
        with authorized_db(request, write=payload is not None, allow_admin=True) as (db, principal, failure):
            if failure:
                return failure
            if principal.role != "ADMIN":
                return error(request, 403, "FORBIDDEN", "仅限内容管理员")
            admin_id = principal.guardian_id
            key = request.headers.get("Idempotency-Key") if payload is not None else None
            if key is not None:
                if not key.isascii() or not 1 <= len(key) <= 64 or any(ord(c) < 33 or ord(c) > 126 for c in key):
                    return error(request, 422, "VALIDATION_ERROR", "无效的幂等键")
                body = json.dumps([request.method, request.url.path, payload.model_dump(exclude_unset=True)],
                                  ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(body.encode()).hexdigest()
                saved = db.execute("SELECT digest, response_json FROM admin_write_receipts WHERE admin_id=? AND request_key=?",
                                   (admin_id, key)).fetchone()
                if saved:
                    if saved["digest"] != digest:
                        return error(request, 409, "IDEMPOTENCY_CONFLICT", "该幂等键已经用于其他请求")
                    return JSONResponse({"data": json.loads(saved["response_json"]), "requestId": request.state.request_id})
            result = action(db, admin_id)
            if key is not None:
                db.execute("INSERT INTO admin_write_receipts(admin_id,request_key,digest,response_json) VALUES(?,?,?,?)",
                           (admin_id, key, digest, json.dumps(result, ensure_ascii=False)))
            return JSONResponse({"data": result, "requestId": request.state.request_id}, status_code=201 if created else 200)
    except (sqlite3.Error, MediaDependencyError):
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "暂时无法访问内容或素材库", retryable=True)


def invalid(message):
    raise ServiceError("VALIDATION_ERROR", message, 422)


def conflict(message):
    raise ServiceError("SESSION_CONFLICT", message, 409)


def safe_content(text):
    return bool(text.strip()) and not SENSITIVE.search(unicodedata.normalize("NFKC", text))


def library_version(db, version_id):
    row = db.execute("""SELECT v.* FROM story_versions v JOIN storybooks b ON b.id=v.storybook_id
                        WHERE v.id=? AND b.source_type='LIBRARY'""", (version_id,)).fetchone()
    if row is None:
        raise ServiceError("RESOURCE_NOT_FOUND", "资料库版本不存在", 404)
    return row


def validate_story_paths(pages, branches):
    edges = {page["pageNumber"]: set() for page in pages}
    if 1 not in edges or len(edges) != len(pages):
        invalid("页面须有唯一页码，且包含第 1 页")
    for page in pages:
        if page["defaultNextPageNumber"] is not None:
            edges[page["pageNumber"]].add(page["defaultNextPageNumber"])
    labels = set()
    for branch in branches:
        source, target, label = branch["fromPageNumber"], branch["nextPageNumber"], branch["choiceLabel"]
        if source not in edges or (source, label) in labels:
            invalid("分支起点无效或选项重复")
        labels.add((source, label))
        edges[source].add(target)
    indegree = dict.fromkeys(edges, 0)
    for targets in edges.values():
        for target in targets:
            if target not in edges:
                invalid("翻页或分支目标不存在")
            indegree[target] += 1
    if [page for page, count in indegree.items() if count == 0] != [1]:
        invalid("绘本含不可达页面或环路")
    queue, visited = deque([1]), 0
    while queue:
        visited += 1
        for target in edges[queue.popleft()]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(edges):
        invalid("绘本含不可达页面或环路")


@router.post("/storybooks", status_code=201)
def create_story(payload: LibraryDraft, request: Request):
    def create(db, admin_id):
        validate_story_paths([p.model_dump() for p in payload.pages], [b.model_dump() for b in payload.branches])
        now = int(time.time() * 1000)
        book = payload.storybookId
        if book is None:
            book = db.execute("INSERT INTO storybooks(source_type,created_at) VALUES('LIBRARY',?)", (now,)).lastrowid
        elif not db.execute("SELECT id FROM storybooks WHERE id=? AND source_type='LIBRARY'", (book,)).fetchone():
            raise ServiceError("RESOURCE_NOT_FOUND", "资料库绘本不存在", 404)
        number, = db.execute("SELECT coalesce(max(version_no),0)+1 FROM story_versions WHERE storybook_id=?", (book,)).fetchone()
        low, high = AGES[payload.ageBand]
        version = db.execute("""INSERT INTO story_versions(storybook_id,version_no,title,topics_json,age_min,age_max,
            license_note,status,created_at,branching) VALUES(?,?,?,?,?,?,?,'DRAFT',?,?)""",
            (book, number, payload.title, json.dumps(payload.topics, ensure_ascii=False), low, high, payload.licenseNote,
             now, int(bool(payload.branches)))).lastrowid
        db.executemany("""INSERT INTO story_pages(version_id,page_no,body_text,image_ref,default_next_page_number)
                       VALUES(?,?,?,?,?)""", [(version, p.pageNumber, p.text, p.imageRef, p.defaultNextPageNumber) for p in payload.pages])
        db.executemany("""INSERT INTO story_branches(version_id,from_page_no,next_page_no,choice_label,review_status)
                       VALUES(?,?,?,?,'PENDING')""", [(version, b.fromPageNumber, b.nextPageNumber, b.choiceLabel) for b in payload.branches])
        return admin_story_object(db, library_version(db, version), page_details=True)
    return admin_request(request, create, payload=payload, created=True)


@router.patch("/story-versions/{version_id}")
def set_story_status(payload: StoryStatus, request: Request, version_id: int = Path(ge=1, le=MAX_ID)):
    def update(db, admin_id):
        row = library_version(db, version_id)
        if row["status"] == payload.status:
            return admin_story_object(db, row, page_details=True)
        if payload.status == "WITHDRAWN":
            if row["status"] not in ("PUBLISHED", "RETIRED"):
                conflict("当前版本尚未发布")
            db.execute("UPDATE story_versions SET status='WITHDRAWN' WHERE id=?", (version_id,))
        else:
            if row["status"] not in ("DRAFT", "PENDING_REVIEW"):
                conflict("当前版本不能发布")
            data = admin_story_object(db, row, page_details=True)
            validate_story_paths(data["pages"], data["branches"])
            if not row["license_note"].strip() or not all(safe_content(text) for text in
                    [row["title"], *data["topics"], *(p["text"] for p in data["pages"]), *(b["choiceLabel"] for b in data["branches"])]):
                invalid("标题、正文、分支或许可依据未通过审核")
            if any(b["reviewStatus"] == "REJECTED" for b in data["branches"]):
                invalid("绘本含未通过审核的分支")
            if row["source_ref"] == SEED_COVER_SOURCE_REF:
                if not cover_ref_from_manifest(version_id, pin_version_media(db, row, source_type="LIBRARY")):
                    invalid("绘本封面不可用")
            images = json.loads(freeze_approval_page_media(db, row))
            if any(not images.get(str(p["pageNumber"])) for p in data["pages"]):
                invalid("每页须提供可读取的已授权图片")
            db.execute("UPDATE story_versions SET status='RETIRED' WHERE storybook_id=? AND status='PUBLISHED'", (row["storybook_id"],))
            db.execute("UPDATE story_versions SET status='PUBLISHED',reviewed_by_id=?,reviewed_at=? WHERE id=?",
                       (admin_id, int(time.time() * 1000), version_id))
            db.execute("UPDATE story_branches SET review_status='APPROVED' WHERE version_id=? AND review_status='PENDING'", (version_id,))
        return admin_story_object(db, library_version(db, version_id), page_details=True)
    return admin_request(request, update, payload=payload)


def knowledge_data(row):
    return {"knowledgeItemId": row["id"], "itemKey": row["item_key"], "versionNumber": row["version_no"],
            "title": row["title"], "sourceTitle": row["source_title"], "sourceReference": row["source_ref"],
            "locator": row["locator"], "ageBand": row["age_band"], "topicTags": json.loads(row["topics_json"]),
            "taskTypes": json.loads(row["task_types_json"]), "content": row["content"], "status": row["status"]}


def insert_knowledge(db, item, number):
    now = int(time.time() * 1000)
    approved = item["status"] == "APPROVED"
    if approved:
        if not all(safe_content(t) for t in [item["title"], item["content"], *item["topicTags"]]):
            invalid("知识内容未通过审核")
        db.execute("UPDATE knowledge_items SET status='RETIRED' WHERE item_key=? AND status='APPROVED'", (item["itemKey"],))
    key = db.execute("""INSERT INTO knowledge_items(version_no,item_key,title,source_title,source_ref,locator,age_band,
        topics_json,task_types_json,content,status,reviewed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (number, item["itemKey"], item["title"], item["sourceTitle"], item["sourceReference"], item["locator"],
         item["ageBand"], json.dumps(item["topicTags"], ensure_ascii=False), json.dumps(item["taskTypes"]), item["content"],
         item["status"], now if approved else None, now)).lastrowid
    return knowledge_data(db.execute("SELECT * FROM knowledge_items WHERE id=?", (key,)).fetchone())


@router.get("/knowledge-items")
def list_knowledge(request: Request, page: int = Query(1, ge=1, le=MAX_ID),
                   page_size: int = Query(20, ge=1, le=50, alias="pageSize"), status: Status | None = None,
                   query: str = Query("", max_length=200)):
    def read(db, admin_id):
        rows = db.execute("""SELECT * FROM knowledge_items WHERE (? IS NULL OR status=?)
            AND (instr(title,?) OR instr(item_key,?) OR instr(content,?))
            ORDER BY item_key,version_no DESC LIMIT ? OFFSET ?""",
            (status, status, query, query, query, page_size, min((page-1)*page_size, 2**63-1)))
        return {"items": [knowledge_data(row) for row in rows], "page": page, "pageSize": page_size}
    return admin_request(request, read)


@router.post("/knowledge-items", status_code=201)
def create_knowledge(payload: KnowledgeCreate, request: Request):
    def create(db, admin_id):
        if db.execute("SELECT 1 FROM knowledge_items WHERE item_key=?", (payload.itemKey,)).fetchone():
            conflict("知识标识已存在，请修订原条目")
        return insert_knowledge(db, payload.model_dump(), 1)
    return admin_request(request, create, payload=payload, created=True)


@router.patch("/knowledge-items/{knowledge_id}")
def update_knowledge(payload: KnowledgePatch, request: Request, knowledge_id: int = Path(ge=1, le=MAX_ID)):
    def update(db, admin_id):
        source = db.execute("SELECT * FROM knowledge_items WHERE id=?", (knowledge_id,)).fetchone()
        if source is None:
            raise ServiceError("RESOURCE_NOT_FOUND", "知识版本不存在", 404)
        item = knowledge_data(source)
        if source["status"] == "RETIRED" and payload.status not in ("DRAFT", "APPROVED"):
            conflict("已退休版本须明确建立草稿或重新审核")
        if payload.status == "RETIRED":
            retired = db.execute("UPDATE knowledge_items SET status='RETIRED' WHERE id=? AND status='APPROVED'", (knowledge_id,))
            if retired.rowcount != 1:
                conflict("仅可停用当前批准版本")
        item.update(payload.model_dump(exclude_unset=True))
        number, = db.execute("SELECT max(version_no)+1 FROM knowledge_items WHERE item_key=?", (item["itemKey"],)).fetchone()
        return insert_knowledge(db, item, number)
    return admin_request(request, update, payload=payload)
