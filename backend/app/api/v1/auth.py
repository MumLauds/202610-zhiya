"""Account registration and cookie-based guardian sessions."""

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import closing

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import database_path

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


def connect():
    db = sqlite3.connect(f"{database_path().as_uri()}?mode=rw", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


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
    if not allowed_login(client):
        return error(request, 429, "RATE_LIMITED", "尝试次数过多，请稍后再试", retryable=True,
                     headers={"Retry-After": str(LOGIN_WINDOW)})
    try:
        with closing(connect()) as db, db:
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
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, secure=True,
                        httponly=True, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf_for(token), max_age=SESSION_SECONDS, secure=True,
                        httponly=False, samesite="lax", path="/")
    return response


@router.delete("/sessions/current", status_code=204)
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return error(request, 401, "AUTH_REQUIRED", "请先登录")
    if not valid_csrf(request, token):
        return error(request, 403, "CSRF_INVALID", "请求校验失败")
    now = int(time.time() * 1000)
    try:
        with closing(connect()) as db, db:
            session = db.execute(
                "SELECT id FROM auth_sessions WHERE token_digest = ? AND revoked_at IS NULL AND expires_at > ?",
                (token_digest(token), now),
            ).fetchone()
            if not session:
                return error(request, 401, "AUTH_REQUIRED", "请先登录")
            db.execute("UPDATE auth_sessions SET revoked_at = ? WHERE id = ?", (now, session["id"]))
            db.execute(
                "UPDATE child_grants SET revoked_at = ? WHERE issued_by_session_id = ? AND revoked_at IS NULL",
                (now, session["id"]),
            )
    except sqlite3.Error:
        return error(request, 503, "DEPENDENCY_UNAVAILABLE", "服务暂不可用")
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    response.delete_cookie(CSRF_COOKIE, secure=True, samesite="lax", path="/")
    return response
