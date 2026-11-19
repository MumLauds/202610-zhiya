#!/bin/sh
# Run in the Linux Docker host. No application image is rebuilt.
set -eu
umask 077
action=${1:?Use backup-a, restore-a, start-a, probe-a, down-a, seed-b, start-b-source, probe-b-source, stop-b-source, backup-b, restore-b, start-b, probe-b, down-b, or original}
here=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
web="$here/../local-https"
run=${G15_RESTORE_RUN:?Set a new lowercase project prefix}
out=${G15_EVIDENCE:?Set a private absolute directory for evidence}
tls=${G15_TEST_TLS:?Set the previously tested local certificate directory}
case "$run" in ''|*[!a-z0-9-]*) echo 'Invalid project prefix' >&2; exit 2;; esac
mkdir -p "$out"
baseline=${G15_BASELINE:-$web/deployment-result.json}
if command -v python3 >/dev/null 2>&1; then
    set -- python3
else
    reader=${G15_BASELINE_READER:?Set the local backend image to read the baseline without host Python}
    set -- docker run --pull never --rm --read-only --network none -i --entrypoint python "$reader"
fi
inputs=$("$@" -c '
import json, sys

record = json.load(sys.stdin)
fields = [record["containers"]["backend"]["persistentSqliteVolume"]]
for role in ("backend", "web"):
    image = record["images"][role]
    reference = image.get("reference") or next(iter(image.get("repoDigests") or ()), image["id"])
    fields.extend((reference, image["id"]))
if any(not field or any(c.isspace() for c in field) for field in fields):
    raise ValueError("Invalid volume or image reference in deployment baseline")
print(*fields)
' < "$baseline")
read -r original api api_id edge edge_id <<EOF
$inputs
EOF
[ "$(docker image inspect --format '{{.Id}}' "$api")" = "$api_id" ] || { echo 'Backend image differs from baseline' >&2; exit 1; }
[ "$(docker image inspect --format '{{.Id}}' "$edge")" = "$edge_id" ] || { echo 'Web image differs from baseline' >&2; exit 1; }
export G15_WEB_IMAGE="$edge" G15_API_IMAGE="$api" G15_TEST_TLS="$tls"
G15_CERTIFI=$(docker run --pull never --rm --network none "$api" python -c 'import certifi; print(certifi.where())')
export G15_CERTIFI
fresh() {
    if docker volume inspect "$1" >/dev/null 2>&1; then echo "Expected a new volume: $1" >&2; exit 1; fi
    docker volume create "$1" >/dev/null
}
stopped() {
    docker volume inspect "$1" >/dev/null
    writers=$(docker ps -q --filter "volume=$1") || { echo "Cannot check writers for $1" >&2; exit 1; }
    [ -z "$writers" ] || { echo "Stop the writer before backup: $1" >&2; exit 1; }
}
run_bundle() {
    operation=$1; source=$2; destination=$3; output=$4
    docker volume inspect "$source" >/dev/null
    set -- --mount "type=volume,source=$source,target=/src,readonly,volume-nocopy"
    if [ -n "$destination" ]; then
        fresh "$destination"
        set -- "$@" --mount "type=volume,source=$destination,target=/dst,volume-nocopy"
    fi
    docker run --pull never --rm --network none --user 0:0 "$@" \
        --mount "type=bind,source=$here,target=/ops,readonly" \
        --mount "type=bind,source=$web/fixture.py,target=/test/fixture.py,readonly" \
        "$api" python /ops/volume_bundle.py "$operation" > "$out/$output.json"
}
dc() { docker compose --env-file /dev/null -p "$project" -f "$web/compose.yaml" -f "$here/restore-compose.yaml" "$@"; }
health() {
    for attempt in $(seq 1 45); do
        if curl --noproxy '*' --silent --fail --cacert "$tls/cert.pem" https://127.0.0.1:18443/api/v1/health; then echo; return; fi
        sleep 1
    done
    echo 'Loopback HTTPS failed' >&2; return 1
}
start_service() {
    docker volume inspect "$G15_VOLUME" >/dev/null
    dc up --detach --pull never
    health > "$out/$1-health.json"
    for service in backend edge provider; do
        id=$(dc ps -q "$service")
        docker inspect --format '{"image":{{json .Image}},"startedAt":{{json .State.StartedAt}},"ports":{{json .NetworkSettings.Ports}},"volume":"{{range .Mounts}}{{if eq .Destination "/var/lib/zhiya"}}{{.Name}}{{end}}{{end}}"}' "$id" > "$out/$1-$service.json"
    done
    [ "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/zhiya"}}{{.Name}}{{end}}{{end}}' "$(dc ps -q backend)")" = "$G15_VOLUME" ]
    docker network inspect --format '{{.Internal}}' "${project}_business" > "$out/$1-network.txt"
}
case "$action" in
    *-b-source|seed-b) project="$run-b-source"; export G15_VOLUME="$run-b-source-data" G15_MEDIA_ROOT=/var/lib/zhiya/fixture-source;;
    *-b) project="$run-b-restored"; export G15_VOLUME="$run-b-restored-data" G15_MEDIA_ROOT=;;
    *) project="$run-a-restored"; export G15_VOLUME="$run-a-restored-data" G15_MEDIA_ROOT=;;
esac
case "$action" in
    backup-a) stopped "$original"; run_bundle backup "$original" "$run-a-backup" a-backup;;
    restore-a) run_bundle restore "$run-a-backup" "$G15_VOLUME" a-restore;;
    start-a) start_service a-service;;
    probe-a|probe-b|probe-b-source)
        dc exec -T backend python /test/fixture.py deployment-snapshot /var/lib/zhiya/zhiya.sqlite3 > "$out/$action.json"
        dc logs --no-log-prefix provider > "$out/$action-supplier.txt"
        ;;
    down-a|stop-b-source) dc down;;
    down-b)
        dc down
        date -u '+%Y-%m-%dT%H:%M:%SZ' > "$out/b-stopped-at.txt"
        stopped "$run-b-source-data"; run_bundle probe "$run-b-source-data" '' b-source-after
        stopped "$G15_VOLUME"; run_bundle probe "$G15_VOLUME" '' b-target-after
        ;;
    original) stopped "$original"; run_bundle probe "$original" '' original-after;;
    seed-b)
        fresh "$G15_VOLUME"
        dc run --rm --no-deps --pull never backend python database/init_db.py
        dc run --rm --no-deps --pull never backend python /test/fixture.py seed /var/lib/zhiya/zhiya.sqlite3
        dc run --rm --no-deps --pull never -v "$here/media_fixture.py:/test/media_fixture.py:ro" backend python /test/media_fixture.py /var/lib/zhiya/zhiya.sqlite3 > "$out/b-image.json"
        ;;
    start-b-source) start_service b-source-service;;
    backup-b) stopped "$run-b-source-data"; run_bundle backup "$run-b-source-data" "$run-b-backup" b-backup;;
    restore-b) run_bundle restore "$run-b-backup" "$G15_VOLUME" b-restore;;
    start-b)
        start_service b-restored-service
        dc exec -T backend python -c 'import json; from pathlib import Path; from app.services.story_media import ensure_media_dependencies; root,_=ensure_media_dependencies(); evidence={"fixtureSourceExists":Path("/var/lib/zhiya/fixture-source").exists(),"imageAvailableInMediaRoot":(root/"g15-restore-paper-boat.png").exists()}; assert not any(evidence.values()), evidence; print(json.dumps(evidence))' > "$out/b-restored-origin.json"
        ;;
    *) echo 'Unknown action' >&2; exit 2;;
esac
