"""On-disk encoding shared by the G15 backup writer and evidence collector."""
import json


def manifest_bytes(manifest):
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
