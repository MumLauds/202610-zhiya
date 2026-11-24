"""P6 现场演示驱动脚本：按演示脚本顺序走真实接口，并打印可讲解的结果。

用法：
    python qa/p6/demo_flow.py --base-url http://127.0.0.1:8765 --allow-http
    python qa/p6/demo_flow.py --base-url https://... --account p6demo --password '***' --pause

默认不注册新账号时使用 --account/--password；未提供时自动注册演示账号。
脚本只访问 HTTP 接口，不导入后端代码，不读取 .env。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ApiSession, Blocked, error_code, loopback_host, response_json, select_story, unique_suffix  # noqa: E402


def step(number: int, title: str) -> None:
    print(f"\n{'=' * 64}\n步骤 {number}：{title}\n{'=' * 64}")


def pause(enabled: bool, prompt: str = "按回车继续…") -> None:
    if enabled:
        input(prompt)


def show(response, *, keys: tuple[str, ...] = ()) -> None:
    body = response_json(response)
    print(f"HTTP {response.status_code}")
    if isinstance(body, dict):
        if keys:
            body = {key: body.get(key) for key in keys}
        print(json.dumps(body, ensure_ascii=False, indent=2)[:2000])
    else:
        print(response.text[:500])


def expect(response, status: int, code: str | None = None) -> dict:
    body = response_json(response)
    if response.status_code != status:
        raise SystemExit(f"演示中断：期望 HTTP {status}，实际 {response.status_code}，"
                         f"body={json.dumps(body, ensure_ascii=False)[:400]}")
    if code and error_code(response) != code:
        raise SystemExit(f"演示中断：期望错误码 {code}，实际 {error_code(response)}")
    return body or {}


def demo_voice(client: ApiSession, profile_id: int, version_id: int, *, enabled: bool) -> None:
    if not enabled:
        print("本轮跳过合成，使用归档报告讲解未配置时的 503 与文字回退。")
        return
    voice_session = expect(client.post("/api/v1/reading-sessions", csrf=True, json={
        "childProfileId": profile_id,
        "storyVersionId": version_id,
        "mode": "PARENT_CHILD",
    }), 201)["data"]["readingSessionId"]
    expect(client.post(f"/api/v1/reading-sessions/{voice_session}/events", csrf=True, json={
        "eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0,
        "clientEventId": f"demo-voice-{uuid4().hex[:8]}",
    }), 201)
    synthesis_body = {"readingSessionId": voice_session, "pageNumber": 1, "revision": 1}
    voice = client.post("/api/v1/voice/syntheses", csrf=True, json=synthesis_body)
    print(f"真实合成：HTTP {voice.status_code}")
    show(voice)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--account", help="已有演示账号；不传则自动注册")
    parser.add_argument("--password", default="Zhiya-demo-2026")
    parser.add_argument("--story-version-id", type=int)
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--insecure-tls", action="store_true")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--pause", action="store_true", help="每一步暂停，适合现场演示")
    parser.add_argument("--voice", action="store_true", help="尝试真实 TTS（会产生费用）")
    args = parser.parse_args(argv)
    if args.insecure_tls and not loopback_host(urlsplit(args.base_url).hostname or ""):
        parser.error("登录演示需要校验 HTTPS 证书")

    client = ApiSession(args.base_url, timeout=args.timeout, verify=not args.insecure_tls,
                        allow_insecure_cookies=args.allow_http)
    account = args.account or f"p6demo{unique_suffix()}"
    password = args.password

    step(1, "健康检查")
    health = client.get("/api/v1/health")
    expect(health, 200)
    show(health)
    pause(args.pause)

    step(2, "登录（必要时先注册）")
    login = client.post("/api/v1/auth/sessions", json={"account": account, "password": password})
    if login.status_code == 401 and not args.account:
        register = client.post("/api/v1/auth/registrations", json={"account": account, "password": password})
        expect(register, 201)
        print(f"已注册演示账号 {account}")
        login = client.post("/api/v1/auth/sessions", json={"account": account, "password": password})
    expect(login, 201)
    print(f"登录成功：{account}；会话 Cookie 已建立，CSRF 令牌已取得")
    pause(args.pause)

    step(3, "幼儿档案")
    profiles = expect(client.get("/api/v1/child-profiles"), 200)["data"]["items"]
    if profiles:
        profile = profiles[0]
        print(f"复用已有档案：{profile}")
    else:
        profile = expect(client.post("/api/v1/child-profiles", csrf=True, json={
            "nickname": "小芽", "ageBand": "AGE_4_5",
            "interests": ["动物", "友谊"], "preferredMinutes": 10,
        }), 201)["data"]
        print(f"已创建档案：{profile}")
    pause(args.pause)

    step(4, "绘本馆")
    books = expect(client.get("/api/v1/storybooks", params={"pageSize": 50}), 200)["data"]["items"]
    print(f"可用绘本 {len(books)} 本")
    try:
        book = select_story(client, books, args.story_version_id)
    except (Blocked, AssertionError) as exc:
        raise SystemExit(f"演示中断：{exc}") from None
    print(f"选择：《{book['title']}》，版本 {book['storyVersionId']}，{len(book['pages'])} 页")
    print(f"第一页：{book['pages'][0]['text'][:80]}")
    pause(args.pause)

    step(5, "创建共读会话并开始")
    session = expect(client.post("/api/v1/reading-sessions", csrf=True, json={
        "childProfileId": profile["childProfileId"],
        "storyVersionId": book["storyVersionId"],
        "mode": "PARENT_CHILD",
    }), 201)["data"]
    session_id = session["readingSessionId"]
    print(f"会话 {session_id}：status={session['status']}, revision={session['revision']}")
    event = {"eventType": "STARTED", "pageNumber": 1, "expectedRevision": 0,
             "clientEventId": f"demo-start-{uuid4().hex[:8]}"}
    started = expect(client.post(f"/api/v1/reading-sessions/{session_id}/events", csrf=True, json=event), 201)
    print(f"开始阅读：revision={started['data']['revision']}")
    pause(args.pause)

    step(6, "幂等重放：同一个 clientEventId 再提交一次")
    replay = expect(client.post(f"/api/v1/reading-sessions/{session_id}/events", csrf=True, json=event), 200)
    print(f"重放返回同一结果：interactionEventId={replay['data']['interactionEventId']}, "
          f"revision={replay['data']['revision']}（没有重复写入）")
    pause(args.pause)

    revision = started["data"]["revision"]
    next_page = book["pages"][0].get("defaultNextPageNumber")
    if next_page:
        step(7, "翻页")
        changed = expect(client.post(f"/api/v1/reading-sessions/{session_id}/events", csrf=True, json={
            "eventType": "PAGE_CHANGED", "pageNumber": next_page,
            "expectedRevision": revision, "clientEventId": f"demo-page-{uuid4().hex[:8]}",
        }), 201)
        revision = changed["data"]["revision"]
        print(f"翻到第 {next_page} 页，revision={revision}")
        pause(args.pause)

    step(8, "结束阅读并查看小结")
    finished = expect(client.post(f"/api/v1/reading-sessions/{session_id}/completions", csrf=True, json={
        "reason": "USER_FINISH", "expectedRevision": revision,
        "clientEventId": f"demo-finish-{uuid4().hex[:8]}",
    }), 200)["data"]
    print(f"会话状态：{finished['session']['status']}")
    report = finished.get("report") or {}
    print(f"小结：status={report.get('status')}, summary={report.get('summary')!r}, "
          f"activities={len(report.get('activities') or [])}")
    print("说明：未配置文本模型时小结为 FAILED/PENDING，保留事实摘要；activities 仍为 0，"
          "这是当前 FR-10 的已知边界。")
    pause(args.pause)

    step(9, "儿童模式：另开一个家长会话，进入 CHILD_SOLO")
    guardian = ApiSession(args.base_url, timeout=args.timeout, verify=not args.insecure_tls,
                          allow_insecure_cookies=args.allow_http)
    expect(guardian.post("/api/v1/auth/sessions", json={"account": account, "password": password}), 201)
    child_session = expect(guardian.post("/api/v1/reading-sessions", csrf=True, json={
        "childProfileId": profile["childProfileId"],
        "storyVersionId": book["storyVersionId"],
        "mode": "PARENT_CHILD",
    }), 201)["data"]["readingSessionId"]
    child = expect(guardian.put(f"/api/v1/reading-sessions/{child_session}/mode-settings", csrf=True, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": [book["storyVersionId"]],
        "timeLimitSeconds": 120,
    }), 200)["data"]
    print(f"儿童授权：grant={child['childGrantId']}, expiresAt={child['expiresAt']}")
    print("儿童可以读取授权绘本和会话；尝试修改设置会返回 403。")
    forbidden = guardian.put(f"/api/v1/reading-sessions/{child_session}/mode-settings", csrf=True, json={
        "mode": "CHILD_SOLO", "allowedStoryVersionIds": [book["storyVersionId"]],
        "timeLimitSeconds": 600,
    })
    print(f"儿童修改时长：HTTP {forbidden.status_code} {error_code(forbidden)}")
    pause(args.pause)

    step(10, "家长解锁，旧儿童凭证立即失效")
    child_token = guardian.session_cookie
    unlock = guardian.post("/api/v1/auth/parent-unlocks", csrf=True,
                           json={"account": account, "password": password})
    expect(unlock, 200)
    print("家长解锁成功，已签发新的家长会话。")
    old = ApiSession(args.base_url, timeout=args.timeout, verify=not args.insecure_tls,
                     allow_insecure_cookies=args.allow_http)
    old.set_cookie("zhiya_session", child_token or "")
    revoked = old.get("/api/v1/child-profiles")
    old.close()
    print(f"使用旧儿童 Cookie 再访问：HTTP {revoked.status_code} {error_code(revoked)}")
    pause(args.pause)

    step(11, "语音接口的安全回退（默认不产生付费调用）")
    demo_voice(client, profile["childProfileId"], book["storyVersionId"], enabled=args.voice)
    pause(args.pause)

    step(12, "演示结束")
    print("已演示：健康检查、登录、档案、书架、会话、幂等、翻页、结束、儿童模式、家长解锁、语音回退。")
    print("未演示（明确说明）：情境提问 API-016/017、原创故事/分支、亲子活动、公网 HTTPS、真实模型效果。")
    client.close()
    guardian.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
