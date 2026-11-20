#!/usr/bin/env bash
# Exercise the real timer entry point with a temporary release directory
# and stand-ins for Docker/GitHub. No network or production mounts.
set -euo pipefail
scripts=$(cd -- "$(dirname -- "$0")" && pwd)
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
export DEPLOY_DIR="$fixture" COMPOSE_PROJECT_NAME=test ZHIYA_DOMAIN=example.invalid
export ZHIYA_PROVIDER_ENV_FILE="$fixture/provider" ZHIYA_GATEWAY_PORT=18080
echo fixture > "$ZHIYA_PROVIDER_ENV_FILE"
sha=$(printf 'a%.0s' {1..40})
api="sha256:$(printf '1%.0s' {1..64})"
web_id="sha256:$(printf '2%.0s' {1..64})"
release="$fixture/releases/$sha"
mkdir -p "$release"
printf '%s\n' "$api" "$web_id" > "$release/images"
cp "$scripts/deploy.sh" "$scripts/backup.py" "$fixture/"

flock() { return 0; }
git() { printf '%s\trefs/heads/main\n' "$sha"; }
python3() {
    case "$1" in
        receipt.py) return 0 ;;
        gate.py)
            if [[ -f "$fixture/cancelled" ]]; then return 0; fi
            echo "$run_id" ;;
        *) return 1 ;;
    esac
}
docker() {
    case "$*" in
        "image inspect "*) echo "$sha" ;;
        *".Mounts"*) echo test_sqlite-data ;;
        *"stat -c"*) echo 10 ;;
        *"{{.Image}}"*"test-backend-1") echo "$api" ;;
        *"{{.Image}}"*"test-web-1") echo "$web_id" ;;
        *"python - create")
            echo backup >> "$fixture/backups"
            if [[ $scenario == failure ]]; then return 1; fi
            touch "$fixture/cancelled"
            echo /fixture/backup ;;
        *) echo "Unexpected docker operation: $*" >> "$fixture/unexpected"; return 1 ;;
    esac
}
# Each invocation gets the same errexit semantics as the systemd service.
invoke() { ( source "$fixture/deploy.sh" ); }
scenario=failure
run_id=11-1
set +e
invoke
failed=$?
set -e
[[ $failed != 0 && -f "$release/attempt-11-1" ]]
invoke
[[ $(wc -l < "$fixture/backups") -eq 1 ]]
scenario=cancel
run_id=12-1
invoke
[[ $(wc -l < "$fixture/backups") -eq 2 ]]
[[ ! -e "$fixture/pending" && ! -e "$fixture/public/current.json" && ! -e "$fixture/unexpected" ]]
