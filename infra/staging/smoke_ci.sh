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
export STAGING_SPEECH_API_KEY= STAGING_ASR_API_URL= STAGING_TTS_API_URL=
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
export YAYA_AGENT2_INPUT_ALLOWLIST="${YAYA_AGENT2_ALLOWLIST%%,*}:$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["profile"])' "$scratch/state.json")"
export YAYA_AGENT2_API_URL=https://ws-synthetic-ci.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions
export YAYA_AGENT2_API_KEY=synthetic-ci-placeholder
export YAYA_AGENT2_LEDGER_SECRET=synthetic-ci-ledger-secret-not-a-production-credential
export YAYA_AGENT2_INPUT_CNY_PER_MILLION_TOKENS=1.25 YAYA_AGENT2_OUTPUT_CNY_PER_MILLION_TOKENS=3.5
export STAGING_SPEECH_API_KEY=synthetic-ci-speech-placeholder
export STAGING_ASR_API_URL=https://ws-synthetic-voice.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions
export STAGING_TTS_API_URL=https://ws-synthetic-voice.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer
"${compose[@]}" up -d --wait --wait-timeout 120 --no-build backend
"${compose[@]}" exec -T backend python - <<'PYTHON'
import os, sqlite3
from contextlib import closing
from app.services.yaya_agent_config import YayaSettings
from app.speech_config import SpeechSettings

settings = YayaSettings.from_env()
speech = SpeechSettings.from_env()
assert speech.asr_configured and speech.tts_configured
assert speech.api_key == "synthetic-ci-speech-placeholder"
assert speech.api_key != settings.model.model_api_key
assert speech.asr_model == "qwen3-asr-flash"
assert speech.tts_model == "qwen-audio-3.1-tts-flash"
allowed, local_only = [int(part) for part in os.environ["YAYA_AGENT2_ALLOWLIST"].split(",")]
assert settings.model.model_name == "qwen3.8-flash"
assert settings.model.model_api_url.startswith("https://ws-synthetic-ci.cn-beijing.maas.aliyuncs.com/")
assert settings.model.model_api_key == "synthetic-ci-placeholder"
assert settings.cost_micros(56, 13) == 116
_, profile = [int(part) for part in os.environ["YAYA_AGENT2_INPUT_ALLOWLIST"].split(":")]
assert settings.enrolls(local_only) and settings.authorizes_model_input(allowed, profile)
assert not settings.authorizes_model_input(local_only, profile)
assert not settings.authorizes_model_input(allowed, profile+1)
with closing(sqlite3.connect(os.environ["ZHIYA_DB_PATH"])) as db:
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert {"priced", "settled_micros"} <= {row[1] for row in db.execute("PRAGMA table_info(yaya_agent_calls)")}
    assert db.execute("SELECT source_ref, status FROM story_versions").fetchall() == [("synthetic-yaya-staging", "PUBLISHED")]
    assert db.execute("SELECT count(*) FROM story_pages").fetchone() == (3,)
PYTHON
"${smoke[@]}" allowlist
"${compose[@]}" exec -T backend python -c 'import os,sqlite3; db=sqlite3.connect(os.environ["ZHIYA_DB_PATH"]); assert db.execute("SELECT COUNT(*) FROM yaya_agent_calls").fetchone() == (0,)'
echo "PASS: SQLite initialization, seed replay, cohort and input authorization, Beijing Flash runtime config"
echo "PASS: isolated ASR and TTS configuration with synthetic values; no voice provider calls"

export YAYA_AGENT2_INPUT_ALLOWLIST=
"${compose[@]}" up -d --wait --wait-timeout 120 --no-build backend
"${smoke[@]}" revoked
"${compose[@]}" exec -T backend python -c 'import os,sqlite3; db=sqlite3.connect(os.environ["ZHIYA_DB_PATH"]); assert db.execute("SELECT COUNT(*) FROM yaya_agent_calls").fetchone() == (0,)'
echo "PASS: revoked input consent denies provider dispatch on the existing pinned session"

export YAYA_AGENT2_MODE=off
if [[ -n ${STAGING_ROLLBACK_REVISION:-} ]]; then
  # Use the previous qualified bridge build so existing 2.0 sessions stay pinned.
  revision=$STAGING_ROLLBACK_REVISION
  [[ $revision =~ ^[0-9a-f]{40}$ ]]
  git cat-file -e "$revision^{commit}" 2>/dev/null || git fetch --quiet --no-tags --depth=1 origin "$revision"
  source="$scratch/rollback-source"
  mkdir "$source"
  git archive "$revision" infra/Dockerfile infra/hk/Web.Dockerfile backend database data/seed knowledge-base_v2.5/图片 frontend |
    tar -x -C "$source"
  docker build --label "org.opencontainers.image.revision=$revision" -t "$project-rollback-backend" -f "$source/infra/Dockerfile" "$source"
  docker build --label "org.opencontainers.image.revision=$revision" -t "$project-rollback-web" -f "$source/infra/hk/Web.Dockerfile" "$source/frontend"
  cat > "$scratch/rollback.yaml" <<YAML
services:
  backend:
    image: $project-rollback-backend
  web:
    image: $project-rollback-web
YAML
  image_id() { docker inspect -f '{{.Image}}' "$("${compose[@]}" ps -q "$1")"; }
  db_identity() { "${compose[@]}" exec -T backend stat -c '%d:%i' /var/lib/zhiya/staging.sqlite3; }
  candidate_backend=$(image_id backend)
  candidate_web=$(image_id web)
  db_before=$(db_identity)
  "${compose[@]}" -f "$scratch/rollback.yaml" up -d --wait --wait-timeout 120 --no-build --pull never
  baseline_backend=$(docker image inspect -f '{{.Id}}' "$project-rollback-backend")
  baseline_web=$(docker image inspect -f '{{.Id}}' "$project-rollback-web")
  [[ $(image_id backend) == "$baseline_backend" && $candidate_backend != "$baseline_backend" ]]
  [[ $(image_id web) == "$baseline_web" && $candidate_web != "$baseline_web" ]]
  [[ $(db_identity) == "$db_before" ]]
  curl --silent --show-error --fail --cacert "$ca" --max-time 10 "https://localhost:$STAGING_HTTPS_PORT/mobile.html" >/dev/null
  echo "PASS: actual backend and web image rollback to $revision; same SQLite file and trusted HTTPS"
else
  "${compose[@]}" up -d --wait --wait-timeout 120 --no-build backend
fi
"${smoke[@]}" off
echo "PASS: off mode restores legacy for new sessions and preserves pinned 2.0 sessions"
echo "BLOCKED: real Beijing provider calls require separate approved synthetic staging credentials"
