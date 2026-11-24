"""P6 复测证据回归，使用临时文件、独立 Git 仓库和离线服务替身。"""

import copy
import csv
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest

import build_test_doc
import common
from report_files import publish_pair
import prepare_demo_data
import run_acceptance
import run_model_compare as runner
import run_p6
import summarize_model_compare as scoring
import track_defects


def test_candidate_rebuilds_after_commit_change_and_rejects_stale_browser_script(tmp_path, monkeypatch):
    from course_rehearsal import build_candidate_frontend, open_candidate

    repo = tmp_path / "repo"
    frontend = repo / "frontend"
    frontend.mkdir(parents=True)
    (frontend / ".gitignore").write_text("dist/\n", encoding="utf-8")
    source = frontend / "main.js"

    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=P6", "-c", "user.email=p6@example.invalid",
                                        "-c", "commit.gpgsign=false", *args], cwd=repo).decode().strip()

    git("init", "-q")
    source.write_bytes(b"first")
    git("add", ".")
    git("commit", "-qm", "first")
    first = git("rev-parse", "HEAD")
    stale = frontend / "dist/entry.js"
    stale.parent.mkdir()
    stale.write_bytes(source.read_bytes())
    source.write_bytes(b"second")
    git("commit", "-qam", "second")
    second = git("rev-parse", "HEAD")
    assert not git("status", "--porcelain")

    # Only replace Vite. Keep the two real commits and the ignored old build on disk.
    run = subprocess.run
    def run_build(args, **kwargs):
        if args[0] != "node":
            return run(args, **kwargs)
        assert args[1:7] == ["node_modules/vite/bin/vite.js", "build", "--mode", "public", "--manifest", "--outDir"]
        output = Path(args[7])
        (output / ".vite").mkdir()
        (output / ".vite/manifest.json").write_text('{"index.html":{"file":"entry.js"}}', encoding="utf-8")
        (output / "entry.js").write_bytes((kwargs["cwd"] / "main.js").read_bytes())

    monkeypatch.setattr(subprocess, "run", run_build)
    dist = tmp_path / "fresh"
    with pytest.raises(ValueError, match="候选提交"):
        build_candidate_frontend(repo, first, dist)
    entry = build_candidate_frontend(repo, second, dist)
    assert entry == {"path": "/entry.js", "sha256": hashlib.sha256(b"second").hexdigest()}
    assert stale.read_bytes() == b"first"
    page = MagicMock()
    response = page.expect_response.return_value.__enter__.return_value.value
    response.status = 200
    response.body.return_value = stale.read_bytes()
    with pytest.raises(ValueError, match="入口脚本"):
        open_candidate(page, "https://localhost", entry)
    response.body.return_value = (dist / "entry.js").read_bytes()
    assert open_candidate(page, "https://localhost", entry) == entry


def test_answer_audio_checks_both_event_and_spoken_words():
    from course_rehearsal import verify_answer_audio

    answer = {"interactionEventId": 12, "outputText": "小狐狸站在门口，看着外面的雨。"}
    request = {"interactionEventId": 12}
    verify_answer_audio(answer, request, "小狐狸站在门口 看着外面的雨！")
    with pytest.raises(AssertionError, match="转写"):
        verify_answer_audio(answer, request, "青蛙在池塘边唱歌。")
    with pytest.raises(AssertionError, match="绑定"):
        verify_answer_audio(answer, {"interactionEventId": 13}, answer["outputText"])


def test_retest_report_preserves_archive_and_previous_run(tmp_path, monkeypatch):
    record = tmp_path / "acceptance-latest.json"
    args = ["--acceptance", str(record), "--no-docx"]
    folder = tmp_path / "archive"
    folder.mkdir()
    archive = {folder / path.name: b"frozen fixture" for path in build_test_doc.ARCHIVED_FILES}
    for path, saved in archive.items():
        path.write_bytes(saved)
    monkeypatch.setattr(build_test_doc, "ARCHIVED_FILES", set(archive))
    for day in (19, 20):
        record.write_text(json.dumps({"meta": {"startedAt": f"2026-11-{day}T01:00:00Z"},
                                      "results": []}), encoding="utf-8")
        assert build_test_doc.main(args) == 0
    reports = list((tmp_path / "retests").glob("*.md"))
    assert len(reports) == 2
    assert {line for report in reports for line in report.read_text("utf-8").splitlines()
            if line.startswith("| 本轮测试日期")} == {
                "| 本轮测试日期 | 2026-11-19 |", "| 本轮测试日期 | 2026-11-20 |"}
    for path, saved in archive.items():
        with pytest.raises(SystemExit):
            build_test_doc.main([*args, "--markdown", str(path)])
        assert path.read_bytes() == saved
    custom = tmp_path / "custom.md"
    assert build_test_doc.main([*args, "--markdown", str(custom)]) == 0
    assert "| 本轮测试日期 | 2026-11-20 |" in custom.read_text("utf-8")


def test_local_revision_covers_staged_unstaged_and_new_inputs(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True).stdout

    git("init", "-q")
    assert run_p6.local_revision(tmp_path) is None
    source = tmp_path / "backend/app/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("# first\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=P6", "-c", "user.email=p6@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    head = git("rev-parse", "HEAD").decode().strip()
    report = tmp_path / "qa/p6/reports/acceptance-latest.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}", encoding="utf-8")
    assert run_p6.local_revision(tmp_path) == head
    new_source = report.parent.parent / "helper.py"
    new_source.touch()
    assert run_p6.local_revision(tmp_path) == head + "-dirty"
    new_source.unlink()
    source.write_text("# changed\n", encoding="utf-8")
    assert run_p6.local_revision(tmp_path) == head + "-dirty"
    git("add", str(source))
    assert run_p6.local_revision(tmp_path) == head + "-dirty"


@pytest.mark.parametrize("flags", [["--limit", "0"], ["--limit", "-1"], ["--conditions", ""],
                                   ["--conditions", "baseline,baseline"], ["--conditions", "domain,"],
                                   ["--conditions", "unknown"]])
def test_invalid_model_scope_stops_before_any_call(tmp_path, monkeypatch, flags):
    live = Mock(side_effect=AssertionError("不应启动模型"))
    monkeypatch.setattr(runner, "run_live", live)
    assert runner.main(["--outdir", str(tmp_path), "--live", *flags]) == 2
    live.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(("second_text", "expected_code", "valid"), [("   ", 1, 0), ("示例", 0, 1)])
def test_model_run_and_summary_report_actual_outputs(tmp_path, monkeypatch, second_text, expected_code, valid):
    from app.adapters.model import ModelAdapter
    from app.config import Settings
    from app.schemas.agent import ModelGenerateResult

    monkeypatch.setattr(Settings, "from_env", lambda: Settings(model_api_url="https://model.invalid",
                        model_api_key="offline-fixture", model_name="test", model_send_thinking=False,
                        model_timeout_seconds=7, model_max_tokens=64))
    gateway = MagicMock()
    monkeypatch.setattr("app.model_gateway.ModelGateway", gateway)
    generate = AsyncMock(side_effect=[ModelGenerateResult(success=False, error_code="TIMEOUT"),
                                      ModelGenerateResult(success=True, text=second_text)])
    monkeypatch.setattr(ModelAdapter, "generate", generate)
    args = ["--outdir", str(tmp_path)]
    assert runner.main([*args, "--limit", "1", "--db", str(tmp_path / "empty.db"), "--live"]) == expected_code
    assert generate.await_count == 2
    settings = gateway.call_args.args[0]
    assert (settings.model_send_thinking, settings.model_timeout_seconds, settings.model_max_tokens) == (False, 7, 64)
    plan = json.loads((tmp_path / "model-compare-plan.json").read_text("utf-8"))
    meta = json.loads(next(tmp_path.glob("run-*.json")).read_text("utf-8"))
    rows = scoring.load_results(next(tmp_path.glob("results-*.jsonl")))
    assert len(rows) == 2 and {r["planHash"] for r in rows} == {meta["planHash"]} == {scoring.plan_fingerprint(plan)}
    assert scoring.main(args) == expected_code
    summary = json.loads((tmp_path / "model-compare-summary.json").read_text("utf-8"))
    assert (summary["resultRows"], summary["validOutputRows"]) == (2, valid)
    assert bool(list(tmp_path.glob("scoring-*.csv"))) == bool(valid)
    if not valid:
        assert "本轮没有可评分输出" in (tmp_path / "model-compare-summary.md").read_text("utf-8")


def test_legacy_plan_upgrade_preserves_original_date_and_preview_scores(tmp_path):
    args = ["--outdir", str(tmp_path), "--limit", "1"]
    assert runner.main(args) == 0
    path = tmp_path / "model-compare-plan.json"
    previous = json.loads(path.read_text("utf-8"))
    fingerprint = previous.pop("planHash")
    previous["generatedAt"] = "2026-11-18T01:00:00Z"
    path.write_text(json.dumps(previous), encoding="utf-8")
    preview = tmp_path / "scoring.csv"
    saved = preview.read_bytes()
    assert runner.main(args) == 0
    assert json.loads(path.read_text("utf-8")) == {**previous, "planHash": fingerprint}
    assert preview.read_bytes() == saved


def test_changed_plan_and_legacy_results_preserve_valid_summary(tmp_path, capsys):
    args = ["--outdir", str(tmp_path)]
    assert runner.main([*args, "--limit", "1"]) == scoring.main(args) == 0
    plan = json.loads((tmp_path / "model-compare-plan.json").read_text("utf-8"))
    results = tmp_path / "results-A.jsonl"
    row = {"caseId": plan["cases"][0]["caseId"], "condition": "baseline", "runId": "A",
           "model": "offline", "output": "fixture", "success": True, "planHash": plan["planHash"]}
    results.write_text(json.dumps(row), encoding="utf-8")
    assert scoring.main(args) == 0
    summary = tmp_path / "model-compare-summary.json"
    saved = summary.read_bytes()
    other = copy.deepcopy(plan)
    other["cases"][0]["pageText"] += "另一个页面"
    changed_plan = tmp_path / "other-plan.json"
    for fingerprint in (plan["planHash"], scoring.plan_fingerprint(other)):
        other["planHash"] = fingerprint
        changed_plan.write_text(json.dumps(other), encoding="utf-8")
        assert scoring.main([*args, "--plan", str(changed_plan)]) == 2
        assert not scoring.eligible_outputs(other, [row]) and summary.read_bytes() == saved
    del row["planHash"]
    results.write_text(json.dumps(row), encoding="utf-8")
    assert scoring.main(args) == 2 and "缺少计划指纹" in capsys.readouterr().err
    assert summary.read_bytes() == saved and list(tmp_path.glob("outputs-for-scoring-*.md"))
    results.write_bytes(b"")
    assert scoring.main(args) == 1 and "本轮没有可评分输出" in (tmp_path / "model-compare-summary.md").read_text("utf-8")


def test_scoring_retry_keeps_complete_rows_and_notes(tmp_path):
    def interrupted():
        yield {"key": "baseline"}
        raise OSError("模拟中断")

    with pytest.raises(OSError, match="模拟中断"):
        scoring.publish_csv(tmp_path / "interrupted.csv", ["key"], interrupted())
    assert not list(tmp_path.iterdir())
    outputs = {c: {"caseId": "C", "condition": c, "model": "stub", "output": c,
                   "runId": "A", "planHash": "plan"} for c in ("baseline", "domain")}
    def sheet():
        return scoring.score_sheet(tmp_path, Path("results-A.jsonl"), outputs, ["安全性"])

    original = sheet()
    with original.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns, first = reader.fieldnames, next(reader)
    first.update(rater1_安全性="0", rater1_note="保留这条意见")
    with original.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow(first)
    saved = original.read_bytes()
    recovered = sheet()
    assert recovered != original and sheet() == recovered and original.read_bytes() == saved
    with recovered.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2 and rows[0] == first and rows[1]["rater1_安全性"] == ""
    assert not scoring.publish_csv(original, columns, []) and original.read_bytes() == saved
    target = tmp_path / "concurrent.csv"
    with ThreadPoolExecutor(4) as pool:
        published = list(pool.map(lambda _: scoring.publish_csv(target, columns, rows), range(4)))
    assert sum(published) == 1
    with target.open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle)) == rows


@pytest.fixture
def acceptance(monkeypatch):
    monkeypatch.setattr(run_acceptance, "ApiSession", Mock(side_effect=lambda *args, **kwargs: Mock()))
    return run_acceptance.Acceptance(run_acceptance.parse_args([]))


def test_ledger_uses_current_archive_even_if_latest_changes(tmp_path, monkeypatch, capsys):
    def run(argv, **kwargs):
        if Path(argv[1]).name == "track_defects.py":
            return track_defects.main(argv[2:])
        own = common.Recorder()
        own.meta["testedCommit"] = "own-commit"
        own.record("TC-P6-BOOK-003", "绘本详情", "AC-03", "FAIL", "HTTP 500 reading story")
        archive = common.write_reports(own, tmp_path)["json"]
        Path(argv[argv.index("--report-path-file") + 1]).write_text(str(archive), "utf-8")
        other = common.Recorder()
        other.meta["testedCommit"] = "other-commit"
        common.write_reports(other, tmp_path)
        return 1

    monkeypatch.setattr(run_p6, "run", run)
    assert run_p6.main(["--base-url", "https://test.invalid", "--outdir", str(tmp_path), "--skip-model"]) == 1
    for name in ("defect-ledger.md", "defect-ledger.csv"):
        ledger = (tmp_path / name).read_text("utf-8-sig")
        assert "DEF-P6-008" in ledger and "HTTP 500 reading story" in ledger
    output = capsys.readouterr().out
    assert "own-commit" in output and "other-commit" not in output


def test_invalid_score_preserves_previous_summary(tmp_path, capsys):
    args = ["--outdir", str(tmp_path)]
    assert runner.main([*args, "--limit", "1"]) == scoring.main(args) == 0
    original = {p: p.read_bytes() for p in tmp_path.glob("model-compare-summary.*")}
    plan = json.loads((tmp_path / "model-compare-plan.json").read_text("utf-8"))
    key = "rater2_" + plan["dimensions"][0]
    sheet = tmp_path / "invalid.csv"
    with sheet.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["outputKey", *scoring.scoring_columns(plan["dimensions"])])
        writer.writeheader()
        writer.writerow({"caseId": "case-A", "condition": "domain", key: "3"})
    assert scoring.main([*args, "--scores", str(sheet)]) == 2
    error = capsys.readouterr().err
    assert all(value in error for value in ("case-A", key, "3"))
    assert all(path.read_bytes() == content for path, content in original.items())


def test_concurrent_ledgers_keep_each_pair_and_publish_matching_links(tmp_path, monkeypatch):
    first_csv, second_done = Event(), Event()
    replace = Path.replace

    def pause_after_first_csv(path, target):
        result = replace(path, target)
        if target == tmp_path / "defect-ledger.csv" and not first_csv.is_set():
            first_csv.set()
            second_done.wait(0.5)
        return result

    monkeypatch.setattr(Path, "replace", pause_after_first_csv)

    def publish(run):
        report = tmp_path / f"acceptance-{run}.json"
        payload = {"meta": {"runId": run}, "results": [{"case_id": run, "status": "FAIL", "detail": f"fault-{run}"}]}
        report.write_text(json.dumps(payload), "utf-8")
        return track_defects.main(["--acceptance", str(report), "--outdir", str(tmp_path)])

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(publish, "A")
        assert first_csv.wait(5)
        try:
            assert pool.submit(publish, "B").result(timeout=10) == 0
        finally:
            second_done.set()
        assert first.result(timeout=10) == 0

    latest_md = (tmp_path / "defect-ledger.md").read_text("utf-8")
    for run in ("A", "B"):
        archive, = (tmp_path / "ledgers").glob(f"ledger-{run}-*")
        for name in ("defect-ledger.csv", "defect-ledger.md"):
            saved = archive / name
            assert f"fault-{run}" in saved.read_text("utf-8-sig")
            if run == "B":
                assert saved.relative_to(tmp_path).as_posix() in latest_md
        if run == "B":
            assert (archive / "defect-ledger.csv").read_bytes() == (tmp_path / "defect-ledger.csv").read_bytes()
    assert "fault-B" in latest_md and "fault-A" not in latest_md


def test_ledger_defaults_to_selected_outdir_and_honors_explicit_report(tmp_path, monkeypatch):
    default_out, selected = tmp_path / "default", tmp_path / "selected"
    for folder in (default_out, selected):
        folder.mkdir()
        report = {"results": [{"case_id": "TC-P6-123", "status": "FAIL", "detail": f"failure-{folder.name}"}]}
        (folder / "acceptance-latest.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(track_defects, "DEFAULT_OUT", default_out)
    args = ["--outdir", str(selected)]
    for flags, marker in (([], "failure-selected"), (["--acceptance", str(default_out / "acceptance-latest.json")],
                                                    "failure-default")):
        assert track_defects.main([*args, *flags]) == 0
        for name in ("defect-ledger.md", "defect-ledger.csv"):
            assert marker in (selected / name).read_text("utf-8-sig")
    (selected / "acceptance-latest.json").unlink()
    assert track_defects.main(args) == 2


@pytest.mark.parametrize("previous", [False, True])
def test_summary_publish_failure_preserves_both_entries_and_retries(tmp_path, monkeypatch, previous):
    args = ["--outdir", str(tmp_path)]
    assert runner.main([*args, "--limit", "1"]) == 0
    if previous:
        assert scoring.main(args) == 0
    before = {p.name: p.read_bytes() for p in tmp_path.glob("model-compare-summary.*")}
    plan = json.loads((tmp_path / "model-compare-plan.json").read_text("utf-8"))
    row = {"caseId": plan["cases"][0]["caseId"], "condition": "baseline", "runId": "B",
           "model": "offline", "output": "fixture", "success": True, "planHash": plan["planHash"]}
    (tmp_path / "results-B.jsonl").write_text(json.dumps(row), encoding="utf-8")
    markdown = tmp_path / "model-compare-summary.md"
    replace = Path.replace

    def locked(path, target):
        if target == markdown:
            raise PermissionError("Markdown 文件被占用")
        return replace(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", locked)
        assert scoring.main(args) == 2
    assert {p.name: p.read_bytes() for p in tmp_path.glob("model-compare-summary.*")} == before
    assert scoring.main(args) == 0
    assert "results-B.jsonl" in markdown.read_text("utf-8")
    assert json.loads(markdown.with_suffix(".json").read_text("utf-8"))["resultRows"] == 1


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(("number", "after"), [(0, False), (0, True), (1, False), (1, True)])
def test_interrupted_publication_keeps_matching_pair(tmp_path, monkeypatch, existing, number, after):
    staging = tmp_path / "staging"
    staging.mkdir()
    sources = [staging / name for name in ("summary.json", "summary.md")]
    entries = [tmp_path / src.name for src in sources]
    for src, entry in zip(sources, entries):
        src.write_bytes(b"new")
        if existing:
            entry.write_bytes(b"old")
    replace = Path.replace

    def interrupt(path, target):
        if path == sources[number] and not after:
            raise KeyboardInterrupt
        result = replace(path, target)
        if path == sources[number] and after:
            raise KeyboardInterrupt
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", interrupt)
        with pytest.raises(KeyboardInterrupt):
            publish_pair(*sources, tmp_path)
    if number == 1 and after:
        assert all(entry.read_bytes() == b"new" for entry in entries)
    elif existing:
        assert all(entry.read_bytes() == b"old" for entry in entries)
    else:
        assert all(not entry.exists() for entry in entries)


def test_missing_acceptance_preserves_ledger_until_manual_only_requested(tmp_path, capsys):
    report = tmp_path / "acceptance-A.json"
    report.write_text(json.dumps({"results": [{"case_id": "TC-P6-123", "status": "FAIL", "detail": "failure-A"}]}),
                      encoding="utf-8")
    args = ["--outdir", str(tmp_path)]
    assert track_defects.main([*args, "--acceptance", str(report)]) == 0
    files = {path: path.read_bytes() for path in tmp_path.rglob("defect-ledger.*")}
    assert len(files) == 4 and all(b"failure-A" in content for content in files.values())
    missing = tmp_path / "typo.json"
    assert track_defects.main([*args, "--acceptance", str(missing)]) == 2
    assert str(missing) in capsys.readouterr().err
    assert {path: path.read_bytes() for path in tmp_path.rglob("defect-ledger.*")} == files
    assert track_defects.main([*args, "--manual-only"]) == 0
    assert "failure-A" not in (tmp_path / "defect-ledger.csv").read_text("utf-8-sig")
    assert all(path.read_bytes() == content for path, content in files.items() if path.parent != tmp_path)


def test_failed_latest_markdown_preserves_previous_ledger(tmp_path, monkeypatch):
    def publish(run):
        payload = {"meta": {"runId": run}, "results": [{"case_id": run, "status": "FAIL", "detail": run}]}
        return track_defects.publish_ledger(track_defects.auto_defects(payload), payload, tmp_path, Path(f"{run}.json"))

    publish("first")
    entries = {p: p.read_bytes() for p in tmp_path.glob("defect-ledger.*")}
    replace = Path.replace

    def refuse_markdown(path, target):
        if target == tmp_path / "defect-ledger.md":
            raise PermissionError("入口文件被占用")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", refuse_markdown)
    with pytest.raises(PermissionError):
        publish("second")
    assert all(path.read_bytes() == original for path, original in entries.items())


@pytest.mark.parametrize("flags", [[], ["--live"]])
@pytest.mark.parametrize("invalid", [{"task": "STORY_ANSWR"}, {"task": "CUSTOM", "customTask": "SAFTEY"}])
def test_unknown_task_rejected_before_plan_or_calls(tmp_path, monkeypatch, flags, invalid):
    payload = json.loads(runner.DEFAULT_CASES.read_text("utf-8"))
    payload["cases"][-1].update(invalid)
    source = tmp_path / "cases.json"
    source.write_text(json.dumps(payload), "utf-8")
    live = Mock()
    monkeypatch.setattr(runner, "run_live", live)
    with pytest.raises(SystemExit, match="未知"):
        runner.main(["--outdir", str(tmp_path), "--cases", str(source), *flags])
    live.assert_not_called()
    assert not (tmp_path / "model-compare-plan.json").exists()


def test_profile_checks_reloaded_interests(acceptance):
    acceptance.profile = {"childProfileId": 1}
    acceptance.a.patch.return_value = httpx.Response(200, json={"data": {"preferredMinutes": 15, "interests": ["动物"]}})
    row = {"childProfileId": 1, "preferredMinutes": 15, "interests": ["友谊"]}
    acceptance.a.get.return_value = httpx.Response(200, json={"data": {"items": [row]}})
    with pytest.raises(AssertionError, match="重新读取"):
        acceptance.test_profile_update()


def test_child_list_rejects_extra_story(acceptance, monkeypatch):
    guardian = Mock()
    monkeypatch.setattr(run_acceptance, "ApiSession", Mock(return_value=guardian))
    acceptance.profile, acceptance.story = {"childProfileId": 1}, {"storyVersionId": 3}
    acceptance.account, acceptance.password = "fixture", "fixture-only"
    acceptance.login = Mock(return_value=httpx.Response(201, json={}))
    guardian.post.return_value = httpx.Response(201, json={"data": {"readingSessionId": 1}})
    guardian.put.return_value = httpx.Response(200, json={"data": {"mode": "CHILD_SOLO"}})
    guardian.get.return_value = httpx.Response(200, json={"data": {"items": [{"storyVersionId": i} for i in (3, 4)]}})
    with pytest.raises(AssertionError, match="授权范围"):
        acceptance.test_child_mode()


@pytest.mark.parametrize("invalid", [None, {}, " "])
def test_voice_requires_nonempty_url(acceptance, invalid):
    acceptance.voice_session, acceptance.args.voice_live = 1, True
    acceptance.a.post.return_value = httpx.Response(201, json={"data": {"audioUrl": invalid}})
    with pytest.raises(AssertionError, match="audioUrl"):
        acceptance.test_voice_validation()


@pytest.mark.parametrize("invalid", [None, False, [], " "])
def test_recommendation_requires_nonempty_reason(acceptance, invalid):
    acceptance.profile = {"childProfileId": 1}
    body = {"requestId": "req", "data": {"items": [{"storyVersionId": 1, "reason": invalid}]}}
    acceptance.a.post.side_effect = [
        httpx.Response(403, json={"error": {"code": "CSRF_INVALID"}}),
        httpx.Response(200, json=body, headers={"X-Request-ID": "req"}),
    ]
    with pytest.raises(AssertionError, match="reason"):
        acceptance.test_recommendation()


@pytest.mark.parametrize(("login_status", "detail_id", "status", "expected"),
                         [(201, 7, "FINISHED", "PASS"), (201, 8, "FINISHED", "FAIL"),
                          (201, 7, "ACTIVE", "FAIL"), (429, 7, "FINISHED", "BLOCKED")])
def test_history_uses_only_fresh_owner_session(acceptance, monkeypatch, login_status, detail_id, status, expected):
    client = Mock()
    monkeypatch.setattr(run_acceptance, "ApiSession", Mock(return_value=client))
    acceptance.account, acceptance.password, acceptance.session_id = "fixture", "fixture-only", 7
    client.post.return_value = httpx.Response(login_status, json={})
    client.get.side_effect = [
        httpx.Response(200, json={"data": {"items": [{"readingSessionId": 7, "status": "FINISHED"}]}}),
        httpx.Response(200, json={"data": {"session": {"readingSessionId": detail_id, "status": status}}})]
    assert acceptance.rec.case("history", "", "", acceptance.test_history).status == expected
    client.post.assert_called_once_with("/api/v1/auth/sessions", json={"account": "fixture", "password": "fixture-only"})
    client.close.assert_called_once()
    assert client.get.call_count == (0 if login_status == 429 else 2)
    assert not acceptance.a.mock_calls


def test_one_page_story_blocks_page_change(acceptance):
    acceptance.session_id, acceptance.story = 7, {"pages": [{"defaultNextPageNumber": None}]}
    assert acceptance.rec.case("page", "", "", acceptance.test_page_change).status == "BLOCKED"
    assert not acceptance.a.mock_calls


@pytest.mark.parametrize(("entry", "location", "expected"), [
    ("http://entry.example", "https://app.example:443/home", "PASS"),
    ("http://entry.example", "https://other.example/home", "FAIL"),
    ("http://entry.example", "https://app.example:8443/home", "FAIL"),
    ("http://entry.example", "/home", "FAIL"),
    ("http://entry.example", "", "FAIL"),
    ("https://entry.example", "https://app.example", "FAIL"),
])
def test_redirect_targets_configured_https_origin(acceptance, monkeypatch, entry, location, expected):
    acceptance.args.http_url, acceptance.args.base_url = entry, "https://app.example"
    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    client.get.return_value = httpx.Response(301, headers={"Location": location})
    monkeypatch.setattr(httpx, "Client", factory)
    assert acceptance.rec.case("redirect", "", "", acceptance.test_http_redirect).status == expected
    if entry.startswith("http://"):
        assert factory.call_args.kwargs["follow_redirects"] is False
        client.get.assert_called_once()
    else:
        factory.assert_not_called()


@pytest.mark.parametrize(("frame", "policy", "expected"), [("DENY", "default-src 'self'", "PASS"),
                          ("sameorigin", "default-src 'self'", "PASS"), ("unknown", "default-src 'self'", "WARN"),
                          ("DENY", "", "WARN")])
def test_security_header_status_matches_evidence(acceptance, frame, policy, expected):
    headers = {"X-Content-Type-Options": "NoSniff", "X-Frame-Options": frame,
               "Content-Security-Policy": policy, "Referrer-Policy": "same-origin"}
    acceptance.anon.get.return_value = httpx.Response(200, headers=headers)
    result = acceptance.rec.case("headers", "", "", acceptance.test_security_headers)
    assert result.status == expected
    assert result.evidence["missing"] == ([] if policy else ["content-security-policy"])
    assert result.evidence["invalid"] == (["x-frame-options"] if frame == "unknown" else [])


def test_reachability_does_not_stand_in_for_browser_flow():
    probe = {"case_id": "TC-P6-FE-001", "title": "首页", "ac": "NFR-01", "status": "PASS", "detail": "HTTP 200"}
    rendered, _, _ = build_test_doc.render(build_test_doc.TEMPLATE.read_text("utf-8"), {"results": [probe]})
    assert next(line for line in rendered.splitlines() if "| 前端页面可达 |" in line).endswith("| PASS（通过） |")
    assert next(line for line in rendered.splitlines() if "| 前端端到端 |" in line).endswith("| 本轮未执行 |")


def test_contract_checks_each_method(acceptance):
    paths = {}
    for op in json.loads(run_acceptance.CONTRACT.read_text("utf-8"))["endpoints"]:
        paths.setdefault(op["path"], {})[op["method"].lower()] = {}
    route = "/api/v1/admin/knowledge-items"
    acceptance.anon.get.return_value = httpx.Response(200, json={"paths": paths})
    assert acceptance.test_openapi_coverage()[1]["missingOperations"] == []
    del paths[route]["post"]
    paths[route]["parameters"] = []
    acceptance.anon.get.return_value = httpx.Response(200, json={"paths": paths})
    warning = acceptance.test_openapi_coverage()
    assert warning.evidence["missingOperations"] == [f"POST {route}"]
    assert warning.evidence["missingPaths"] == warning.evidence["extraOperations"] == []


def test_empty_database_recovers_after_seed_failure(tmp_path, monkeypatch):
    database = tmp_path / "demo.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(prepare_demo_data, "import_seed", Mock(side_effect=OSError("中断")))
        with pytest.raises(OSError, match="中断"):
            prepare_demo_data.ensure_database(database)
    monkeypatch.setattr("sys.argv", ["prepare_demo_data", "--db", str(database), "--yes", "--if-empty"])
    assert prepare_demo_data.main() == 0
    counts = prepare_demo_data.check(database)
    assert counts["publishedVersions"] > 0 and counts["accounts"] > 0
    monkeypatch.setattr(prepare_demo_data, "approve_content", Mock(side_effect=AssertionError("重复准备")))
    assert prepare_demo_data.main() == 0


def test_same_second_keeps_each_acceptance_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "stamp", lambda: "20261119-120000")
    for version in ("A", "B"):
        rec = common.Recorder()
        rec.meta["testedCommit"] = version
        common.write_reports(rec, tmp_path)
    archived = [p for p in tmp_path.glob("acceptance-*.json") if p.name != "acceptance-latest.json"]
    assert len(archived) == 2
    assert {json.loads(p.read_text("utf-8"))["meta"]["testedCommit"] for p in archived} == {"A", "B"}
    for p in archived:
        assert p.with_suffix(".md").exists() and p.with_suffix(".xml").exists()
    assert json.loads((tmp_path / "acceptance-latest.json").read_text("utf-8"))["meta"]["testedCommit"] == "B"


def test_one_click_resumes_after_seed_commit(tmp_path, monkeypatch):
    path = tmp_path / "demo.sqlite3"
    monkeypatch.setattr("sys.argv", ["prepare", "--db", str(path), "--yes", "--if-empty"])
    with monkeypatch.context() as fault:
        fault.setattr(prepare_demo_data, "approve_content", Mock(side_effect=OSError("审核中断")))
        with pytest.raises(OSError, match="审核中断"):
            prepare_demo_data.main()
    before = prepare_demo_data.check(path)
    # Import commits the course publisher and 20 published originals before the interrupted QA transaction.
    assert before["story_versions"] == 21 and before["publishedVersions"] == 20
    assert before["accounts"] == 1 and before["child_profiles"] == before["approvedKnowledge"] == 0
    assert prepare_demo_data.main() == 0
    after = prepare_demo_data.check(path)
    assert after["accounts"] == before["accounts"] + 2 and after["child_profiles"] == 1
    assert after["publishedVersions"] == before["publishedVersions"] and after["approvedKnowledge"] > 0
    monkeypatch.setattr(prepare_demo_data, "approve_content", Mock(side_effect=AssertionError("重复发布")))
    assert prepare_demo_data.main() == 0 and prepare_demo_data.check(path) == after


def test_unmarked_seed_database_requires_explicit_preparation(tmp_path, monkeypatch):
    path = tmp_path / "existing.sqlite3"
    prepare_demo_data.ensure_database(path)
    with closing(prepare_demo_data.connect(path)) as db, db:
        db.execute("DROP TABLE p6_demo_setup_pending")
    before = prepare_demo_data.check(path)
    monkeypatch.setattr("sys.argv", ["prepare", "--db", str(path), "--yes", "--if-empty"])
    assert prepare_demo_data.main() == 2 and prepare_demo_data.check(path) == before


@pytest.mark.parametrize("value", [None, "", "  ", 7])
def test_eval_id_required_before_any_provider_call(tmp_path, monkeypatch, value):
    payload = json.loads(runner.DEFAULT_CASES.read_text("utf-8"))
    case = payload["cases"][0]
    if value is None:
        del case["evalId"]
    else:
        case["evalId"] = value
    source, out = tmp_path / "cases.json", tmp_path / "results"
    source.write_text(json.dumps(payload), encoding="utf-8")
    live = Mock(side_effect=AssertionError("不应启动模型"))
    monkeypatch.setattr(runner, "run_live", live)
    with pytest.raises(SystemExit, match=f"{case['caseId']}.*evalId"):
        runner.main(["--cases", str(source), "--outdir", str(out), "--live"])
    live.assert_not_called()
    assert not out.exists()


def test_branch_uses_reachable_page_and_observed_revision(acceptance):
    acceptance.story = {"pages": [{"pageNumber": 1, "defaultNextPageNumber": 2}, {"pageNumber": 2}],
                        "branches": [{"branchId": i, "fromPageNumber": page, "nextPageNumber": 3,
                                      "reviewStatus": "APPROVED"} for i, page in [(10, 7), (11, 2)]]}
    acceptance._create_session = Mock(return_value={"readingSessionId": 1, "revision": 0})
    def response(status, data):
        return httpx.Response(status, json={"data": data})
    acceptance.a.post.side_effect = [response(201, {"revision": 1}), response(201, {"revision": 2}),
                                     response(201, {"currentPage": 3, "revision": 3}), response(200, {}),
                                     httpx.Response(409, json={"error": {"code": "SESSION_CONFLICT"}})]
    outcome = acceptance.test_branch_choice()
    posts = [call.kwargs["json"] for call in acceptance.a.post.call_args_list]
    assert [event["pageNumber"] for event in posts[:2]] == [1, 2]
    assert posts[1]["eventType"] == "PAGE_CHANGED"
    assert [event["expectedRevision"] for event in posts] == [0, 1, 2, 2, 2]
    assert outcome.evidence["branchId"] == 11
    acceptance.story["branches"] = acceptance.story["branches"][:1]
    acceptance.story["pages"][1]["defaultNextPageNumber"] = 1
    with pytest.raises(common.Blocked, match="无法到达"):
        acceptance.test_branch_choice()
    acceptance._create_session.assert_called_once()


@pytest.mark.parametrize("has_previous", [False, True])
def test_interrupted_acceptance_preserves_latest_and_archive(tmp_path, monkeypatch, has_previous):
    recorder = common.Recorder()
    if has_previous:
        recorder.meta["testedCommit"] = "A"
        common.write_reports(recorder, tmp_path)
    previous = {p: p.read_bytes() for p in tmp_path.glob("acceptance-latest.*")}
    replace = Path.replace

    def interrupt_json(path, target):
        if target == tmp_path / "acceptance-latest.json":
            raise KeyboardInterrupt
        return replace(path, target)

    recorder.meta["testedCommit"] = "B"
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", interrupt_json)
        with pytest.raises(KeyboardInterrupt):
            common.write_reports(recorder, tmp_path)
    assert {p: p.read_bytes() for p in tmp_path.glob("acceptance-latest.*")} == previous
    archives = [p for p in tmp_path.glob("acceptance-*.json") if p.name != "acceptance-latest.json"]
    commits = set()
    for archive in archives:
        meta = json.loads(archive.read_text("utf-8"))["meta"]
        commits.add(meta["testedCommit"])
        assert meta["runId"] in archive.with_suffix(".md").read_text("utf-8")
        assert archive.with_suffix(".xml").is_file()
    assert commits == ({"A", "B"} if has_previous else {"B"})
    retried = common.write_reports(recorder, tmp_path)
    assert retried["latestJson"].read_bytes() == retried["json"].read_bytes()
    assert retried["latestMarkdown"].read_bytes() == retried["markdown"].read_bytes()


def test_simultaneous_publications_keep_latest_pair_together(tmp_path, monkeypatch):
    first = common.Recorder()
    second = common.Recorder()
    first.meta["testedCommit"], second.meta["testedCommit"] = "A", "B"
    first_alias = Event()
    second_finished = Event()
    replace = Path.replace

    def pause_first_writer(path, destination):
        result = replace(path, destination)
        if not first_alias.is_set():
            first_alias.set()
            second_finished.wait(1)
        return result

    monkeypatch.setattr(Path, "replace", pause_first_writer)
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(common.write_reports, first, tmp_path)
        assert first_alias.wait(5)
        b = pool.submit(common.write_reports, second, tmp_path)
        try:
            b.result(timeout=10)
        finally:
            second_finished.set()
        a.result(timeout=10)
    latest = json.loads((tmp_path / "acceptance-latest.json").read_text("utf-8"))
    assert latest["meta"]["testedCommit"] == "B"
    archived = tmp_path / f"acceptance-{latest['meta']['runId']}.md"
    assert archived.read_bytes() == (tmp_path / "acceptance-latest.md").read_bytes()
