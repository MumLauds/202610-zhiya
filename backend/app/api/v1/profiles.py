"""Guardian-owned child profile endpoints."""

import json
import sqlite3
import time
from typing import Literal

from fastapi import APIRouter, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.v1.auth import authorized_db, error
from app.services.cleanup import delete_session_rows, purge_voice_media

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


def serialize(row):
    return {
        "childProfileId": row["id"], "nickname": row["nickname"], "ageBand": row["age_band"],
        "interests": json.loads(row["interests_json"]), "preferredMinutes": row["preferred_minutes"],
    }


@router.get("")
def list_profiles(request: Request):
    try:
        with authorized_db(request, guardian_only=True) as (db, principal, failure):
            if failure:
                return failure
            rows = db.execute("SELECT * FROM child_profiles WHERE guardian_id = ? ORDER BY id", (principal.guardian_id,))
            return {"data": {"items": [serialize(row) for row in rows]}, "requestId": request.state.request_id}
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用", retryable=True)


@router.post("", status_code=201)
def create_profile(payload: ProfileCreate, request: Request):
    try:
        with authorized_db(request, write=True, guardian_only=True) as (db, principal, failure):
            if failure:
                return failure
            owner = principal.guardian_id
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
        with authorized_db(request, write=True, guardian_only=True) as (db, principal, failure):
            if failure:
                return failure
            owner = principal.guardian_id
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


@router.delete("/{profile_id}", status_code=204)
def delete_profile(request: Request, profile_id: int = Path(ge=1, le=9007199254740991)):
    """API-007: delete a guardian-owned child profile and all associated sessions, grants and derived data."""
    session_ids: list[int] = []
    try:
        with authorized_db(request, write=True, guardian_only=True, settle_guardian=False) as (db, principal, failure):
            if failure:
                return failure
            owner = principal.guardian_id
            row = db.execute(
                "SELECT id FROM child_profiles WHERE id = ? AND guardian_id = ?", (profile_id, owner)
            ).fetchone()
            if not row:
                return error(request, 404, "RESOURCE_NOT_FOUND", "档案不可用")
            session_ids = [
                r[0] for r in db.execute("SELECT id FROM reading_sessions WHERE profile_id = ?", (profile_id,))
            ]
            for sid in session_ids:
                delete_session_rows(db, sid)
            db.execute(
                "DELETE FROM child_grant_books WHERE grant_id IN (SELECT id FROM child_grants WHERE profile_id = ?)",
                (profile_id,),
            )
            db.execute("DELETE FROM child_grants WHERE profile_id = ?", (profile_id,))
            db.execute("DELETE FROM child_profiles WHERE id = ? AND guardian_id = ?", (profile_id, owner))
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
    purge_voice_media(request.app.state, session_ids)
    return Response(status_code=204)
