"""Serve the last committed receipt only while both running images match it."""
import json
import re
import subprocess
import sys
from pathlib import Path


def check_receipt(path, project, last_success):
    if not last_success.exists():
        path.unlink(missing_ok=True)
        return
    try:
        content = last_success.read_bytes()
        record = json.loads(content)
        sha = record["revision"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("invalid revision")
        for service in ("backend", "web"):
            image = record[service]
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                raise ValueError("invalid image ID")
            state = subprocess.check_output([
                "docker", "inspect", "--type", "container", "--format",
                '{{.State.Running}} {{.Image}} {{index .Config.Labels "org.opencontainers.image.revision"}}',
                f"{project}-{service}-1",
            ], text=True).strip()
            if state != f"true {image} {sha}":
                raise ValueError(f"{service} running state, image ID or revision differs")
    except (KeyError, TypeError, ValueError, subprocess.CalledProcessError, OSError) as error:
        path.unlink(missing_ok=True)
        print(f"Withdrew stale release receipt: {error}", file=sys.stderr)
        return
    if not path.exists() or path.read_bytes() != content:
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.chmod(0o644)
        temporary.replace(path)


if __name__ == "__main__":
    check_receipt(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
