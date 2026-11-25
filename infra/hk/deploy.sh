#!/usr/bin/env bash
# Host-installed entry point. Only application build contexts come from main.
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
root=$PWD
exec 9>release.lock
flock -n 9 || exit 0

: "${DEPLOY_DIR:?}" "${COMPOSE_PROJECT_NAME:?}" "${ZHIYA_PROVIDER_ENV_FILE:?}" "${ZHIYA_DOMAIN:?}" "${ZHIYA_GATEWAY_PORT:?}"
test -s "$ZHIYA_PROVIDER_ENV_FILE"
project=$COMPOSE_PROJECT_NAME
repo=https://github.com/MumLauds/202610-zhiya.git
backend="$project-backend-1"
web="$project-web-1"
install -d -m 0755 public
mkdir -p releases
receipt=public/current.json
last_success=last-success.json
image_id() { docker inspect --format '{{.Image}}' "$1"; }
db_volume() { docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/zhiya"}}{{.Name}}{{end}}{{end}}' "$backend"; }
db_file_id() { docker exec "$backend" stat -c '%d:%i' /var/lib/zhiya/zhiya.sqlite3; }
same_db() {
    [[ $1 =~ ^[0-9]+:[0-9]+$ && $(db_volume) == "${project}_sqlite-data" && $(db_file_id) == "$1" ]]
}
main_sha() { git ls-remote --exit-code "$repo" refs/heads/main | cut -f1; }
revision() { docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$1"; }
switch_images() {
    ZHIYA_API_IMAGE="$1" ZHIYA_WEB_IMAGE="$2" docker compose --env-file /dev/null \
        -p "$project" -f "$DEPLOY_DIR/compose.yaml" -f "$root/receipt.yaml" \
        up -d --no-deps --wait --wait-timeout 120 --pull never backend web
}
probe() {
    curl --silent --show-error --max-time 12 --resolve "$ZHIYA_DOMAIN:443:127.0.0.1" "https://$ZHIYA_DOMAIN$1" "${@:2}"
}
recover() {
    # pending is written atomically before either live container is replaced.
    local expected_file_id committed=false
    read -r target old_api old_web pending_run expected_file_id < pending
    if [[ -s "$last_success" ]] && jq -e --arg sha "$target" --arg run "$pending_run" \
        '.revision == $sha and .run == $run' "$last_success" >/dev/null; then
        committed=true
    else
        rm -f "$receipt" || return 1
        echo "Restoring images recorded before $target" >&2
        switch_images "$old_api" "$old_web" || return 1
        [[ $(image_id "$backend") == "$old_api" && $(image_id "$web") == "$old_web" ]] || return 1
    fi
    if ! same_db "$expected_file_id"; then
        rm -f "$receipt" || return 1
        mv -f pending history-hold || return 1
        echo "Cannot confirm original SQLite file during recovery; see history-hold" >&2
        return 1
    fi
    if [[ $committed == true ]]; then rm pending; return; fi
    probe /api/v1/health -f | jq -e '.data.status == "ok"' >/dev/null || return 1
    # A crash may have interrupted the candidate before its history check.
    # Always use the original baseline when completing an interrupted switch.
    local prior_backup
    read -r prior_backup < "releases/$target/attempt-$pending_run" || return 1
    if ! docker exec -i "$backend" python - verify "$prior_backup" < backup.py; then
        mv -f pending history-hold || return 1
        echo "History verification failed after rollback; see history-hold" >&2
        return 1
    fi
    rm pending
}
# Upgrade installations that published their verified receipt before this
# private commit record existed. Only adopt an identical release manifest.
if [[ ! -e "$last_success" && -s "$receipt" ]]; then
    legacy_sha=$(jq -er '.revision' "$receipt") || legacy_sha=''
    if [[ $legacy_sha =~ ^[0-9a-f]{40}$ ]] && cmp -s "$receipt" "releases/$legacy_sha/current.json"; then
        install -m 0600 "$receipt" last-success.tmp
        mv last-success.tmp "$last_success"
    fi
fi
if [[ -f pending ]]; then recover; fi
if [[ -e history-hold ]]; then
    rm -f "$receipt"
    echo "Automatic deployment paused; reconcile original backup recorded by history-hold" >&2
    exit 1
fi
# Reap only this script's abandoned scratch directories. The release lock is
# held, and no scratch directory for the current run has been allocated yet.
find . -mindepth 1 -maxdepth 1 -regextype posix-extended -type d \
    -regex '\./(git|build)\.zhiya-[A-Za-z0-9]{8}' -mmin +1440 \
    -exec rm -rf --one-file-system -- {} + || echo "Could not clean stale build directories" >&2
python3 receipt.py "$receipt" "$project" "$last_success"
sha=$(main_sha)
[[ $sha =~ ^[0-9a-f]{40}$ ]]
run=$(python3 gate.py "$sha")
[[ -n "$run" ]] || exit 0
[[ $run =~ ^[0-9]+-[0-9]+$ ]]
if [[ -s "$receipt" ]] && jq -e --arg sha "$sha" --arg run "$run" \
    '.revision == $sha and .run == $run' "$receipt" >/dev/null; then exit 0; fi
release="$root/releases/$sha"
mkdir -p "$release"
# A failed cutover needs a new validated run/attempt to try this SHA again.
if [[ -e "$release/attempt-$run" ]]; then exit 0; fi
api_tag="zhiya-api:main-$sha"
web_tag="zhiya-web:main-$sha"
if [[ ! -s "$release/images" ]]; then
    git_tmp=
    src=
    trap 'rm -rf -- "$git_tmp" "$src"' EXIT
    git_tmp=$(mktemp -d "$root/git.zhiya-XXXXXXXX")
    git init -q "$git_tmp"
    git -C "$git_tmp" fetch -q --depth=1 --no-tags "$repo" "$sha"
    fetched=$(git -C "$git_tmp" rev-parse --verify 'FETCH_HEAD^{commit}')
    [[ $fetched == "$sha" ]] || { echo "Fetched commit does not match $sha" >&2; exit 1; }
    src=$(mktemp -d "$root/build.zhiya-XXXXXXXX")
    git -C "$git_tmp" archive "$sha" infra backend database data/seed knowledge-base_v2.5/图片 frontend | tar -x -C "$src"
    rm -rf -- "$git_tmp"
    git_tmp=
    docker build --label "org.opencontainers.image.revision=$sha" -t "$api_tag" -f "$src/infra/Dockerfile" "$src"
    docker build --label "org.opencontainers.image.revision=$sha" -t "$web_tag" -f "$root/Web.Dockerfile" "$src/frontend"
    docker image inspect -f '{{.Id}}' "$api_tag" "$web_tag" > "$release/images.tmp"
    mv "$release/images.tmp" "$release/images"
    rm -rf -- "$src"
    trap - EXIT
fi
mapfile -t images < "$release/images"
[[ ${#images[@]} == 2 ]]
for id in "${images[@]}"; do
    [[ $id =~ ^sha256:[[:xdigit:]]{64}$ && $(revision "$id") == "$sha" ]]
done
[[ $(db_volume) == "${project}_sqlite-data" ]]
file_id=$(db_file_id)
[[ $file_id =~ ^[0-9]+:[0-9]+$ ]]
old_api=$(image_id "$backend")
old_web=$(image_id "$web")
previous=$(revision "$old_api")
[[ $previous =~ ^[0-9a-f]{40}$ && $(revision "$old_web") == "$previous" ]]
attempt="$release/attempt-$run"
# Claim this attempt before writing to the production volume, even on failure.
printf 'backup-started\n' > "$attempt"
backup=$(docker exec -i "$backend" python - create < backup.py)
[[ -n $backup ]]
printf '%s\n' "$backup" "$old_api" "$old_web" "$previous" > "$attempt"
latest=$(main_sha)
if [[ $latest != "$sha" ]]; then echo "Superseded $sha"; exit 0; fi
current_run=$(python3 gate.py "$sha")
if [[ $current_run != "$run" ]]; then echo "Run $run no longer authorizes release"; exit 0; fi
printf '%s %s %s %s %s\n' "$sha" "$old_api" "$old_web" "$run" "$file_id" > pending.tmp
mv pending.tmp pending
trap 'rc=$?; if [[ -f pending ]]; then recover || echo "Review recovery state and service journal" >&2; fi; exit "$rc"' EXIT
rm -f "$receipt"
switch_images "${images[@]}"
same_db "$file_id"
[[ $(image_id "$backend") == "${images[0]}" && $(image_id "$web") == "${images[1]}" ]]
for page in index.html mobile.html; do
    docker exec "$web" cat "/srv/www/$page" > "$release/$page"
    probe "/$page" -f | cmp - "$release/$page"
done
probe /api/v1/health -f | jq -e '.data.status == "ok"' >/dev/null
for path in /api/v1/storybooks /api/v1/admin/story-versions; do
    [[ $(probe "$path" -D "$release/headers" -o /dev/null -w '%{http_code}') == 401 ]]
    if grep -qi '^www-authenticate: *Basic' "$release/headers"; then exit 1; fi
done
if ! docker exec -i "$backend" python - verify "$backup" < backup.py; then
    cp pending history-hold.tmp
    mv history-hold.tmp history-hold
    exit 1
fi
jq -n --arg revision "$sha" --arg backend "${images[0]}" --arg web "${images[1]}" \
    --arg previous "$previous" --arg run "$run" '{ $revision, $backend, $web, $previous, $run }' > "$release/current.json"
install -m 0600 "$release/current.json" last-success.tmp
mv -f last-success.tmp "$last_success"
rm pending
trap - EXIT
python3 receipt.py "$receipt" "$project" "$last_success"
test -s "$receipt"
echo "Published $sha"
