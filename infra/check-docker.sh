#!/bin/sh
# Isolated empty-key verification. No paid provider calls; requires Docker Compose.
set -eu
cd "$(dirname "$0")/.."
unset MODEL_API_KEY DASHSCOPE_API_KEY MODEL_API_URL MODEL_NAME MODEL_TIMEOUT_SECONDS MODEL_MAX_TOKENS
unset ASR_API_URL ASR_MODEL TTS_API_URL TTS_MODEL TTS_VOICE VOICE_TIMEOUT_SECONDS COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES
project="zhiya-check-$(date +%s)-$$"
container="$project-backend"
dc() { docker compose --env-file /dev/null -p "$project" -f infra/compose.yaml "$@"; }
inside() { docker exec -i "$container" python -; }
cleanup() {
    docker rm -f "$container" >/dev/null 2>&1 || true
    dc down
    printf 'Retained test volume: %s_sqlite-data\n' "$project"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'PROJECT=%s\n' "$project"
dc config --quiet
dc build
# Compose run does not publish service ports; use a free loopback port.
dc run -d --no-deps --name "$container" --publish '127.0.0.1::8000' backend
docker port "$container" 8000
for expected in 503 200; do
    if [ "$expected" = 200 ]; then
        docker exec "$container" python database/init_db.py
    fi
    docker exec -i -e "EXPECTED_STATUS=$expected" "$container" python - <<'PY'
import json, os, time, urllib.request, urllib.error
from pathlib import Path
expected = int(os.environ['EXPECTED_STATUS'])
for attempt in range(50):
    try:
        response = urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2)
    except urllib.error.HTTPError as exc:
        response = exc
    except urllib.error.URLError:
        time.sleep(0.2)
        continue
    with response:
        body = json.load(response)
        assert response.status == expected, response.status
        assert body['requestId'] == response.headers['X-Request-ID']
        if expected == 200:
            assert body['data']['status'] == 'ok'
        else:
            assert body['error']['code'] == 'DEPENDENCY_UNAVAILABLE'
            assert not Path(os.environ['ZHIYA_DB_PATH']).exists()
    break
else:
    raise AssertionError('health server did not start')
print(f'PASS health {expected}, request ID and explicit initialization')
PY
done
inside <<'PY'
import json, urllib.error, urllib.request
for method, path in [('POST', 'voice/transcriptions'), ('POST', 'voice/syntheses'), ('GET', 'media/audio-test')]:
    request = urllib.request.Request('http://127.0.0.1:8000/api/v1/' + path, method=method)
    try:
        urllib.request.urlopen(request, timeout=2)
    except urllib.error.HTTPError as response:
        assert response.code == 503
        assert json.load(response)['error']['code'] == 'VOICE_AUTH_NOT_CONFIGURED'
    else:
        raise AssertionError('Voice route did not reject missing business authentication')
print('PASS public speech routes reject missing business authentication')
PY
for source in $(find backend/app -name '*.py' -type f) backend/app/voice_catalog.json backend/smoke_model.py backend/smoke_voice.py; do
    expected=$(sha256sum "$source" | cut -d ' ' -f 1)
    actual=$(docker exec "$container" sha256sum "/app/$source" | cut -d ' ' -f 1)
    test "$expected" = "$actual"
done
printf 'PASS image contains current backend source\n'
inside <<'PY'
import asyncio, io, json, os, subprocess, wave
from pathlib import Path
from app.config import Settings
from app.adapters.model import ModelAdapter
from app.model_gateway import ModelGateway
from app.schemas.agent import ModelGenerateRequest
from app.errors import ServiceError
from app.speech_config import SpeechSettings
from app.speech_options import MODEL_RATES
from app.voice_gateway import VoiceGateway
assert os.getuid() == 10001
assert not list(Path('/app').rglob('.env*'))
s, v = Settings.from_env(), SpeechSettings.from_env()
assert not s.model_configured and not v.asr_configured and not v.tts_configured
assert s.model_name == 'deepseek-flash' and v.tts_model == 'qwen-audio-3.1-tts-flash'
options = subprocess.run(['python', 'backend/smoke_voice.py', 'options'], capture_output=True,
                         text=True, check=True, env={**os.environ, 'VOICE_TIMEOUT_SECONDS': 'invalid'})
catalog = json.loads(options.stdout)['data']
assert [voice['voiceId'] for voice in catalog['voices']] == [
    'wenhuaiqing_v3.1', 'yezhiqing_v3.1', 'longniuniu_v3.1',
    'longshanshan_v3.1', 'longjielidou_v3.1', 'longpaopao_v3.1',
]
assert len(json.loads(Path('/app/backend/app/voice_catalog.json').read_text())['voices']) == 6
assert catalog['speechRates'] == ["SLOW", "NORMAL", "FAST"]
assert MODEL_RATES == {"SLOW": 0.9, "NORMAL": 1.0, "FAST": 1.2}
print('PASS voice/model-rate catalog works without credentials or valid provider configuration')
for script, args, code in (
    ('smoke_model.py', [], 'MODEL_NOT_CONFIGURED'),
    ('smoke_voice.py', ['tts', '/tmp/zhiya-no-key.wav'], 'TTS_NOT_CONFIGURED'),
):
    result = subprocess.run(['python', f'backend/{script}', *args], input='test\n',
                            text=True, capture_output=True)
    assert result.returncode == 1 and code in result.stderr, script
assert not Path('/tmp/zhiya-no-key.wav').exists()
buffer = io.BytesIO()
with wave.open(buffer, 'wb') as wav:
    wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    wav.writeframes(b'\0\0' * 160)
async def check():
    async with ModelGateway(s) as gateway:
        result = await ModelAdapter(gateway).generate(ModelGenerateRequest(
            system_prompt='test', context_text='test context', user_input='test',
        ))
        assert result.model_dump() == {'success': False, 'text': '', 'error_code': 'API_ERROR'}
    async with VoiceGateway(v) as voice:
        try:
            await voice.transcribe(audio=buffer.getvalue(), media_type='audio/wav')
        except ServiceError as exc:
            assert exc.code == 'ASR_NOT_CONFIGURED'
        else:
            raise AssertionError('ASR did not reject missing key')
asyncio.run(check())
print('PASS non-root, no environment files, three missing-key errors and no output audio')
PY
docker exec "$container" python database/import_seed.py
docker exec "$container" python database/import_seed.py
snapshot="import sqlite3, os; db=sqlite3.connect(os.environ['ZHIYA_DB_PATH']); print(db.execute('select count(*) from storybooks').fetchone()[0])"
count=$(docker exec "$container" python -c "$snapshot")
test "$count" -gt 0
docker restart "$container"
test "$(docker exec "$container" python -c "$snapshot")" = "$count"
attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' "$container")" = healthy ]; do
    attempt=$((attempt + 1))
    test "$attempt" -lt 50
    sleep 1
done
docker exec "$container" python database/init_db.py --check
docker exec "$container" python -m pip --no-cache-dir check
printf 'PASS repeated seed import, restart persistence and Docker healthy\n'
