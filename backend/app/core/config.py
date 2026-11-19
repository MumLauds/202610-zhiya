import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def database_path() -> Path:
    default = ROOT / "database" / "instance" / "zhiya.sqlite3"
    return Path(os.environ.get("ZHIYA_DB_PATH", default)).resolve()
