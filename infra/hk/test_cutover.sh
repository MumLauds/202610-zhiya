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
echo baseline > "$fixture/live.db"
sha=$(printf 'a%.0s' {1..40})
api="sha256:$(printf '1%.0s' {1..64})"
web_id="sha256:$(printf '2%.0s' {1..64})"
release="$fixture/releases/$sha"
mkdir -p "$release"
printf '%s\n' "$api" "$web_id" > "$release/images"
cp "$scripts/deploy.sh" "$scripts/backup.py" "$fixture/"

flock() { return 0; }
git() {
    [[ $1 == ls-remote ]] || { echo "Unexpected Git operation: $*" >> "$fixture/unexpected"; return 1; }
    printf '%s\trefs/heads/main\n' "$sha"
}
python3() {
    case "$1" in
        receipt.py)
            if [[ -s $4 ]]; then cp "$4" "$2"; fi ;;
        gate.py)
            if [[ -f "$fixture/cancelled" ]]; then return 0; fi
            echo "$run_id" ;;
        *) return 1 ;;
    esac
}
fixture_docker() {
    case "$*" in
        "image inspect "*) echo "$sha" ;;
        *".Mounts"*) echo test_sqlite-data ;;
        *"stat -c"*) stat -c '%d:%i' "$fixture/live.db" ;;
        *"{{.Image}}"*"test-backend-1") echo "$api" ;;
        *"{{.Image}}"*"test-web-1") echo "$web_id" ;;
        *"python - create")
            echo backup >> "$fixture/backups"
            if [[ $scenario == failure ]]; then return 1; fi
            if [[ $scenario == cancel ]]; then touch "$fixture/cancelled"; fi
            echo /fixture/backup ;;
        "compose "*)
            echo switch >> "$fixture/switches"
            if [[ -f "$fixture/replacement.db" ]]; then
                mv "$fixture/replacement.db" "$fixture/live.db"
            elif [[ -f "$fixture/rollback-unavailable" ]]; then
                return 1
            fi ;;
        *"cat /srv/www/"*) echo html ;;
        *"python - verify /fixture/backup") echo verify >> "$fixture/verifications"; [[ $scenario != history ]] ;;
        *) echo "Unexpected docker operation: $*" >> "$fixture/unexpected"; return 1 ;;
    esac
}
docker() { fixture_docker "$@"; }
curl() {
    case "$*" in
        *html*) echo html ;;
        *health*) echo '{"data":{"status":"ok"}}' ;;
        *storybooks*|*story-versions*) touch "$release/headers"; printf 401 ;;
        *) return 1 ;;
    esac
}
jq() {
    [[ $* == *'.data.status'* ]] || return 1
    grep -q '"ok"'
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

# A failed DB check rolls back the images, and the next run cannot replace
# the baseline even if Docker reports the previous application as healthy.
rm "$fixture/cancelled"
scenario=history
run_id=13-1
set +e
invoke
failed=$?
set -e
[[ $failed != 0 && -f "$fixture/history-hold" && ! -e "$fixture/pending" ]]
[[ $(wc -l < "$fixture/switches") -eq 2 && $(wc -l < "$fixture/verifications") -eq 2 ]]
read -r held_sha _ _ held_run _ < "$fixture/history-hold"
[[ $held_sha == "$sha" && $held_run == 13-1 ]]
[[ $(head -n 1 "$release/attempt-$held_run") == /fixture/backup ]]
run_id=14-1
set +e
invoke
failed=$?
set -e
[[ $failed != 0 && ! -e "$release/attempt-$run_id" && ! -e "$fixture/public/current.json" ]]
[[ $(wc -l < "$fixture/backups") -eq 3 && ! -e "$fixture/unexpected" ]]

# An identical backup copy in the same volume has the expected rows, but
# cannot prove that writes after the backup survived. Test both recovery paths.
scenario=replaced
for run_id in 15-1 16-1; do
    rm "$fixture/history-hold"
    original_id=$(stat -c '%d:%i' "$fixture/live.db")
    cp "$fixture/live.db" "$fixture/replacement.db"
    if [[ $run_id == 16-1 ]]; then touch "$fixture/rollback-unavailable"; fi
    set +e
    invoke
    failed=$?
    set -e
    [[ $failed != 0 && $(stat -c '%d:%i' "$fixture/live.db") != "$original_id" ]]
    if [[ $run_id == 16-1 ]]; then
        [[ -s "$fixture/pending" && ! -e "$fixture/history-hold" ]]
        rm "$fixture/rollback-unavailable"
        set +e
        invoke
        failed=$?
        set -e
        [[ $failed != 0 ]]
    fi
    read -r _ _ _ held_run held_file_id < "$fixture/history-hold"
    [[ $held_run == "$run_id" && $held_file_id == "$original_id" ]]
    [[ ! -e "$fixture/pending" && ! -e "$fixture/public/current.json" ]]
done

# A pending switch can recover normally when the file is unchanged.
rm "$fixture/history-hold"
touch "$fixture/cancelled"
printf '%s %s %s %s %s\n' "$sha" "$api" "$web_id" "$run_id" "$(stat -c '%d:%i' "$fixture/live.db")" > "$fixture/pending"
invoke
[[ ! -e "$fixture/pending" && ! -e "$fixture/history-hold" ]]

# A pending switch from the previous script has no recorded file identity.
printf '%s %s %s %s\n' "$sha" "$api" "$web_id" "$run_id" > "$fixture/pending"
set +e
invoke
failed=$?
set -e
[[ $failed != 0 && -s "$fixture/history-hold" && ! -e "$fixture/pending" && ! -e "$fixture/unexpected" ]]

# Exercise cold builds with real local Git transport and tar. Never allow a
# network protocol, even if the deployment URL changes. Docker stays mocked.
(
origin="$fixture/origin"
fixture="$fixture/cold"
mkdir -p "$fixture"
export DEPLOY_DIR="$fixture" ZHIYA_PROVIDER_ENV_FILE="$fixture/provider"
echo fixture > "$fixture/provider"; echo baseline > "$fixture/live.db"
cp "$scripts/deploy.sh" "$scripts/backup.py" "$fixture/"
export GIT_ALLOW_PROTOCOL=file GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
command git init -q -b main "$origin"
for dir in infra backend database data/seed knowledge-base_v2.5/图片 frontend; do
    mkdir -p "$origin/$dir"; echo note > "$origin/$dir/marker"
done
echo excluded > "$origin/unrelated"
upstream_commit() {
    echo "$1" > "$origin/frontend/marker"
    command git -C "$origin" add .
    command git -C "$origin" -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm "$1"
    command git -C "$origin" rev-parse HEAD
}
sha=$(upstream_commit first)
release="$fixture/releases/$sha"
operations="$fixture/operations"
stage() {
    echo "$1" >> "$operations"
    [[ $scenario != "$1" ]] || return 23
}
git() {
    case " $* " in
        *" init "*) stage init ;;
        *" fetch "*) stage fetch; [[ " $* " == *" --depth=1 "* && " $* " == *" --no-tags "* && ${!#} == "$sha" ]] ;;
        *" rev-parse "*) stage verify; if [[ $scenario == mismatch ]]; then echo "$wrong_sha"; return; fi ;;
        *" archive "*)
            if [[ $scenario == archive ]]; then
                # A valid tar stream followed by a Git error must still fail (pipefail).
                command git "$@"
                echo archive-failed > "$fixture/injected"
                return 23
            fi
            stage archive ;;
    esac
    command git -c "url.file://$origin.insteadOf=https://github.com/MumLauds/202610-zhiya.git" "$@"
}
tar() { command tar "$@"; stage tar; }
docker() {
    case "$*" in
        "build "*)
            [[ -z $(find "$fixture" -maxdepth 1 -name 'git.*' -print -quit) ]]
            local context=${!#}
            if [[ $* == *" -t zhiya-api:"* ]]; then
                [[ ! -e $context/.git && ! -e $context/unrelated ]]
                for dir in infra backend database data/seed knowledge-base_v2.5/图片 frontend; do
                    [[ -f "$context/$dir/marker" ]]
                done
                stage backend
            else
                [[ $(cat "$context/marker") == "$(command git -C "$origin" show "$sha:frontend/marker")" ]]
                stage frontend
            fi ;;
        "image inspect "*"{{.Id}}"*) printf '%s\n' "$new_api" "$new_web" ;;
        "image inspect "*) local id=${!#}; echo "${id: -40}" ;;
        *"{{.Image}}"*) cat "$fixture/${!#}" ;;
        *"cat /srv/www/"*) if [[ $scenario == page-mismatch ]]; then echo stale; else echo html; fi ;;
        "compose "*) echo "$ZHIYA_API_IMAGE" > "$fixture/test-backend-1"; echo "$ZHIYA_WEB_IMAGE" > "$fixture/test-web-1" ;;
        *) fixture_docker "$@" ;;
    esac
}
jq() { command jq "$@"; }
# Use distinct image IDs and revision labels for each simulated deployment.
wrong_sha=$(printf '0%.0s' {1..40})
prefix_api="sha256:$(printf '1%.0s' {1..24})"
prefix_web="sha256:$(printf '2%.0s' {1..24})"
echo "$prefix_api$wrong_sha" > "$fixture/test-backend-1"
echo "$prefix_web$wrong_sha" > "$fixture/test-web-1"
new_api="$prefix_api$sha"; new_web="$prefix_web$sha"; run_id=20-1
for scenario in init fetch mismatch archive tar backend frontend; do
    : > "$operations"
    set +e; invoke; failed=$?; set -e
    expected=$scenario; if [[ $scenario == mismatch ]]; then expected=verify; fi
    [[ $failed != 0 ]]
    if [[ $scenario == archive ]]; then
        [[ -s "$fixture/injected" && $(tail -1 "$operations") == tar ]]
    else
        [[ $(tail -1 "$operations") == "$expected" ]]
    fi
    [[ ! -e "$fixture/source.git" && ! -e "$release/images" && ! -e "$fixture/backups" ]]
    [[ -z $(find "$fixture" -maxdepth 1 \( -name 'git.*' -o -name 'build.*' \) -print -quit) ]]
done
# Existing source.git, including its keepalive refs and objects, is read-only
# evidence here; compare its whole file set and contents across releases.
command git clone -q --bare "$origin" "$fixture/source.git"
command git --git-dir="$fixture/source.git" update-ref refs/keepalive/last "$sha"
legacy_digest() { (cd "$fixture/source.git"; find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum); }
before=$(legacy_digest)
previous=$wrong_sha; scenario=success
for label in first second third; do
    if [[ $label != first ]]; then sha=$(upstream_commit "$label"); fi
    new_api="$prefix_api$sha"; new_web="$prefix_web$sha"; release="$fixture/releases/$sha"
    : > "$operations"
    invoke
    [[ $(cat "$operations") == $'init\nfetch\nverify\narchive\ntar\nbackend\nfrontend' ]]
    [[ $(cat "$release/images") == "$(printf '%s\n' "$new_api" "$new_web")" ]]
    command jq -e --arg sha "$sha" --arg prev "$previous" '.revision == $sha and .previous == $prev' "$fixture/last-success.json" >/dev/null
    cmp "$fixture/last-success.json" "$fixture/public/current.json"
    [[ -z $(find "$fixture" -maxdepth 1 \( -name 'git.*' -o -name 'build.*' \) -print -quit) ]]
    [[ $(legacy_digest) == "$before" ]]
    previous=$sha
done
# A newly authorized attempt for the same built SHA goes through cutover but
# neither allocates a work directory nor fetches/rebuilds images.
run_id=21-1; : > "$operations"
mktemp() { echo unexpected-mktemp >> "$operations"; return 1; }
invoke
[[ ! -s "$operations" && -s "$release/attempt-21-1" && $(legacy_digest) == "$before" ]]
unset -f mktemp
# A failed fourth SHA must restore the actual previous SHA, not candidate tags.
sha=$(upstream_commit rejected); release="$fixture/releases/$sha"
new_api="$prefix_api$sha"; new_web="$prefix_web$sha"; scenario=page-mismatch
set +e; invoke; failed=$?; set -e
[[ $failed != 0 && ! -e "$fixture/pending" && ! -e "$fixture/history-hold" ]]
[[ $(cat "$fixture/test-backend-1") == "$prefix_api$previous" && $(cat "$fixture/test-web-1") == "$prefix_web$previous" ]]
command jq -e --arg prev "$previous" '.revision == $prev' "$fixture/last-success.json" >/dev/null
[[ ! -e "$fixture/public/current.json" && $(legacy_digest) == "$before" ]]
echo "Temporary source: 7 build failures, 3 distinct releases, cache reuse and failed-cutover rollback passed."
)
