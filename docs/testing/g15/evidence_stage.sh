# Source from a shell with set -e. Publish only after all commands in a capture succeed.
begin_capture() {
    capture_dest=$1
    capture_marker="$capture_dest/.capture-status-$2"
    capture_dir=$(mktemp -d "$capture_dest/.capture.XXXXXX")
    trap 'rm -f -- "$capture_dir"/* "$capture_dir/.pending" "$capture_dir/.ready"; rmdir -- "$capture_dir"' EXIT
    trap 'exit 1' HUP INT TERM
}

publish_capture() {
    set -- "$capture_dir"/*
    [ -f "$1" ] || return 0
    for captured do
        if [ -d "$capture_dest/${captured##*/}" ]; then
            echo "Evidence target is a directory: ${captured##*/}" >&2
            return 1
        fi
    done
    # An interrupted multi-file rename must never be accepted as a completed capture.
    printf 'pending\n' > "$capture_dir/.pending"
    printf 'ready %s\n' "${capture_dir##*/}" > "$capture_dir/.ready"
    mv -T -- "$capture_dir/.pending" "$capture_marker"
    for captured do
        mv -T -- "$captured" "$capture_dest/${captured##*/}"
    done
    mv -T -- "$capture_dir/.ready" "$capture_marker"
}
