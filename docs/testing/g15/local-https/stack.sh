#!/bin/sh
# G15 loopback rehearsal. The frontend bundle is created by prepare_frontend.py.
set -eu
action=${1:?build, up, start, restart, inspect, snapshot, baseline, or down}
state=${G15_STATE:?Set an absolute private Linux directory for this run}
project=${G15_PROJECT:?Set a unique Docker Compose project name}
revision=${G15_COMMIT:?Set the full tested Git SHA}
repo=$(cd "$(dirname "$0")/../../../.." && pwd)
testdir=$repo/docs/testing/g15/local-https
case "$state" in /*) ;; *) echo 'G15_STATE must be absolute' >&2; exit 2;; esac
export G15_WEB_IMAGE="g15-web:$project" G15_API_IMAGE="g15-backend:$project"
export G15_TEST_TLS="$state/tls"
dc() { docker compose --env-file /dev/null -p "$project" -f "$testdir/compose.yaml" "$@"; }
health() {
    for attempt in $(seq 1 45); do
        if response=$(curl --noproxy '*' --connect-timeout 2 --max-time 5 --silent --fail --cacert "$G15_TEST_TLS/cert.pem" https://127.0.0.1:18443/api/v1/health); then printf '%s\n' "$response"; return; fi
        sleep 1
    done
    echo 'HTTPS health did not become ready' >&2; return 1
}
certificate_pin() {
    openssl x509 -pubkey -noout -in "$G15_TEST_TLS/cert.pem" | openssl pkey -pubin -outform DER | openssl dgst -sha256 -binary | openssl base64 -A
}
case "$action" in
    build) umask 077; mkdir -p "$state" ;;
esac
case "$action" in
    build|up|start|restart|baseline|down)
        exec 9<"$state"
        flock -n 9 || { echo "Cannot acquire capture lock: $state" >&2; exit 1; }
        ;;
esac
if [ "$action" = build ]; then
    # A Git archive supplies only the named tracked files. G15_SOURCE_TAR is useful for Windows linked worktrees.
    if [ -e "$state/build-complete" ]; then
        echo 'Choose a fresh G15_STATE for a new build' >&2; exit 1
    fi
    builddir=$(mktemp -d "$state/build.XXXXXX")
    mkdir "$builddir/source" "$builddir/web"
    bundle=${G15_FRONTEND_BUILD:-$state/frontend-build}
    [ "$(cat "$bundle/commit")" = "$revision" ] || { echo 'Frontend commit mismatch or build incomplete' >&2; exit 1; }
    cp -R "$bundle/dist" "$builddir/web/dist"
    # Compare the complete copied file set, including files added after the build.
    (cd "$builddir/web"; find dist -type f -exec sha256sum {} +) > "$builddir/web-files"
    LC_ALL=C sort "$builddir/web-files" > "$builddir/web-sha256sums"
    cmp "$bundle/sha256sums" "$builddir/web-sha256sums"
    if [ -n "${G15_SOURCE_TAR:-}" ]; then
        # git archive's first PAX header carries the commit, also in a minimal Docker host without Git.
        actual=$(dd if="$G15_SOURCE_TAR" bs=512 skip=1 count=1 2>/dev/null | tr '\000' '\n' | sed -n 's/^[0-9]* comment=//p')
        [ "$actual" = "$revision" ] || { echo 'Archive commit mismatch' >&2; exit 1; }
        (umask 022; tar xf "$G15_SOURCE_TAR" -C "$builddir/source")
    else
        git -C "$repo" archive -o "$builddir/source.tar" "$revision" -- backend/app backend/requirements.txt backend/smoke_model.py backend/smoke_voice.py database data/seed knowledge-base_v2.5/图片 infra/Dockerfile
        (umask 022; tar xf "$builddir/source.tar" -C "$builddir/source")
    fi
    cp "$testdir/Caddyfile" "$testdir/Web.Dockerfile" "$builddir/web/"
    chmod -R a+rX "$builddir/web"
    docker build --pull=false --label "org.opencontainers.image.revision=$revision" -t "$G15_API_IMAGE" -f "$builddir/source/infra/Dockerfile" "$builddir/source"
    docker build --pull=false --label "org.opencontainers.image.revision=$revision" -t "$G15_WEB_IMAGE" -f "$builddir/web/Web.Dockerfile" "$builddir/web"
    mkdir "$G15_TEST_TLS"
    openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=127.0.0.1 \
        -addext 'subjectAltName=IP:127.0.0.1,DNS:supplier' -addext 'basicConstraints=critical,CA:TRUE' \
        -keyout "$G15_TEST_TLS/key.pem" -out "$G15_TEST_TLS/cert.pem" 2>"$state/cert-build.log"
    chmod 644 "$G15_TEST_TLS/cert.pem"
    printf '%s\n' "$project $revision" > "$state/build-complete"
    certificate_pin; echo
    exit 0
fi
[ "$(cat "$state/build-complete")" = "$project $revision" ] || { echo 'No completed build for this run' >&2; exit 1; }
case "$action" in
    restart|baseline)
        [ ! -e "$state/deployment-result.json" ] || { echo 'Baseline already published; use a fresh run' >&2; exit 1; }
        ;;
esac
G15_CERTIFI=$(docker run --rm --network none "$G15_API_IMAGE" python -c 'import certifi; print(certifi.where())')
export G15_CERTIFI
capture() (
    . "$testdir/../evidence_stage.sh"
    begin_capture "$state" "$1"
    phase=$1
    dc exec -T backend python /test/fixture.py deployment-snapshot /var/lib/zhiya/zhiya.sqlite3 > "$capture_dir/$phase-db.json"
    dc logs --no-color --no-log-prefix provider > "$capture_dir/$phase-provider.jsonl"
    for service in backend edge provider; do
        id=$(dc ps -q "$service")
        docker inspect --format '{"image":{{json .Image}},"reference":{{json .Config.Image}},"started":{{json .State.StartedAt}},"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}},"volume":"{{range .Mounts}}{{if eq .Destination "/var/lib/zhiya"}}{{.Name}}{{end}}{{end}}"}' "$id" > "$capture_dir/$phase-$service.json"
    done
    publish_capture
)
case "$action" in
    up)
        existing=$(docker volume ls -q)
        if printf '%s\n' "$existing" | grep -Fxq -- "${project}_sqlite-data"; then
            echo 'Select a fresh project for initialization; this project already has a SQLite volume' >&2; exit 1
        fi
        dc config --quiet
        dc run --rm --no-deps backend python database/init_db.py
        dc run --rm --no-deps backend python /test/fixture.py seed /var/lib/zhiya/zhiya.sqlite3
        dc up -d --pull never
        health ;;
    start)
        docker volume inspect "${project}_sqlite-data" >/dev/null
        dc up -d --pull never
        health ;;
    restart) capture beforeRestart; dc restart; health ;;
    baseline)
        browser_result=${2:?Provide the successful post-restart browser JSON path}
        [ -f "$browser_result" ] || { echo 'Browser result missing' >&2; exit 1; }
        browser_result=$(realpath "$browser_result")
        capture afterBrowserReadback
        docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
            --mount "type=bind,source=$testdir,target=/check,readonly" \
            --mount "type=bind,source=$browser_result,target=/browser.json,readonly" \
            --mount "type=bind,source=$state,target=/state" "$G15_API_IMAGE" \
            python /check/deployment_baseline.py /state /browser.json "$project" "$revision" "$(certificate_pin)"
        ;;
    inspect)
        for service in edge backend provider; do
            docker inspect --format '{{.Name}} container={{.Id}} image={{.Image}} started={{.State.StartedAt}} ports={{json .NetworkSettings.Ports}} {{range .Mounts}}{{if eq .Destination "/var/lib/zhiya"}}sqlite={{.Name}}{{end}}{{end}}' "$(dc ps -q "$service")"
        done
        docker image inspect --format '{{.Id}} {{json .RepoDigests}}' "$G15_API_IMAGE" "$G15_WEB_IMAGE" ;;
    snapshot)
        dc exec -T backend python /test/fixture.py snapshot /var/lib/zhiya/zhiya.sqlite3
        dc logs --no-log-prefix provider ;;
    down) dc down ;;
    *) echo 'Unknown action' >&2; exit 2 ;;
esac
