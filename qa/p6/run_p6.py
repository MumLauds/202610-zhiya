"""P6 一键执行入口：本地起服务、准备数据、跑验收、生成模型对比模板与缺陷表。

示例：
    python qa/p6/run_p6.py --start-local
    python qa/p6/run_p6.py --base-url https://zhiya.example.com --profile public \
        --account p6demo --password 'Zhiya-demo-2026'
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from http.client import HTTPException
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "instance" / "zhiya.sqlite3"
DEFAULT_OUT = ROOT / "qa" / "p6" / "reports"


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    print(f"运行 {Path(cmd[1]).name}", flush=True)
    completed = subprocess.run(cmd, cwd=ROOT, env=env)
    return completed.returncode


def wait_health(base_url: str, server: subprocess.Popen, instance: str, timeout: float = 40.0) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while server.poll() is None and time.monotonic() < deadline:
        try:
            with opener.open(f"{base_url}/api/v1/health", timeout=2) as response:
                if (response.status == 200 and response.headers.get("X-P6-Instance") == instance
                        and server.poll() is None):
                    return True
        except (OSError, HTTPException):
            pass
        time.sleep(0.2)
    return False


def local_revision(root: Path) -> str | None:
    # 只检查本地服务、数据准备和验收使用的输入，报告目录属于运行输出。
    inputs = ["backend/app", "backend/requirements.txt", "database/schema.sql", "database/migrations",
              ":(glob)database/*.py", "data/seed", ":(glob)qa/p6/*.py", "qa/p6/model_cases.json",
              "docs/design/api-contract-v0.4.json"]
    try:
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, check=True,
                              capture_output=True).stdout.decode("ascii").strip()
        changes = subprocess.run(["git", "--no-optional-locks", "status", "--porcelain=v1",
                                  "--untracked-files=all", "--", *inputs],
                                 cwd=root, check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{head}-dirty" if changes else head


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知芽 P6 一键验收")
    parser.add_argument("--base-url", help="被测地址；不传则本地启动")
    parser.add_argument("--profile", choices=["local", "public"], default=None)
    parser.add_argument("--start-local", action="store_true", help="本地启动 uvicorn")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--account")
    parser.add_argument("--password", default=os.environ.get("P6_ACCEPTANCE_PASSWORD", "P6-test-pass-2026"))
    parser.add_argument("--tested-commit", help="被测服务的 Git 提交，外部部署请显式提供")
    parser.add_argument("--text-model-live", action="store_true", help="允许本地后端使用真实文本模型生成小结")
    voice = parser.add_mutually_exclusive_group()
    voice.add_argument("--voice-live", action="store_true", help="明确执行真实 TTS")
    voice.add_argument("--voice-expect-unconfigured", action="store_true", help="已确认目标服务禁用 TTS，验证 503")
    parser.add_argument("--frontend-url", help="前端地址，例如 http://127.0.0.1:5174")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--skip-acceptance", action="store_true")
    parser.add_argument("--skip-model", action="store_true")
    parser.add_argument("--skip-defects", action="store_true")
    args = parser.parse_args(argv)

    args.outdir = args.outdir.resolve()
    args.db = args.db.resolve()
    base_url = args.base_url
    profile = args.profile or ("public" if base_url and base_url.startswith("https://") else "local")
    server: subprocess.Popen | None = None
    instance = uuid4().hex
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"

    tested_commit = args.tested_commit
    if base_url is None:
        if not args.skip_acceptance:
            tested_commit = local_revision(ROOT)
            if args.tested_commit and (not tested_commit or not tested_commit.startswith(args.tested_commit)):
                parser.error("--tested-commit 与本地服务的实际版本不符")
            if not tested_commit:
                print("无法确认本地 Git 版本，本轮被测提交将记为未记录。", file=sys.stderr)
        base_url = f"http://127.0.0.1:{args.port}"
        if not args.skip_prepare:
            code = run([args.python, str(HERE / "prepare_demo_data.py"), "--db", str(args.db),
                        "--yes", "--if-empty"], env=env)
            if code:
                return code
        env["ZHIYA_DB_PATH"] = str(args.db.resolve())
        if not args.text_model_live:
            env["MODEL_API_KEY"] = ""
        if not args.voice_live:
            env["DASHSCOPE_API_KEY"] = ""
            env["TTS_API_URL"] = ""
        print(f"启动本地服务：{base_url}")
        server = subprocess.Popen(
            [args.python, "-m", "uvicorn", "app.main:create_app", "--factory",
             "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(args.port), "--no-access-log",
             "--header", f"X-P6-Instance:{instance}"],
            cwd=ROOT, env=env,
        )

    code = 0
    acceptance_path = args.outdir / "acceptance-latest.json" if args.skip_acceptance else None
    try:
        if server is not None and not wait_health(base_url, server, instance):
            print("本次启动的服务未就绪，请检查端口和启动日志。", file=sys.stderr)
            return 2
        if not args.skip_acceptance:
            cmd = [args.python, str(HERE / "run_acceptance.py"), "--base-url", base_url,
                   "--profile", profile, "--outdir", str(args.outdir)]
            if profile == "local":
                cmd.append("--allow-http")
            if args.voice_live:
                cmd.append("--voice-live")
            elif args.voice_expect_unconfigured:
                cmd.append("--voice-expect-unconfigured")
            if args.account:
                cmd += ["--account", args.account]
                env["P6_ACCEPTANCE_PASSWORD"] = args.password
            if tested_commit:
                cmd += ["--tested-commit", tested_commit]
            if args.frontend_url:
                cmd += ["--frontend-url", args.frontend_url]
            with tempfile.TemporaryDirectory() as tmp:
                receipt = Path(tmp) / "report-path.txt"
                code = run([*cmd, "--report-path-file", str(receipt)], env=env)
                if receipt.is_file():
                    acceptance_path = Path(receipt.read_text(encoding="utf-8"))
                if acceptance_path is None or not acceptance_path.is_file():
                    print("本轮验收未生成归档报告，无法关联缺陷台账。", file=sys.stderr)
                    acceptance_path = None
                    code = code or 2

        if not args.skip_model:
            model_out = str(args.outdir / "model-compare")
            model_code = run([args.python, str(HERE / "run_model_compare.py"), "--outdir", model_out], env=env)
            if not model_code:
                model_code = run([args.python, str(HERE / "summarize_model_compare.py"), "--outdir", model_out], env=env)
            code = code or model_code
        if not args.skip_defects and acceptance_path is not None:
            defects_code = run([args.python, str(HERE / "track_defects.py"), "--outdir", str(args.outdir),
                                "--acceptance", str(acceptance_path)], env=env)
            code = code or defects_code
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()

    if acceptance_path is not None and acceptance_path.is_file():
        payload = json.loads(acceptance_path.read_text(encoding="utf-8"))
        print(json.dumps(payload.get("meta", {}), ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
