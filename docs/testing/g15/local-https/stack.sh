#!/bin/sh
# G15 loopback rehearsal. Run inside Linux; the frontend has already been built at the tested commit.
set -eu
action=${1:?build, up, start, restart, inspect, snapshot, or down}
state=${G15_STATE:?Set an absolute private Linux directory for this run}
project=${G15_PROJECT:?Set a unique Docker Compose project name}
revision=${G15_COMMIT:?Set the full tested Git SHA}
repo=$(cd "$(dirname "$0")/../../../.." && pwd)
testdir=$repo/docs/testing/g15/local-https
case "$state" in /*) ;; *) echo 'G15_STATE must be absolute' >&2; exit 2;; esac
export G15_WEB_IMAGE="g15-web:$revision" G15_API_IMAGE="g15-backend:$revision"
export G15_TEST_TLS="$state/tls"
dc() { docker compose --env-file /dev/null -p "$project" -f "$testdir/compose.yaml" "$@"; }
health() {
    for attempt in $(seq 1 45); do
        if curl --noproxy '*' --silent --fail --cacert "$G15_TEST_TLS/cert.pem" https://127.0.0.1:18443/api/v1/health; then echo; return; fi
        sleep 1
    done
    echo 'HTTPS health did not become ready' >&2; return 1
}
if [ "$action" = build ]; then
    # A Git archive supplies only the named tracked files. G15_SOURCE_TAR is useful for Windows linked worktrees.
    umask 077
    mkdir -p "$state"
    builddir=$(mktemp -d "$state/build.XXXXXX")
    mkdir "$builddir/source" "$builddir/web"
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
    cp -R "$repo/frontend/dist" "$builddir/web/dist"
    chmod -R a+rX "$builddir/web"
    docker build --pull=false --label "org.opencontainers.image.revision=$revision" -t "$G15_API_IMAGE" -f "$builddir/source/infra/Dockerfile" "$builddir/source"
    docker build --pull=false --label "org.opencontainers.image.revision=$revision" -t "$G15_WEB_IMAGE" -f "$builddir/web/Web.Dockerfile" "$builddir/web"
    if [ ! -e "$G15_TEST_TLS" ]; then
        mkdir "$G15_TEST_TLS"
        openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=127.0.0.1 \
            -addext 'subjectAltName=IP:127.0.0.1,DNS:supplier' -addext 'basicConstraints=critical,CA:TRUE' \
            -keyout "$G15_TEST_TLS/key.pem" -out "$G15_TEST_TLS/cert.pem" 2>"$state/cert-build.log"
    fi
    chmod 644 "$G15_TEST_TLS/cert.pem"
    openssl x509 -pubkey -noout -in "$G15_TEST_TLS/cert.pem" | openssl pkey -pubin -outform DER | openssl dgst -sha256 -binary | openssl base64 -A; echo
    exit 0
fi
G15_CERTIFI=$(docker run --rm --network none "$G15_API_IMAGE" python -c 'import certifi; print(certifi.where())')
export G15_CERTIFI
case "$action" in
    up)
        if docker volume inspect "${project}_sqlite-data" >/dev/null 2>&1; then
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
    restart) dc restart; health ;;
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
