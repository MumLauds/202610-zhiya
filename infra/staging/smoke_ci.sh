#!/usr/bin/env bash
set -euo pipefail

project="zhiya-agent-stg-ci-${GITHUB_RUN_ID:-$$}-${GITHUB_RUN_ATTEMPT:-1}"
scratch=$(mktemp -d)
compose=(docker compose --project-name "$project" --env-file infra/staging/.env.example -f infra/staging/compose.yaml)
cleanup() {
  local status=$?
  if (( status )); then
    "${compose[@]}" ps || true
    "${compose[@]}" logs --tail 50 || true
  fi
  "${compose[@]}" down --volumes || true
}
trap cleanup EXIT

export STAGING_HTTPS_PORT=8443
export YAYA_AGENT2_MODE=off YAYA_AGENT2_ALLOWLIST= YAYA_AGENT2_INPUT_ALLOWLIST=
export YAYA_AGENT2_API_KEY= YAYA_AGENT2_API_URL= YAYA_AGENT2_LEDGER_SECRET=
"${compose[@]}" build
"${compose[@]}" up -d --wait --wait-timeout 120 --no-build
ca="$scratch/staging-ca.crt"
for attempt in $(seq 1 20); do
  if "${compose[@]}" cp web:/data/caddy/pki/authorities/local/root.crt "$ca" 2>/dev/null; then
    break
  fi
  sleep 1
done
test -s "$ca"
curl --silent --show-error --fail --cacert "$ca" --retry 15 --retry-connrefused --retry-delay 1 \
  --max-time 10 "https://localhost:$STAGING_HTTPS_PORT/api/v1/health" >/dev/null
smoke=(python infra/staging/smoke_ci.py "https://localhost:$STAGING_HTTPS_PORT" "$ca" "$scratch/state.json")
"${smoke[@]}" setup
echo "PASS: image builds, container startup, HTTPS certificate and API health"

export YAYA_AGENT2_MODE=allowlist
export YAYA_AGENT2_ALLOWLIST="$(python -c 'import json,sys; print(",".join(map(str,json.load(open(sys.argv[1]))["enrolled"])))' "$scratch/state.json")"
export YAYA_AGENT2_INPUT_ALLOWLIST="${YAYA_AGENT2_ALLOWLIST%%,*}"
export YAYA_AGENT2_API_URL=https://ws-synthetic-ci.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions
export YAYA_AGENT2_API_KEY=synthetic-ci-placeholder
export YAYA_AGENT2_LEDGER_SECRET=synthetic-ci-ledger-secret-not-a-production-credential
"${compose[@]}" up -d --wait --wait-timeout 120 --no-build backend
"${compose[@]}" exec -T backend python - <<'PYTHON'
import os, sqlite3
from contextlib import closing
from app.services.yaya_agent_config import YayaSettings

settings = YayaSettings.from_env()
allowed, local_only = [int(part) for part in os.environ["YAYA_AGENT2_ALLOWLIST"].split(",")]
assert settings.model.model_name == "qwen3.8-flash"
assert settings.model.model_api_url.startswith("https://ws-synthetic-ci.cn-beijing.maas.aliyuncs.com/")
assert settings.model.model_api_key == "synthetic-ci-placeholder"
assert settings.enrolls(local_only) and settings.authorizes_model_input(allowed)
assert not settings.authorizes_model_input(local_only)
with closing(sqlite3.connect(os.environ["ZHIYA_DB_PATH"])) as db:
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("SELECT source_ref, status FROM story_versions").fetchall() == [("synthetic-yaya-staging", "PUBLISHED")]
    assert db.execute("SELECT count(*) FROM story_pages").fetchone() == (3,)
PYTHON
"${smoke[@]}" allowlist
"${compose[@]}" exec -T backend python -c 'import os,sqlite3; db=sqlite3.connect(os.environ["ZHIYA_DB_PATH"]); assert db.execute("SELECT COUNT(*) FROM yaya_agent_calls").fetchone() == (0,)'
echo "PASS: SQLite initialization, seed replay, cohort and input authorization, Beijing Flash runtime config"

export YAYA_AGENT2_MODE=off
"${compose[@]}" up -d --wait --wait-timeout 120 --no-build backend
"${smoke[@]}" off
echo "PASS: off mode restores legacy for new sessions and preserves pinned 2.0 sessions"
echo "BLOCKED: real Beijing provider calls require separate approved synthetic staging credentials"
