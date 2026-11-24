"""发布同一份 P6 报告的两种格式。"""

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


def publish_pair(first: Path, second: Path, outdir: Path) -> None:
    """从本轮临时目录发布两个文件，第二次替换失败时恢复第一个入口。"""
    first_entry = outdir / first.name
    backup = first.with_name(first.name + ".bak")
    with closing(sqlite3.connect(outdir / ".p6-report-lock.sqlite3", timeout=30)) as lock, lock:
        lock.execute("BEGIN IMMEDIATE")
        if first_entry.exists():
            shutil.copyfile(first_entry, backup)
        try:
            first.replace(first_entry)
            second.replace(outdir / second.name)
        except (OSError, KeyboardInterrupt):
            # replace 成功后源文件消失；中断可能发生在系统已换名、Python 尚未返回时。
            if not first.exists() and second.exists():
                if backup.exists():
                    backup.replace(first_entry)
                else:
                    first_entry.unlink()
            raise
