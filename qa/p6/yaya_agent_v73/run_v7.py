"""Replay the frozen v7 build in a new local evidence directory. MOCK by default."""
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

from eval_v6 import dump, reconcile, review_forms
from decimal import Decimal
from provider import Settings, BailianProvider, PRIMARY_MODEL, message_digest
from replay_v6 import replay

HERE = Path(__file__).resolve().parent
def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch", choices=("regression", "v72-dev", "development", "probes", "holdout"), default="regression")
    p.add_argument("--out", type=Path, required=True, help="A new directory; no prior run is overwritten")
    p.add_argument("--live", action="store_true")
    p.add_argument("--prompt-key", action="store_true")
    p.add_argument("--base-url", help="Exact Beijing workspace URL; may also be supplied by environment")
    p.add_argument("--fixture", type=Path, help="Independently frozen synthetic holdout")
    p.add_argument("--fixture-sha256", help="Required for holdout")
    p.add_argument("--cny-per-million", nargs=2, type=Decimal, metavar=("INPUT", "OUTPUT"))
    a = p.parse_args()
    if a.prompt_key and not a.live:
        p.error("--prompt-key is for --live only")
    if (a.batch == "holdout" and not (a.fixture and a.fixture_sha256)) or (a.batch != "holdout" and (a.fixture or a.fixture_sha256)):
        p.error("Only the independently frozen holdout accepts --fixture and --fixture-sha256")
    manifest_path = HERE / "v72-run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if digest(HERE/name) != expected:
            p.error(f"Frozen file changed: {name}")
    fixture = (a.fixture or HERE/{"regression":"fixtures_v72_regression.json","v72-dev":"fixtures_v72_development.json","probes":"fixtures_v7_probes.json","development":"fixtures_v7.json"}.get(a.batch, "fixtures_v7.json")).resolve()
    fh = digest(fixture)
    if a.fixture_sha256 and fh != a.fixture_sha256.lower(): p.error("Holdout SHA-256 mismatch")
    if not a.fixture and fh != manifest["files"][fixture.name]: p.error("Fixture SHA-256 mismatch")
    data = json.loads(fixture.read_text(encoding="utf-8-sig"))
    if data.get("synthetic_only") is not True: p.error("Fixture must explicitly declare synthetic data")
    case_count = len(data["cases"])
    scheduled = sum(len(c["turns"]) for c in data["cases"])
    if a.batch == "holdout" and (case_count != 6 or scheduled != 19): p.error("Unexpected holdout coverage")
    if a.batch == "regression" and scheduled != 16: p.error("Expected 16 paired regression turns")
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
                or json.loads(pre.get("text", "null")) != {"ok": True}):
            p.error("API preflight did not meet the verification criteria")
    else:
        out.mkdir()
    # Retain the exact synthetic fixture and manifest; separate from the untouched originals.
    (out/"fixture.json").write_bytes(fixture.read_bytes())
    (out/"v72-run-manifest.json").write_bytes(manifest_path.read_bytes())
    dump(out/"batch.json", dict(mode=mode, batch=a.batch, cases=case_count, scheduled_turns=scheduled,
                               fixture_sha256=fh, build_sha256=digest(manifest_path)))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    record = out/"record"
    cmd = [sys.executable, str(HERE/"demo.py"), "--v7", "--port", str(port), "--record-dir", str(record),
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
            if a.live and a.batch != "probes" and any(isinstance(r.get("parsed_draft"),dict) and
                    r["parsed_draft"].get("reply") for r in report["rows"]):
                review_forms(report, report_dir)
            print("REPORT",out/"report"/"report.json",flush=True)
        finally:
            worker.terminate()
            worker.wait(10)

if __name__ == "__main__":
    main()

