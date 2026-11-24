"""在原始 P6 验收数据库中定向撤销旧归档里的测试会话。"""

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from contextlib import closing
from http.cookies import SimpleCookie
from pathlib import Path


def revoke(db_path: Path, report: dict) -> int:
    hashes = set()
    for row in report["results"]:
        if row["case_id"] != "TC-P6-AUTH-004":
            continue
        header = row.get("evidence", {}).get("cookieFlags", {}).get("session")
        if isinstance(header, str):
            cookie = SimpleCookie(header).get("zhiya_session")
            if cookie and cookie.value:
                hashes.add(hashlib.sha256(cookie.value.encode()).hexdigest())
    if len(hashes) != 1:
        raise ValueError("需要含一条原始登录会话的旧测试报告")
    now = time.time_ns() // 1_000_000
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=rw", uri=True)) as db, db:
        return db.execute(
            "UPDATE auth_sessions SET revoked_at=? WHERE token_digest=? AND revoked_at IS NULL AND expires_at>?",
            (now, hashes.pop(), now),
        ).rowcount


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="生成该归档的原测试数据库")
    args = parser.parse_args()
    updated = revoke(args.db, json.load(sys.stdin))
    print(f"已撤销 {updated} 条有效测试会话")
