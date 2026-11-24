"""汇总模型对比结果与两位评分人的评分。

没有真实结果或评分为空时，报告会明确写“未执行/待评分”，不会把空表算成通过。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TextIO

from report_files import publish_pair

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "qa" / "p6" / "reports" / "model-compare"


def latest_results(outdir: Path) -> Path | None:
    files = sorted(outdir.glob("results-*.jsonl"))
    return files[-1] if files else None


def load_results(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows.append(row)
    return rows


def grade_columns(dimensions: list[str]) -> list[str]:
    return [f"{rater}_{dimension}" for rater in ("rater1", "rater2") for dimension in dimensions]


def load_scores(path: Path, dimensions: list[str]) -> list[dict[str, Any]]:
    grades = grade_columns(dimensions)
    required = ["outputKey", "caseId", "condition", *grades]
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        missing = [key for key in required if key not in headers]
        if missing:
            raise ValueError(f"{path.name} 缺少评分表必需列：{', '.join(missing)}")
        duplicates = [key for key in required if headers.count(key) > 1]
        if duplicates:
            raise ValueError(f"{path.name} 评分表必需列重复：{', '.join(duplicates)}")
        for row in reader:
            if None in row:
                raise ValueError(f"{path.name} 第 {reader.line_num} 行的单元格数超过表头列数")
            if any(row[key] is None for key in required):
                raise ValueError(f"{path.name} 第 {reader.line_num} 行缺少必需单元格")
            parsed = dict(row)
            for key in grades:
                raw = row[key].strip()
                if raw and raw not in {"0", "1", "2"}:
                    raise ValueError(f"{path.name}：{row['caseId']} / {row['condition']} / {key}"
                                     f" 评分 {raw!r} 无效，请填写 0、1、2 或留空")
                parsed[key] = int(raw) if raw else None
            rows.append(parsed)
    return rows


def scoring_columns(dimensions: list[str]) -> list[str]:
    return ["caseId", "condition", "model", "outputExcerpt", *grade_columns(dimensions), "rater1_note", "rater2_note"]


def plan_fingerprint(plan: dict) -> str:
    inputs = {key: plan[key] for key in ("cases", "conditions", "dimensions")}
    canonical = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_issue(plan: dict, results: list[dict]) -> str | None:
    fingerprint = plan.get("planHash")
    if fingerprint and fingerprint != plan_fingerprint(plan):
        return "计划内容与其指纹不一致"
    if fingerprint and (plan.get("caseCount") != len(plan["cases"])
                        or plan.get("callBudget") != len(plan["cases"]) * len(plan["conditions"])):
        return "计划中的案例数或调用预算与评测范围不一致"
    if not results:
        return None
    if not fingerprint or any(not row.get("planHash") for row in results):
        return "旧计划或结果缺少计划指纹，原始输出仅供查阅；请用同一份新计划重新执行后评分"
    if any(row["planHash"] != fingerprint for row in results):
        return "结果与所选计划的指纹不一致，请指定生成该结果的计划"
    run_ids = {row.get("runId") for row in results}
    if len(run_ids) != 1 or not all(run_ids):
        return "结果必须来自同一轮有标识的运行"
    return None


def has_output(row: dict) -> bool:
    return row.get("success") is True and isinstance(row.get("output"), str) and bool(row["output"].strip())


def output_key(row: dict) -> str:
    fields = [row["planHash"], row["runId"], row["caseId"], row["condition"], row.get("model"), row["output"]]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode("utf-8")).hexdigest()


def eligible_outputs(plan: dict, results: list[dict]) -> dict:
    if not results or plan_issue(plan, results):
        return {}
    cases = {case["caseId"] for case in plan["cases"]}
    occurrences = Counter((row["caseId"], row["condition"]) for row in results)
    outputs = {}
    for row in results:
        key = (row["caseId"], row["condition"])
        if (occurrences[key] == 1 and has_output(row) and row["condition"] in plan["conditions"]
                and row["caseId"] in cases):
            outputs[key] = row
    return outputs


def publish_text(path: Path, write: Callable[[TextIO], None], *, encoding: str = "utf-8",
                 replace: bool = False) -> bool:
    """先写完整临时文件，再原子发布。默认保留已有文件。"""
    temporary = tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding=encoding,
                                           newline="", delete=False)
    try:
        with temporary as handle:
            write(handle)
        try:
            if replace:
                os.replace(temporary.name, path)
            elif os.name == "nt":
                os.rename(temporary.name, path)  # Windows 的 rename 遇到已存在的目标会失败。
            else:
                os.link(temporary.name, path)
        except FileExistsError:
            return False
        return True
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def publish_csv(path: Path, columns: list[str], rows: Iterable[dict]) -> bool:
    def write(handle: TextIO) -> None:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    return publish_text(path, write, encoding="utf-8-sig")


def score_sheet(outdir: Path, results_path: Path, outputs: dict, dimensions: list[str]) -> Path:
    columns = ["outputKey", *scoring_columns(dimensions)]
    rows = {}
    for output in outputs.values():
        key = output_key(output)
        rows[key] = {"outputKey": key, "caseId": output["caseId"], "condition": output["condition"],
                     "model": output.get("model"), "outputExcerpt": output["output"][:200]}
    digest = hashlib.sha256("".join(sorted(rows)).encode("ascii")).hexdigest()[:12]
    path = outdir / f"scoring-{results_path.stem.removeprefix('results-')}-{digest}.csv"
    # 旧版可能留下半张表。按输出标识保留已填的评分，将缺失行补到独立副本。
    for candidate in (path, path.with_stem(path.stem + "-recovered")):
        if not candidate.exists() and publish_csv(candidate, columns, rows.values()):
            return candidate
        with candidate.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is not None and reader.fieldnames != columns:
                raise ValueError(f"评分表列不匹配，请核对 {candidate}")
            seen = set()
            complete = True
            for saved in reader:
                if None in saved.values():
                    complete = False
                    if saved["condition"] is None:  # 写入在标识字段内中断，此行还没有评分。
                        continue
                key = saved["outputKey"]
                if (None in saved or key not in rows or key in seen or saved["caseId"] != rows[key]["caseId"]
                        or saved["condition"] != rows[key]["condition"]):
                    raise ValueError(f"评分行与原始输出不匹配，请核对 {candidate}")
                seen.add(key)
                rows[key].update({col: saved[col] for col in columns if col.startswith("rater")})
        if complete and seen == rows.keys():
            return candidate
    raise ValueError(f"恢复副本仍不完整，请核对 {candidate}")


def mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 3) if values else None


def summarize(plan: dict, results: list[dict], scores: list[dict], dimensions: list[str]) -> dict:
    conditions = plan["conditions"]
    by_condition: dict[str, dict[str, list[int]]] = {
        condition: {dimension: [] for dimension in dimensions} for condition in conditions
    }
    per_case_condition: dict[tuple[str, str], list[int]] = defaultdict(list)
    agreement = {dimension: {"exact": 0, "within1": 0, "total": 0} for dimension in dimensions}

    outputs = eligible_outputs(plan, results)
    grades = [f"{rater}_{d}" for rater in ("rater1", "rater2") for d in dimensions]
    graded = [row for row in scores if any(isinstance(row.get(g), int) for g in grades)]
    occurrences = Counter((row["caseId"], row["condition"]) for row in graded)
    scored_rows = 0
    for row in graded:
        key = (row["caseId"], row["condition"])
        output = outputs.get(key)
        if output is None or occurrences[key] != 1 or row.get("outputKey") != output_key(output):
            continue
        scored_rows += 1
        condition = row["condition"]
        case_id = row["caseId"]
        for rater in ("rater1", "rater2"):
            values = []
            for dimension in dimensions:
                value = row.get(f"{rater}_{dimension}")
                if isinstance(value, int):
                    by_condition.setdefault(condition, {}).setdefault(dimension, []).append(value)
                    values.append(value)
            if values and len(values) == len(dimensions):
                per_case_condition[(case_id, condition)].append(sum(values))
        for dimension in dimensions:
            first = row.get(f"rater1_{dimension}")
            second = row.get(f"rater2_{dimension}")
            if isinstance(first, int) and isinstance(second, int):
                agreement[dimension]["total"] += 1
                if first == second:
                    agreement[dimension]["exact"] += 1
                if abs(first - second) <= 1:
                    agreement[dimension]["within1"] += 1

    condition_means = {
        condition: {dimension: mean(values) for dimension, values in dims.items()}
        for condition, dims in by_condition.items()
    }
    for condition, dims in condition_means.items():
        values = [value for value in dims.values() if value is not None]
        dims["总均分"] = mean(values)

    pairs = []
    if "baseline" in conditions and "domain" in conditions:
        case_ids = sorted({case_id for case_id, _ in per_case_condition})
        for case_id in case_ids:
            base = per_case_condition.get((case_id, "baseline"))
            domain = per_case_condition.get((case_id, "domain"))
            if base and domain and len(base) == len(domain) == 2:
                if outputs[(case_id, "baseline")].get("model") != outputs[(case_id, "domain")].get("model"):
                    continue
                base_score = sum(base) / (2 * len(dimensions))
                domain_score = sum(domain) / (2 * len(dimensions))
                outcome = "胜" if domain_score > base_score else "平" if domain_score == base_score else "负"
                pairs.append({"caseId": case_id, "baseline": base_score,
                              "domain": domain_score, "outcome": outcome})
    wins = sum(1 for pair in pairs if pair["outcome"] == "胜")
    ties = sum(1 for pair in pairs if pair["outcome"] == "平")
    losses = sum(1 for pair in pairs if pair["outcome"] == "负")

    return {
        "caseCount": plan["caseCount"],
        "conditions": conditions,
        "callBudget": plan["callBudget"],
        "resultRows": len(results),
        "validOutputRows": len(outputs),
        "scoredRows": scored_rows,
        "unmatchedScoreRows": len(graded) - scored_rows,
        "conditionMeans": condition_means,
        "paired": {"wins": wins, "ties": ties, "losses": losses, "cases": pairs},
        "agreement": agreement,
    }


def export_outputs(results: list[dict], outdir: Path, source: Path | None) -> Path | None:
    if not results or source is None:
        return None
    path = outdir / f"outputs-for-scoring-{source.stem}.md"
    lines = ["# 模型原始输出（供评分）", ""]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in results:
        grouped[row["caseId"]].append(row)
    for case_id in sorted(grouped):
        rows = grouped[case_id]
        first = rows[0]
        lines += [f"## {case_id}（{first.get('evalId', '')}）", "",
                  f"- 任务：{first.get('task')}；年龄：{first.get('ageBand')}",
                  f"- 输入：{first.get('userInput')}", f"- 期望：{first.get('expected')}", ""]
        for row in rows:
            status = "成功" if has_output(row) else f"无有效输出（{row.get('errorCode') or '空输出'}）"
            lines += [f"### {row['condition']} / {status} / {row.get('latencyMs')} ms", "",
                      "```text", (row.get("output") or "").strip() or "（空输出）", "```", ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_markdown(summary: dict, outdir: Path, results_path: Path | None, dimensions: list[str],
                   scores_path: Path | None) -> Path:
    lines = ["# 模型对比汇总", "",
             f"- 评测案例：{summary['caseCount']} 例",
             f"- 条件：{', '.join(summary['conditions'])}",
             f"- 计划调用：{summary['callBudget']} 次",
             f"- 已记录结果：{summary['resultRows']} 条，可评分输出 {summary['validOutputRows']} 条",
             f"- 已评分行：{summary['scoredRows']} 行，另有 {summary['unmatchedScoreRows']} 行未匹配本轮有效输出",
             f"- 本轮评分表：{scores_path.name if scores_path else '待生成成功输出'}",
             f"- 原始结果：{results_path.name if results_path else '未执行'}", ""]
    if results_path is None:
        lines += ["> **执行状态：未进行真实模型调用。** 当前环境未提供 MODEL_API_KEY，"
                  "或尚未执行 `run_model_compare.py --live`。本文件不包含模型效果结论。", ""]
    elif not summary["validOutputRows"]:
        lines += ["> **执行状态：本轮没有可评分输出。** 请检查原始结果是否为空、调用失败或返回空文本。", ""]
    if not summary["scoredRows"]:
        lines += ["> **评分状态：待取得成功输出并由两位评分人填写对应评分表。** 空表不会被计为通过。", ""]

    lines += ["## 各条件均分（0～2 分）", "",
              "| 条件 | " + " | ".join(dimensions) + " | 总均分 |",
              "| --- | " + " | ".join("---:" for _ in dimensions) + " | ---: |"]
    for condition, dims in summary["conditionMeans"].items():
        values = " | ".join("-" if dims.get(dim) is None else f"{dims[dim]:.2f}" for dim in dimensions)
        total = dims.get("总均分")
        lines.append(f"| {condition} | {values} | {'-' if total is None else f'{total:.2f}'} |")
    lines += ["", "## 配对比较（领域条件 vs 通用模型）", ""]
    paired = summary["paired"]
    if paired["cases"]:
        lines += [f"- 领域胜：{paired['wins']} 例；平：{paired['ties']} 例；负：{paired['losses']} 例。"]
        lines += ["", "| 案例 | 通用 | 领域 | 结果 |", "| --- | ---: | ---: | --- |"]
        for pair in paired["cases"]:
            lines.append(f"| {pair['caseId']} | {pair['baseline']:.2f} | {pair['domain']:.2f} | {pair['outcome']} |")
    else:
        lines.append("本轮暂无可配对的完整评分。")
    lines += ["", "## 评分一致性", "",
              "| 维度 | 完全一致 | 相差≤1 | 有效对数 |", "| --- | ---: | ---: | ---: |"]
    for dimension in dimensions:
        item = summary["agreement"][dimension]
        lines.append(f"| {dimension} | {item['exact']} | {item['within1']} | {item['total']} |")
    lines += ["", "> 评分仅针对已保存的输出，不承诺重复生成仍相同；"
              "模型接口成功不等于内容安全或适龄验收。", ""]
    path = outdir / "model-compare-summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--scores", type=Path)
    args = parser.parse_args(argv)
    outdir = args.outdir
    for supplied in (args.results, args.scores):
        if supplied is not None and not supplied.is_file():
            raise SystemExit(f"找不到指定文件：{supplied}")
    plan_path = args.plan or outdir / "model-compare-plan.json"
    if not plan_path.exists():
        raise SystemExit(f"缺少计划文件：{plan_path}；请先运行 run_model_compare.py")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    outdir.mkdir(parents=True, exist_ok=True)
    dimensions = plan.get("dimensions") or []
    results_path = args.results or latest_results(outdir)
    results = load_results(results_path)
    issue = plan_issue(plan, results)
    if issue:
        print(issue, file=sys.stderr)
        raw = export_outputs(results, outdir, results_path)
        if raw:
            print(f"原始输出：{raw}")
        return 2
    outputs = eligible_outputs(plan, results)
    scores_path = args.scores
    try:
        if scores_path is None and outputs:
            scores_path = score_sheet(outdir, results_path, outputs, dimensions)
        scores = load_scores(scores_path, dimensions) if scores_path else []
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    summary = summarize(plan, results, scores, dimensions)
    outputs = export_outputs(results, outdir, results_path)
    try:
        with tempfile.TemporaryDirectory(dir=outdir) as temporary:
            staging = Path(temporary)
            summary_path = staging / "model-compare-summary.json"
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            markdown = write_markdown(summary, staging, results_path, dimensions, scores_path)
            publish_pair(summary_path, markdown, outdir)
    except (OSError, sqlite3.Error) as exc:
        print(f"无法发布模型汇总：{exc}", file=sys.stderr)
        return 2
    if scores_path:
        print(f"本轮评分表：{scores_path}")
    print(f"汇总：{outdir / markdown.name}")
    print(f"JSON：{outdir / summary_path.name}")
    if outputs:
        print(f"原始输出：{outputs}")
    print(f"结果 {len(results)} 条，已评分 {summary['scoredRows']} 行")
    return 1 if results_path is not None and not summary["validOutputRows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
