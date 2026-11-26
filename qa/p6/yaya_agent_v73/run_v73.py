"""Replay frozen v7.3 synthetic diagnostics or the fixed live sample in a new local evidence directory. MOCK by default."""
import argparse
from getpass import getpass, GetPassWarning
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import warnings

from eval_v6 import dump, reconcile
from decimal import Decimal
from provider import Settings, BailianProvider, PRIMARY_MODEL, message_digest
from replay_v6 import replay

HERE = Path(__file__).resolve().parent
def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def preflight_ok(pre):
    usage = pre.get("usage")
    try:
        parsed = json.loads(pre.get("text") or "")
    except (TypeError, ValueError):
        return False
    return (isinstance(parsed, dict) and parsed.keys() == {"ok"} and parsed["ok"] is True
            and isinstance(usage, dict)
            and all(type(usage.get(k)) is int and usage[k] > 0
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")))

def sha_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch", choices=("diagnostics", "live-sample"), default="diagnostics")
    p.add_argument("--out", type=Path, required=True, help="A new directory; no prior run is overwritten")
    p.add_argument("--live", action="store_true")
    p.add_argument("--prompt-key", action="store_true")
    p.add_argument("--base-url", help="Exact Beijing workspace URL; may also be supplied by environment")
    p.add_argument("--cny-per-million", nargs=2, type=Decimal, metavar=("INPUT", "OUTPUT"))
    a = p.parse_args()
    if a.prompt_key and not a.live:
        p.error("--prompt-key is for --live only")
    if a.live and a.batch != "live-sample":
        p.error("Only the fixed 12-16 turn live-sample may use --live")
    manifest_path = HERE / "v73-run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if digest(HERE/name) != expected:
            p.error(f"Frozen file changed: {name}")
    fixture = HERE / {"diagnostics":"fixtures_v73_diagnostics.json","live-sample":"fixtures_v73_live.json"}[a.batch]
    fh = digest(fixture)
    if fh != manifest["files"][fixture.name]: p.error("Fixture SHA-256 mismatch")
    data = json.loads(fixture.read_text(encoding="utf-8-sig"))
    if data.get("synthetic_only") is not True: p.error("Fixture must explicitly declare synthetic data")
    case_count = len(data["cases"])
    scheduled = sum(len(c["turns"]) for c in data["cases"])
    nonempty = sum(bool(t["child"].strip()) for c in data["cases"] for t in c["turns"])
    if a.batch == "live-sample" and not 12 <= nonempty <= 16:
        p.error("Expected 12-16 non-empty sample turns")
    if a.batch == "diagnostics" and (case_count < 18 or nonempty < 18):
        p.error("Expected at least 18 diagnostic cases and non-empty turns")
    out = a.out.resolve()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if a.base_url: env["DASHSCOPE_BASE_URL"] = a.base_url
    mode = "LIVE" if a.live else "MOCK"
    # Keys are only in memory / the one child demo process and never logged.
    if a.live:
        if a.prompt_key:
            if not sys.stdin.isatty(): p.error("Run in an interactive terminal to enter the key without echo")
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", GetPassWarning)
                    env["DASHSCOPE_API_KEY"] = getpass("Beijing Bailian API key (hidden): ").strip()
            except (GetPassWarning, EOFError): p.error("Cannot safely read key")
        if not env.get("DASHSCOPE_API_KEY", "").strip(): p.error("DASHSCOPE_API_KEY is not configured")
        cfg = Settings.from_env(env)
        if cfg.region != "cn-beijing" or cfg.timeout_s != 60: p.error("Only Beijing workspace and 60-second provider timeout are supported")
        messages = [{"role":"system","content":"Synthetic connectivity check. Return a JSON object only."},
                    {"role":"user","content":'Return the JSON object {"ok":true}.'}]
        out.mkdir()
        with BailianProvider(cfg, allow_message={message_digest(messages)}.__contains__) as model:
            pre = model.complete(messages, PRIMARY_MODEL)
        dump(out/"preflight.json", {k: pre.get(k) for k in ("status","error_type","http_status","request_id","call_ms","usage","model_returned","text")})
        if (pre["status"] != "RETURNED" or pre.get("http_status") != 200 or not pre.get("request_id")
                or pre.get("model_returned") != PRIMARY_MODEL or not pre.get("usage")
                or not preflight_ok(pre)):
            p.error("API preflight did not meet the verification criteria")
    else:
        env.pop("DASHSCOPE_API_KEY", None)
        out.mkdir()
    # Retain the exact synthetic fixture and manifest; separate from the untouched originals.
    (out/"fixture.json").write_bytes(fixture.read_bytes())
    (out/"v73-run-manifest.json").write_bytes(manifest_path.read_bytes())
    dump(out/"batch.json", dict(mode=mode, batch=a.batch, cases=case_count, scheduled_turns=scheduled,
                               fixture_sha256=fh, build_sha256=digest(manifest_path)))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    record = out/"record"
    cmd = [sys.executable, str(HERE/"demo.py"), "--v73", "--port", str(port), "--record-dir", str(record),
           "--v6-fixtures", str(out/"fixture.json"), "--v6-fixtures-sha256", fh]
    if not a.live: cmd.append("--mock")
    with (out/"server.log").open("w", encoding="utf-8") as log:
        worker = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=log, stderr=log)
        env.pop("DASHSCOPE_API_KEY", None)
        try:
            http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(100):
                if worker.poll() is not None: p.error("Demo did not start; inspect server.log")
                try:
                    with http.open(f"http://127.0.0.1:{port}/v6/catalog", timeout=.3) as r:
                        if json.load(r).get("version") == 7: break
                except OSError:
                    time.sleep(.10)
            else: p.error("Demo readiness check timed out")
            replay(port,mode,record,[],version=7)
            journal = record/"v7-evidence.jsonl"
            for _ in range(100):
                lines = journal.read_bytes().splitlines()
                try: events = [json.loads(l) for l in lines]
                except json.JSONDecodeError: events = []
                if sum(e["event"] == "server_receipt" for e in events)==scheduled: break
                time.sleep(.1)
            else: p.error("Incomplete journal; keep evidence and see server.log")
            report = reconcile(events, [])
            for row in report["rows"]:
                messages = row["messages"]
                row.update(prompt_hash=sha_text(messages[0]["content"]), context_hash=sha_text(messages[1]["content"]),
                           input_hash=sha_text(row["child"]), messages_hash=message_digest(messages))
                if row["prompt_hash"] != manifest["system_prompt_sha256"]:
                    p.error("Observed system prompt differs from the frozen v7.3 prompt")
            report["scope"] = a.batch.upper()
            report["evidence_sha256"] = digest(journal)
            summary = report["summary"]
            cost = None
            if (a.cny_per_million and all(r.is_finite() and r >= 0 for r in a.cny_per_million)
                    and a.live and summary["observed_live_api_calls"] > 0
                    and all(v["reported_calls"] == summary["observed_live_api_calls"] for v in summary["usage"].values())):
                cost = str(sum(Decimal(summary["usage"][k]["observed_sum"])*rate for k,rate in zip(
                    ("prompt_tokens","completion_tokens"),a.cny_per_million))/10**6)
            summary.update(estimated_cny_excluding_preflight=cost, actual_invoice_cny=None,
                           independent_verifier="NOT_PERFORMED", human_review="PENDING" if a.live else "NOT_APPLICABLE")
            report_dir = out/"report"
            report_dir.mkdir()
            dump(report_dir/"report.json",report)
            print("REPORT",out/"report"/"report.json",flush=True)
        finally:
            worker.terminate()
            worker.wait(10)

if __name__ == "__main__":
    main()
