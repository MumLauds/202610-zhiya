"""P6 验收测试公共设施：HTTP 会话、结果记录与报告输出。

本模块不导入后端业务代码，验收脚本可以只依赖 HTTP 访问本地或公网部署。
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from report_files import publish_pair

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
SKIP = "SKIP"
WARN = "WARN"

STATUS_MARK = {PASS: "通过", FAIL: "失败", BLOCKED: "阻塞", SKIP: "跳过", WARN: "警告"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def unique_suffix() -> str:
    return datetime.now().strftime("%m%d%H%M%S") + secrets.token_hex(2)


class Blocked(Exception):
    """用例依赖的外部条件不满足，不能判定为通过或失败。"""


@dataclass
class Warn:
    """用例完成但存在不阻塞验收的缺陷。"""
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    case_id: str
    title: str
    ac: str
    status: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def loopback_http(url: str) -> bool:
    target = httpx.URL(url)
    return target.scheme == "http" and loopback_host(target.host)


class ApiSession:
    """带 Cookie 持久化的 HTTP 客户端。

    服务端的会话 Cookie 固定带 Secure；本地 HTTP 验收时 httpx 默认不会回传
    Secure Cookie。设置 allow_insecure_cookies 后，把收到的 Cookie 以非 Secure
    方式写回本地 CookieJar，仅用于 127.0.0.1 自测，不改变服务端行为。
    """

    def __init__(self, base_url: str, *, timeout: float = 20.0, verify: bool = True,
                 allow_insecure_cookies: bool = False, trust_env: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.allow_insecure_cookies = allow_insecure_cookies and loopback_http(self.base_url)
        # 默认忽略系统代理：Windows 注册表代理会把本机 POST 请求交给代理，导致请求体
        # 被丢弃并返回 422。确实需要代理时设置 ZHIYA_USE_ENV_PROXY=1。
        trust_env = trust_env or os.environ.get("ZHIYA_USE_ENV_PROXY") == "1"
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            verify=verify,
            follow_redirects=False,
            trust_env=trust_env,
            headers={"User-Agent": "zhiya-p6-acceptance/1.0"},
        )

    @property
    def csrf(self) -> str | None:
        return self.client.cookies.get("zhiya_csrf")

    @property
    def session_cookie(self) -> str | None:
        return self.client.cookies.get("zhiya_session")

    def _allow_local_cookies(self) -> None:
        if self.allow_insecure_cookies:
            for cookie in self.client.cookies.jar:
                cookie.secure = False

    def request(self, method: str, path: str, *, csrf: bool = False,
                headers: dict[str, str] | None = None, **kwargs) -> httpx.Response:
        target = self.client.base_url.join(path)
        base = self.client.base_url
        if (target.scheme, target.host, target.port) != (base.scheme, base.host, base.port):
            raise ValueError("请求地址须与被测服务同源")
        if target.scheme != "https" and not self.allow_insecure_cookies:
            raise ValueError("认证请求要求 HTTPS；本地回环 HTTP 须显式启用 --allow-http")
        request_headers = dict(headers or {})
        if csrf:
            token = self.csrf
            if token:
                request_headers["X-CSRF-Token"] = token
        response = self.client.request(method, path, headers=request_headers, **kwargs)
        self._allow_local_cookies()
        return response

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, *, csrf: bool = False, **kwargs) -> httpx.Response:
        return self.request("POST", path, csrf=csrf, **kwargs)

    def patch(self, path: str, *, csrf: bool = False, **kwargs) -> httpx.Response:
        return self.request("PATCH", path, csrf=csrf, **kwargs)

    def put(self, path: str, *, csrf: bool = False, **kwargs) -> httpx.Response:
        return self.request("PUT", path, csrf=csrf, **kwargs)

    def delete(self, path: str, *, csrf: bool = False, **kwargs) -> httpx.Response:
        return self.request("DELETE", path, csrf=csrf, **kwargs)

    def set_cookie(self, name: str, value: str) -> None:
        self.client.cookies.extract_cookies(httpx.Response(
            200, headers={"Set-Cookie": f"{name}={value}; Path=/; Secure"},
            request=httpx.Request("GET", self.base_url),
        ))
        self._allow_local_cookies()

    def close(self) -> None:
        self.client.close()


def select_story(client: ApiSession, items: list[dict], version_id: int | None) -> dict:
    if version_id is not None:
        response = client.get(f"/api/v1/story-versions/{version_id}")
        if response.status_code == 404:
            raise Blocked(f"指定绘本版本 {version_id} 不可用")
        if response.status_code != 200:
            raise AssertionError(f"读取指定绘本版本 {version_id} 返回 HTTP {response.status_code}")
        story = response.json()["data"]
        if story.get("storyVersionId") != version_id:
            raise AssertionError(f"绘本详情返回的版本与指定版本 {version_id} 不一致")
    else:
        if not items:
            raise Blocked("书架为空，请先准备已发布绘本")
        story = next((item for item in items if len(item.get("pages", [])) >= 4), items[0])
    if not story.get("pages"):
        raise AssertionError("绘本详情没有页面")
    return story


def response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def error_code(response: httpx.Response) -> str | None:
    body = response_json(response)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"].get("code")
    return None


def cookie_flags(response: httpx.Response, name: str) -> dict[str, bool]:
    for header in response.headers.get_list("set-cookie"):
        cookie, *attributes = header.split(";")
        if cookie.partition("=")[0].strip() == name:
            flags = {attribute.strip().lower() for attribute in attributes}
            return {"secure": "secure" in flags, "httpOnly": "httponly" in flags,
                    "sameSiteLax": "samesite=lax" in flags}
    raise AssertionError(f"响应未设置 {name}")


class Recorder:
    def __init__(self) -> None:
        self.results: list[Result] = []
        self.meta: dict[str, Any] = {"startedAt": utc_now()}

    def case(self, case_id: str, title: str, ac: str, fn: Callable[[], tuple[str, dict] | str | Warn | None],
             *, blocked: str | None = None) -> Result:
        if blocked:
            return self.record(case_id, title, ac, BLOCKED, blocked)
        started = time.perf_counter()
        try:
            outcome = fn()
            if isinstance(outcome, Warn):
                status, detail, evidence = WARN, outcome.detail, outcome.evidence
            elif isinstance(outcome, tuple):
                status = PASS
                detail, evidence = outcome
            elif isinstance(outcome, str):
                status, detail, evidence = PASS, outcome, {}
            else:
                status, detail, evidence = PASS, "", {}
        except Blocked as exc:
            status, detail, evidence = BLOCKED, str(exc) or "依赖条件不满足", {}
        except AssertionError as exc:
            status, detail, evidence = FAIL, str(exc) or "断言失败", {}
        except Exception as exc:  # noqa: BLE001 - 记录任何异常并继续后续用例
            status = FAIL
            detail = f"{type(exc).__name__}: {exc}"
            evidence = {"traceback": traceback.format_exc(limit=6)}
        result = Result(
            case_id=case_id,
            title=title,
            ac=ac,
            status=status,
            detail=detail,
            evidence=evidence,
            started_at=utc_now(),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        self.results.append(result)
        self._print(result)
        return result

    def record(self, case_id: str, title: str, ac: str, status: str, detail: str,
               evidence: dict | None = None) -> Result:
        result = Result(case_id=case_id, title=title, ac=ac, status=status, detail=detail,
                        evidence=evidence or {}, started_at=utc_now(), duration_ms=0)
        self.results.append(result)
        self._print(result)
        return result

    @staticmethod
    def _print(result: Result) -> None:
        print(f"[{result.status:7}] {result.case_id} {result.title}"
              + (f" - {result.detail}" if result.detail else ""))

    def summary(self) -> dict[str, Any]:
        counts = {status: sum(1 for r in self.results if r.status == status)
                  for status in (PASS, FAIL, BLOCKED, SKIP, WARN)}
        by_ac: dict[str, dict[str, int]] = {}
        for result in self.results:
            bucket = by_ac.setdefault(result.ac or "未映射", {})
            bucket[result.status] = bucket.get(result.status, 0) + 1
        return {
            "total": len(self.results),
            "counts": counts,
            "byAC": by_ac,
            "failed": [r.case_id for r in self.results if r.status == FAIL],
            "blocked": [r.case_id for r in self.results if r.status == BLOCKED],
            "finishedAt": utc_now(),
        }


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def write_reports(recorder: Recorder, outdir: Path, *, title: str = "知芽 P6 验收测试报告") -> dict[str, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    suffix = f"{stamp()}-{secrets.token_hex(16)}"
    summary = recorder.summary()
    payload = {"title": title, "meta": {**recorder.meta, **summary, "runId": suffix}, "results": [r.to_dict() for r in recorder.results]}

    json_path = outdir / f"acceptance-{suffix}.json"
    latest_json = outdir / "acceptance-latest.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    json_path.write_text(text, encoding="utf-8")

    md_lines = [
        f"# {title}",
        "",
        f"- 运行标识：{suffix}",
        f"- 开始时间：{recorder.meta.get('startedAt', '')}",
        f"- 结束时间：{summary['finishedAt']}",
        f"- 被测提交：{recorder.meta.get('testedCommit', '未记录')}",
        f"- 被测地址：{recorder.meta.get('baseUrl', '')}",
        f"- 测试环境：{recorder.meta.get('profile', '')}",
        "",
        "## 结果汇总",
        "",
        "| 状态 | 数量 |",
        "| --- | ---: |",
    ]
    for status in (PASS, FAIL, BLOCKED, SKIP, WARN):
        md_lines.append(f"| {status} | {summary['counts'][status]} |")
    md_lines += ["", f"合计 {summary['total']} 项。", "", "## 用例明细", "",
                 "| 用例 | 验收项 | 标题 | 状态 | 耗时/ms | 说明 |",
                 "| --- | --- | --- | --- | ---: | --- |"]
    for result in recorder.results:
        md_lines.append(
            f"| {result.case_id} | {result.ac} | {_md_escape(result.title)} | {result.status} "
            f"| {result.duration_ms} | {_md_escape(result.detail)} |"
        )
    md_lines += ["", "## 验收项汇总", "", "| 验收项 | 通过 | 失败 | 阻塞 | 跳过 | 警告 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for ac, counts in sorted(summary["byAC"].items()):
        md_lines.append(
            f"| {ac} | {counts.get(PASS, 0)} | {counts.get(FAIL, 0)} | {counts.get(BLOCKED, 0)} "
            f"| {counts.get(SKIP, 0)} | {counts.get(WARN, 0)} |"
        )
    md_path = outdir / f"acceptance-{suffix}.md"
    latest_md = outdir / "acceptance-latest.md"
    md_text = "\n".join(md_lines) + "\n"
    md_path.write_text(md_text, encoding="utf-8")

    suite = ET.Element("testsuite", {
        "name": title,
        "tests": str(len(recorder.results)),
        "failures": str(summary["counts"][FAIL]),
        "skipped": str(sum(summary["counts"][s] for s in (BLOCKED, SKIP, WARN))),
        "time": f"{sum(r.duration_ms for r in recorder.results) / 1000:.3f}",
    })
    for result in recorder.results:
        case = ET.SubElement(suite, "testcase", {
            "classname": result.ac or "p6",
            "name": f"{result.case_id} {result.title}",
            "time": f"{result.duration_ms / 1000:.3f}",
        })
        if result.status == FAIL:
            failure = ET.SubElement(case, "failure", {"message": result.detail})
            failure.text = result.detail
        elif result.status in (BLOCKED, SKIP, WARN):
            ET.SubElement(case, "skipped", {"type": result.status, "message": result.detail})
    xml_path = outdir / f"acceptance-{suffix}.xml"
    ET.ElementTree(suite).write(xml_path, encoding="utf-8", xml_declaration=True)

    with tempfile.TemporaryDirectory(dir=outdir) as temporary:
        staging = Path(temporary)
        staged_md, staged_json = staging / latest_md.name, staging / latest_json.name
        staged_md.write_text(md_text, encoding="utf-8")
        staged_json.write_text(text, encoding="utf-8")
        publish_pair(staged_md, staged_json, outdir)

    return {"json": json_path, "markdown": md_path, "junit": xml_path,
            "latestJson": latest_json, "latestMarkdown": latest_md}


def load_latest(outdir: Path) -> dict[str, Any] | None:
    path = outdir / "acceptance-latest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
