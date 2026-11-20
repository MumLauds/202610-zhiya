"""生成 P6 缺陷跟踪表。

输入：
  defects_manual.json        人工确认的缺陷与缺口
  reports/acceptance-latest.json  最近一次黑盒验收结果

输出：
  reports/defect-ledger.md / defect-ledger.csv

自动失败会自动生成候选缺陷；已由人工缺陷覆盖的 BLOCKED/WARN 不重复造单。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from report_files import publish_pair

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "qa" / "p6" / "reports"
DEFAULT_MANUAL = Path(__file__).with_name("defects_manual.json")

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
STATUS_ORDER = {"待修复": 0, "修复中": 1, "待验证": 2, "已修复": 3, "已验证": 4, "不修复": 5}


def load_manual(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    defects = payload.get("defects") or []
    for defect in defects:
        for field in ("id", "title", "severity", "category", "ac", "status", "owner"):
            if field not in defect:
                raise SystemExit(f"缺陷 {defect.get('id', '?')} 缺少字段 {field}")
        defect.setdefault("relatedCases", [])
    return defects


def load_acceptance(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        raise ValueError("验收报告缺少有效的 results 列表")
    return report


def auto_defects(acceptance: dict[str, Any] | None) -> list[dict]:
    if not acceptance:
        return []
    created = []
    for result in acceptance.get("results", []):
        case_id = result.get("caseId") or result.get("case_id") or "UNKNOWN"
        if result.get("status") != "FAIL":
            continue
        created.append({
            "id": f"DEF-P6-AUTO-{case_id.removeprefix('TC-P6-')}",
            "title": f"自动验收失败：{result.get('title', '')}",
            "severity": "P1",
            "category": "自动化发现",
            "ac": result.get("ac", ""),
            "status": "待修复",
            "owner": "待指派",
            "foundAt": acceptance.get("meta", {}).get("finishedAt", ""),
            "environment": acceptance.get("meta", {}).get("baseUrl", ""),
            "description": result.get("detail", ""),
            "reproduce": f"运行 qa/p6/run_acceptance.py，用例 {case_id}",
            "expected": "用例通过",
            "actual": result.get("detail", ""),
            "evidence": json.dumps(result.get("evidence", {}), ensure_ascii=False)[:500],
            "fixVersion": "待定",
            "relatedCases": [case_id],
        })
    return created


def write_outputs(defects: list[dict], acceptance: dict | None, outdir: Path, report: Path) -> tuple[Path, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    defects = sorted(defects, key=lambda d: (SEVERITY_ORDER.get(d["severity"], 9),
                                             STATUS_ORDER.get(d["status"], 9), d["id"]))
    counts = Counter(defect["severity"] for defect in defects)
    status_counts = Counter(defect["status"] for defect in defects)
    status_by_case = {}
    if acceptance:
        status_by_case = {
            (item.get("caseId") or item.get("case_id")): item["status"]
            for item in acceptance.get("results", [])
        }

    csv_path = outdir / "defect-ledger.csv"
    fields = ["id", "title", "severity", "category", "ac", "status", "owner", "foundAt",
              "environment", "description", "reproduce", "expected", "actual", "evidence",
              "fixVersion", "relatedCases"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for defect in defects:
            row = {key: defect.get(key, "") for key in fields}
            row["relatedCases"] = ",".join(defect.get("relatedCases", []))
            writer.writerow(row)

    lines = [
        "# 知芽 P6 缺陷跟踪表",
        "",
        f"- 缺陷总数：{len(defects)}",
        "- 等级分布：" + "，".join(f"{level} {counts.get(level, 0)}" for level in ("P0", "P1", "P2")),
        "- 状态分布：" + "，".join(f"{status} {count}" for status, count in status_counts.items()),
        f"- 关联自动验收：{report.name if acceptance else '未运行'}",
        "",
        "> 缺陷编号 DEF-P6-xxx 为本轮测试建立；P0 = 阻断核心流程/严重越权，"
        "P1 = 重要功能失效但有回退，P2 = 文案、兼容性或工程细节。"
        "“待修复”表示本轮已确认但尚无修复版本，不等于已验收通过。",
        "",
        "## 缺陷清单",
        "",
        "| 编号 | 等级 | 标题 | 验收项 | 状态 | 负责人 | 关联用例 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for defect in defects:
        cases = defect.get("relatedCases", [])
        case_text = "、".join(
            f"{case}({status_by_case.get(case, '未运行')})" for case in cases
        ) or "-"
        lines.append(f"| {defect['id']} | {defect['severity']} | {defect['title']} | "
                     f"{defect['ac']} | {defect['status']} | {defect['owner']} | {case_text} |")

    lines += ["", "## 缺陷详情", ""]
    for defect in defects:
        lines += [
            f"### {defect['id']} {defect['title']}",
            "",
            f"- 等级 / 类别：{defect['severity']} / {defect['category']}",
            f"- 关联验收项：{defect['ac']}",
            f"- 状态 / 负责人：{defect['status']} / {defect['owner']}",
            f"- 发现日期 / 环境：{defect.get('foundAt', '')} / {defect.get('environment', '')}",
            f"- 复现步骤：{defect.get('reproduce', '')}",
            f"- 预期结果：{defect.get('expected', '')}",
            f"- 实际结果：{defect.get('actual', '')}",
            f"- 证据：{defect.get('evidence', '')}",
            f"- 修复版本：{defect.get('fixVersion', '待定')}",
            "",
        ]
    md_path = outdir / "defect-ledger.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, csv_path


def publish_ledger(defects: list[dict], acceptance: dict | None, outdir: Path, report: Path) -> tuple[Path, Path]:
    archives = outdir / "ledgers"
    archives.mkdir(parents=True, exist_ok=True)
    run_id = (acceptance or {}).get("meta", {}).get("runId") or ("legacy" if acceptance else "manual")
    label = re.sub(r"[^a-zA-Z0-9_-]", "_", str(run_id))[:80]
    with tempfile.TemporaryDirectory(dir=archives) as temporary:
        staging = Path(temporary)
        archive = archives / f"ledger-{label}-{staging.name}"
        md, csv_file = write_outputs(defects, acceptance, staging / "pair", report)
        md.parent.rename(archive)
        md, csv_file = archive / md.name, archive / csv_file.name
        # 固定入口继续提供完整台账；Markdown 中的两个链接始终属于同一份归档。
        links = (f"\n[本轮 Markdown]({md.relative_to(outdir).as_posix()}) · "
                 f"[本轮 CSV]({csv_file.relative_to(outdir).as_posix()})\n")
        latest_md, latest_csv = staging / md.name, staging / csv_file.name
        latest_md.write_text(md.read_text(encoding="utf-8") + links, encoding="utf-8")
        shutil.copyfile(csv_file, latest_csv)
        publish_pair(latest_csv, latest_md, outdir)
    return md, csv_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual", type=Path, default=DEFAULT_MANUAL)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--acceptance", type=Path, help="验收报告，默认读取输出目录中的 acceptance-latest.json")
    source.add_argument("--manual-only", action="store_true", help="只使用人工缺陷记录生成台账")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    report = args.acceptance or args.outdir / "acceptance-latest.json"
    manual = load_manual(args.manual)
    try:
        acceptance = None if args.manual_only else load_acceptance(report)
    except (OSError, ValueError) as exc:
        print(f"无法读取验收报告 {report}：{exc}", file=sys.stderr)
        return 2
    defects = manual + auto_defects(acceptance)
    md_path, csv_path = publish_ledger(defects, acceptance, args.outdir, report)
    print(f"缺陷 {len(defects)} 条：{md_path}")
    print(f"CSV：{csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
