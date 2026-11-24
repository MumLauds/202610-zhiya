"""Publish a complete G15 report in the output filesystem."""
import json
import os
import tempfile
from pathlib import Path


def capture_versions(directory):
    versions = {}
    for marker in directory.glob(".capture-status-*"):
        value = marker.read_bytes()
        if not value.startswith(b"ready "):
            raise ValueError(f"Incomplete capture in {marker.name}, repeat the corresponding capture")
        versions[marker.name] = value
    return versions


def publish_json(path, report, *, replace=False):
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        pending = Path(staging) / "report.json"
        pending.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if replace:
            os.replace(pending, path)
        else:
            # Linking creates the final name atomically and fails if a report already exists.
            os.link(pending, path)
