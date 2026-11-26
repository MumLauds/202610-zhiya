"""Run distinct live batches with one locally entered no-echo key."""
import argparse
from getpass import getpass, GetPassWarning
import os
from pathlib import Path
import subprocess
import sys
import warnings

HERE = Path(__file__).resolve().parent
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--base-url", required=True)
p.add_argument("--fixture", type=Path, required=True)
p.add_argument("--fixture-sha256", required=True)
p.add_argument("--out", type=Path, required=True)
p.add_argument("--cny-per-million", nargs=2)
a = p.parse_args()
if not sys.stdin.isatty():
    p.error("Run in your own interactive terminal")
# Validate the holdout before spending any live tokens.
from run_v7 import digest
if digest(a.fixture) != a.fixture_sha256.lower(): p.error("Frozen holdout SHA-256 mismatch")
a.out.mkdir()
try:
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        key = getpass("Beijing Bailian API key (hidden): ").strip()
except (GetPassWarning, EOFError): p.error("Cannot safely read key")
if not key: p.error("No key entered")
env = dict(os.environ, DASHSCOPE_API_KEY=key, DASHSCOPE_BASE_URL=a.base_url)
del key
for batch in ("development", "probes", "holdout"):
    cmd = [sys.executable, "-B", str(HERE/"run_v7.py"), "--live", "--batch",batch, "--out", str(a.out/batch)]
    if a.cny_per_million: cmd += ["--cny-per-million", *a.cny_per_million]
    if batch=="holdout":
        cmd += ["--fixture",str(a.fixture), "--fixture-sha256",a.fixture_sha256]
    subprocess.run(cmd, env=env, check=True)
print("Completed isolated batches:",a.out)

