"""Withdraw a static release receipt when either live container has drifted."""
import json
import re
import subprocess
import sys
from pathlib import Path


def check_receipt(path, project):
    if not path.exists():
        return
    try:
        record = json.loads(path.read_text())
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
        path.unlink()
        print(f"Withdrew stale release receipt: {error}", file=sys.stderr)


if __name__ == "__main__":
    check_receipt(Path(sys.argv[1]), sys.argv[2])
