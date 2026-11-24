"""Account registration and cookie-based guardian sessions."""

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.connection import connect
from app.services.reading_reports import (
    ensure_startup_recovered, queue_deferred_reports, settle_grant_sessions, unsettled_sessions,
)

router = APIRouter(prefix="/api/v1/auth")
SESSION_COOKIE = "zhiya_session"
CSRF_COOKIE = "zhiya_csrf"
SESSION_SECONDS = 24 * 60 * 60
LOGIN_WINDOW = 15 * 60
LOGIN_LIMIT = 10
_login_attempts = defaultdict(deque)
_login_lock = threading.Lock()


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("account", mode="before")
    @classmethod
    def trim_account(cls, value):
        return value.strip() if isinstance(value, str) else value


def error(request: Request, status: int, code: str, message: str, *, retryable: bool = False, headers=None):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "retryable": retryable},
                 "requestId": request.state.request_id},
        headers=headers,
    )


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected_hex = stored.split("$")
        if (algorithm, n, r, p) != ("scrypt", "16384", "8", "1"):
            return False
        return hmac.compare_digest(password_hash(password, bytes.fromhex(salt_hex)), stored)
    except (ValueError, TypeError):
        return False


def csrf_for(token: str) -> str:
    return hmac.new(token.encode("utf-8"), b"zhiya-csrf-v1", hashlib.sha256).hexdigest()


def valid_csrf(request: Request, token: str) -> bool:
    supplied = request.headers.get("X-CSRF-Token", "")
    return supplied.isascii() and hmac.compare_digest(supplied, csrf_for(token))


@dataclass(frozen=True)
class Principal:
    guardian_id: int
    role: Literal["GUARDIAN", "CHILD", "ADMIN"]
    auth_session_id: int | None = None
    grant_id: int | None = None
    profile_id: int | None = None
    grant_expires_at: int | None = None


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def set_session_cookies(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, secure=True,
                        httponly=True, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf_for(token), max_age=max_age, secure=True,
                        httponly=False, samesite="lax", path="/")


def _identify(db, request: Request, *, csrf: bool, now: int, allow_admin: bool = False):
    """An expired child grant can still identify its owner for settlement and password unlock."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None, error(request, 401, "AUTH_REQUIRED", "请先登录")
    digest = token_digest(token)
    guardian_row = db.execute(
        """SELECT s.id AS session_id, a.id AS account_id, a.role
           FROM auth_sessions s JOIN accounts a ON a.id = s.account_id
           WHERE s.token_digest = ? AND s.revoked_at IS NULL AND s.expires_at > ?""",
        (digest, now),
    ).fetchone()
    if guardian_row:
        if guardian_row["role"] != "GUARDIAN" and not (allow_admin and guardian_row["role"] == "ADMIN"):
            return None, error(request, 403, "FORBIDDEN", "当前账号不能执行此操作")
        if csrf and not valid_csrf(request, token):
            return None, error(request, 403, "CSRF_INVALID", "请求校验失败")
        return Principal(guardian_id=guardian_row["account_id"], role=guardian_row["role"],
                         auth_session_id=guardian_row["session_id"]), None
    child_row = db.execute(
        """SELECT g.id AS grant_id, g.profile_id, g.expires_at, p.guardian_id
           FROM child_grants g
           JOIN child_profiles p ON p.id = g.profile_id
           JOIN accounts a ON a.id = p.guardian_id
           WHERE g.token_digest = ? AND g.revoked_at IS NULL AND g.issued_at <= ?
             AND a.role = 'GUARDIAN'""",
        (digest, now),
    ).fetchone()
    if child_row:
        if csrf and not valid_csrf(request, token):
            return None, error(request, 403, "CSRF_INVALID", "请求校验失败")
        return Principal(guardian_id=child_row["guardian_id"], role="CHILD",
                         grant_id=child_row["grant_id"], profile_id=child_row["profile_id"],
                         grant_expires_at=child_row["expires_at"]), None
    return None, error(request, 401, "AUTH_REQUIRED", "请先登录")


@contextmanager
def authorized_db(request: Request, *, write: bool = False, csrf: bool = False,
                  guardian_only: bool = False, allow_expired_child: bool = False,
                  settle_guardian: bool = True, allow_admin: bool = False, cache_write: bool = False):
    """Keep authorization and business data in one snapshot; publish report jobs after commit."""
    pending = []
    with closing(connect()) as db, db:
        db.execute("BEGIN")
        now = int(time.time() * 1000)
        principal, failure = _identify(db, request, csrf=write or csrf, now=now, allow_admin=allow_admin)
        if principal and (
            write or cache_write or (
                principal.role != "ADMIN"
                and unsettled_sessions(db, principal.guardian_id, principal.grant_id, now).fetchone()
            )
        ):
            # Release the read snapshot before recovery acquires its own write lock.
            db.rollback()
            if not ensure_startup_recovered():
                raise sqlite3.OperationalError("report recovery pending")
            db.execute("BEGIN IMMEDIATE")
            now = int(time.time() * 1000)
            principal, failure = _identify(db, request, csrf=write or csrf, now=now, allow_admin=allow_admin)
            if principal and principal.role != "ADMIN" and (settle_guardian or principal.role == "CHILD"):
                pending = settle_grant_sessions(db, principal.guardian_id, principal.grant_id, now)
            if not write and not cache_write:
                # A bookshelf or history read can be long. Only settlement needs the write lock.
                db.commit()
                queue_deferred_reports(request, pending)
                pending = []
                db.execute("BEGIN")
                now = int(time.time() * 1000)
                principal, failure = _identify(db, request, csrf=csrf, now=now)
        if principal and principal.role == "CHILD":
            if principal.grant_expires_at <= now and not allow_expired_child:
                failure = error(request, 401, "AUTH_REQUIRED", "请先登录")
            elif guardian_only:
                failure = error(request, 403, "FORBIDDEN", "当前为儿童模式，仅限家长操作")
        yield db, principal if failure is None else None, failure
    queue_deferred_reports(request, pending)


def revoke_grant_and_interrupt(db, grant_id: int, now: int) -> None:
    db.execute("UPDATE child_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now, grant_id))
    db.execute(
        """UPDATE reading_sessions SET status = 'INTERRUPTED', end_reason = 'GRANT_REVOKED',
           ended_at = ?, revision = revision + 1
           WHERE grant_id = ? AND status IN ('CREATED', 'IN_PROGRESS')""",
        (now, grant_id),
    )


def allowed_login(client: str) -> bool:
    now = time.monotonic()
    with _login_lock:
        for key in list(_login_attempts):
            attempts = _login_attempts[key]
            while attempts and attempts[0] <= now - LOGIN_WINDOW:
                attempts.popleft()
            if not attempts:
                del _login_attempts[key]
        attempts = _login_attempts[client]
        if len(attempts) >= LOGIN_LIMIT:
            return False
        attempts.append(now)
        return True


@router.post("/registrations", status_code=201)
def register(payload: Credentials, request: Request):
    try:
        with closing(connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO accounts (account, password_hash, role, created_at) VALUES (?, ?, 'GUARDIAN', ?)",
                (payload.account, password_hash(payload.password), int(time.time() * 1000)),
            )
            account_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return error(request, 409, "ACCOUNT_EXISTS", "账号已存在")
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
    return {"data": {"accountId": account_id}, "requestId": request.state.request_id}


@router.post("/sessions", status_code=201)
def login(payload: Credentials, request: Request):
    client = request.client.host if request.client else "unknown"
    try:
        with closing(connect()) as db, db:
            token = request.cookies.get(SESSION_COOKIE)
            if token and db.execute(
                "SELECT 1 FROM child_grants WHERE token_digest = ? AND revoked_at IS NULL",
                (token_digest(token),),
            ).fetchone():
                return error(request, 403, "FORBIDDEN", "请先通过家长解锁退出儿童模式")
            if not allowed_login(client):
                return error(request, 429, "RATE_LIMITED", "尝试次数过多，请稍后再试", retryable=True,
                             headers={"Retry-After": str(LOGIN_WINDOW)})
            account = db.execute(
                "SELECT id, password_hash FROM accounts WHERE account = ?", (payload.account,)
            ).fetchone()
            # Keep password verification work similar when the account is absent.
            stored = account["password_hash"] if account else password_hash("dummy-password")
            if not password_matches(payload.password, stored) or not account:
                return error(request, 401, "INVALID_CREDENTIALS", "账号或密码错误")
            token = secrets.token_urlsafe(32)
            now = int(time.time() * 1000)
            db.execute(
                "INSERT INTO auth_sessions (account_id, token_digest, expires_at) VALUES (?, ?, ?)",
                (account["id"], token_digest(token), now + SESSION_SECONDS * 1000),
            )
            account_id = account["id"]
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
    response = JSONResponse(status_code=201, content={
        "data": {"accountId": account_id}, "requestId": request.state.request_id
    })
    set_session_cookies(response, token, SESSION_SECONDS)
    return response


@router.get("/sessions/current")
def current_session(request: Request):
    try:
        with closing(connect()) as db:
            principal, failure = _identify(db, request, csrf=False, now=int(time.time() * 1000), allow_admin=True)
            if failure:
                return failure
            data = {"role": principal.role}
            if principal.role != "CHILD":
                data["accountId"] = principal.guardian_id
            return JSONResponse(content={"data": data, "requestId": request.state.request_id},
                                headers={"Cache-Control": "no-store"})
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "暂时无法确认登录身份", retryable=True)


@router.delete("/sessions/current", status_code=204)
def logout(request: Request):
    try:
        with authorized_db(request, write=True, allow_expired_child=True, allow_admin=True) as (db, principal, failure):
            if failure:
                return failure
            now = int(time.time() * 1000)
            if principal.auth_session_id is not None:
                db.execute("UPDATE auth_sessions SET revoked_at = ? WHERE id = ?",
                           (now, principal.auth_session_id))
            if principal.role == "GUARDIAN":
                grant_ids = [r[0] for r in db.execute(
                    """SELECT g.id FROM child_grants g
                       JOIN child_profiles p ON p.id = g.profile_id
                       WHERE p.guardian_id = ? AND g.revoked_at IS NULL""",
                    (principal.guardian_id,),
                )]
                for gid in grant_ids:
                    revoke_grant_and_interrupt(db, gid, now)
            elif principal.role == "CHILD":
                revoke_grant_and_interrupt(db, principal.grant_id, now)
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    response.delete_cookie(CSRF_COOKIE, secure=True, samesite="lax", path="/")
    return response


@router.post("/parent-unlocks")
def parent_unlock(payload: Credentials, request: Request):
    """API-021: re-authenticate guardian to exit child mode, revoke grant and issue a fresh session."""
    client = request.client.host if request.client else "unknown"
    try:
        with authorized_db(request, write=True, allow_expired_child=True) as (db, principal, failure):
            if failure:
                return failure
            if principal.role != "CHILD":
                return error(request, 403, "FORBIDDEN", "当前会话不在儿童模式")
            if not allowed_login(client):
                return error(request, 429, "RATE_LIMITED", "尝试次数过多，请稍后再试", retryable=True,
                             headers={"Retry-After": str(LOGIN_WINDOW)})
            account = db.execute(
                "SELECT id, password_hash, role FROM accounts WHERE account = ?", (payload.account,)
            ).fetchone()
            stored = account["password_hash"] if account else password_hash("dummy-password")
            if (not password_matches(payload.password, stored) or not account
                    or account["role"] != "GUARDIAN" or account["id"] != principal.guardian_id):
                return error(request, 401, "INVALID_CREDENTIALS", "账号或密码错误")
            now = int(time.time() * 1000)
            revoke_grant_and_interrupt(db, principal.grant_id, now)
            new_token = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO auth_sessions (account_id, token_digest, expires_at) VALUES (?, ?, ?)",
                (account["id"], token_digest(new_token), now + SESSION_SECONDS * 1000),
            )
            account_id = account["id"]
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
    response = JSONResponse(status_code=200, content={
        "data": {"accountId": account_id}, "requestId": request.state.request_id
    })
    set_session_cookies(response, new_token, SESSION_SECONDS)
    return response
