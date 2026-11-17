"""Guardian-owned child profile endpoints."""

import json
import sqlite3
import time
from contextlib import closing
from typing import Literal

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.v1.auth import SESSION_COOKIE, connect, error, token_digest, valid_csrf

router = APIRouter(prefix="/api/v1/child-profiles")
AgeBand = Literal["AGE_3_4", "AGE_4_5", "AGE_5_6"]


class ProfileFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nickname: str | None = None
    age_band: AgeBand | None = Field(default=None, alias="ageBand")
    interests: list[str] | None = Field(default=None, max_length=10)
    preferred_minutes: int | None = Field(default=None, ge=1, le=30, strict=True, alias="preferredMinutes")

    @field_validator("nickname")
    @classmethod
    def validate_nickname(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not 1 <= len(value) <= 32:
            raise ValueError("昵称长度应为 1 至 32 字符")
        return value

    @field_validator("interests")
    @classmethod
    def validate_interests(cls, value):
        if value is None:
            return None
        cleaned = [item.strip() for item in value]
        if any(not 1 <= len(item) <= 32 for item in cleaned):
            raise ValueError("兴趣长度应为 1 至 32 字符")
        return cleaned


class ProfileCreate(ProfileFields):
    age_band: AgeBand = Field(alias="ageBand")
    preferred_minutes: int = Field(ge=1, le=30, strict=True, alias="preferredMinutes")

    @model_validator(mode="after")
    def check_interests(self):
        if "interests" in self.model_fields_set and self.interests is None:
            raise ValueError("兴趣必须为数组")
        return self


class ProfileUpdate(ProfileFields):
    @model_validator(mode="after")
    def check_fields(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个字段")
        for name in ("age_band", "interests", "preferred_minutes"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} 不可为空")
        return self


def guardian(db, request: Request, *, write: bool):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None, error(request, 401, "AUTH_REQUIRED", "请先登录")
    row = db.execute(
        """SELECT a.id, a.role FROM auth_sessions s JOIN accounts a ON a.id = s.account_id
           WHERE s.token_digest = ? AND s.revoked_at IS NULL AND s.expires_at > ?""",
        (token_digest(token), int(time.time() * 1000)),
    ).fetchone()
    if not row:
        return None, error(request, 401, "AUTH_REQUIRED", "请先登录")
    if row["role"] != "GUARDIAN":
        return None, error(request, 403, "FORBIDDEN", "当前账号不能操作儿童档案")
    if write and not valid_csrf(request, token):
        return None, error(request, 403, "CSRF_INVALID", "请求校验失败")
    return row["id"], None


def serialize(row):
    return {
        "childProfileId": row["id"], "nickname": row["nickname"], "ageBand": row["age_band"],
        "interests": json.loads(row["interests_json"]), "preferredMinutes": row["preferred_minutes"],
    }


@router.get("")
def list_profiles(request: Request):
    try:
        with closing(connect()) as db:
            owner, failure = guardian(db, request, write=False)
            if failure:
                return failure
            rows = db.execute("SELECT * FROM child_profiles WHERE guardian_id = ? ORDER BY id", (owner,))
            return {"data": {"items": [serialize(row) for row in rows]}, "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)


@router.post("", status_code=201)
def create_profile(payload: ProfileCreate, request: Request):
    try:
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            owner, failure = guardian(db, request, write=True)
            if failure:
                return failure
            now = int(time.time() * 1000)
            cursor = db.execute(
                """INSERT INTO child_profiles
                   (guardian_id, nickname, age_band, interests_json, preferred_minutes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (owner, payload.nickname, payload.age_band, json.dumps(payload.interests or [], ensure_ascii=False),
                 payload.preferred_minutes, now, now),
            )
            row = db.execute("SELECT * FROM child_profiles WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return {"data": serialize(row), "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")


@router.patch("/{profile_id}")
def update_profile(payload: ProfileUpdate, request: Request,
                   profile_id: int = Path(ge=1, le=9007199254740991)):
    try:
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            owner, failure = guardian(db, request, write=True)
            if failure:
                return failure
            row = db.execute(
                "SELECT * FROM child_profiles WHERE id = ? AND guardian_id = ?", (profile_id, owner)
            ).fetchone()
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "档案不可用")
            fields = payload.model_dump(exclude_unset=True, by_alias=True)
            current = serialize(row)
            current.update(fields)
            db.execute(
                """UPDATE child_profiles SET nickname = ?, age_band = ?, interests_json = ?,
                   preferred_minutes = ?, updated_at = ? WHERE id = ? AND guardian_id = ?""",
                (current["nickname"], current["ageBand"], json.dumps(current["interests"], ensure_ascii=False),
                 current["preferredMinutes"], int(time.time() * 1000), profile_id, owner),
            )
            updated = db.execute("SELECT * FROM child_profiles WHERE id = ?", (profile_id,)).fetchone()
            return {"data": serialize(updated), "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
