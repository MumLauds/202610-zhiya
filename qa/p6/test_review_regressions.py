"""针对 P6 验收程序的离线回归；临时数据库、HTTP 替身与合成评分。"""

import csv
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, Mock

import httpx
import pytest

import build_test_doc
import common
import demo_flow
import prepare_demo_data as demo
import revoke_archived_session
import run_acceptance
import run_model_compare
import run_p6
import summarize_model_compare
import track_defects


def case(case_id="TC-P6-AUTH-006", status="FAIL"):
    return dict(case_id=case_id, ac="AC-01", title="测试样本", status=status, detail="样本观测")


@pytest.mark.parametrize(("url", "flags", "expected"), [
    ("http://127.0.0.1:8765", ["--profile", "public", "--allow-http"], "FAIL"),
    ("http://elsewhere.example", ["--profile", "local", "--allow-http"], "FAIL"),
    ("https://test.example", ["--profile", "public", "--insecure-tls"], "BLOCKED"),
    ("https://detached.example", ["--profile", "local", "--insecure-tls"], "FAIL"),
])
def test_protocol_gate_runs_before_any_request(tmp_path, monkeypatch, url, flags, expected):
    calls = Mock(side_effect=AssertionError("协议检查前不应发出 HTTP 请求"))
    monkeypatch.setattr(httpx.Client, "request", calls)
    assert run_acceptance.main(["--base-url", url, "--outdir", str(tmp_path), *flags]) != 0
    rows = json.loads((tmp_path / "acceptance-latest.json").read_text("utf-8"))["results"]
    assert rows[-1]["status"] == expected
    assert not any(row["case_id"].startswith("TC-P6-AUTH") for row in rows)
    calls.assert_not_called()


@pytest.mark.parametrize(("url", "local"), [("https://qa.example", False), ("http://127.0.0.1", True)])
def test_cookie_properties_and_deletion(url, local):
    responses = iter(["zhiya_session=sample; Path=/; Secure; HttpOnly; SameSite=Lax",
                      "zhiya_session=; Path=/; Secure; Max-Age=0"])
    def respond(req):
        return httpx.Response(200, headers={"Set-Cookie": next(responses)})

    api = common.ApiSession(url, allow_insecure_cookies=True)
    api.close()
    api.client = httpx.Client(base_url=url, transport=httpx.MockTransport(respond))
    with api.client:
        first = api.get("/")
        flags = common.cookie_flags(first, "zhiya_session")
        assert flags == {"secure": True, "httpOnly": True, "sameSiteLax": True}
        assert api.allow_insecure_cookies is local
        assert next(iter(api.client.cookies.jar)).secure is (not local)
        with pytest.raises(ValueError):
            api.post("http://another.example/login")
        second = api.get("/")
        assert second.request.headers["Cookie"] == "zhiya_session=sample"
        assert api.session_cookie is None


def test_legacy_revoke_only_matches_active_session(tmp_path):
    target = tmp_path / "archive.db"
    with closing(sqlite3.connect(target)) as db, db:
        db.execute("CREATE TABLE auth_sessions (token_digest, revoked_at, expires_at)")
        db.executemany("INSERT INTO auth_sessions VALUES (?, NULL, ?)",
                       [(hashlib.sha256(x.encode()).hexdigest(), 4_000_000_000_000)
                        for x in ("test-archived", "test-other")])
    report = {"results": [{"case_id": "TC-P6-AUTH-004", "evidence": {"cookieFlags": {
        "session": "zhiya_session=test-archived; Secure; HttpOnly"}}}]}
    assert revoke_archived_session.revoke(target, report) == 1
    assert revoke_archived_session.revoke(target, report) == 0
    with closing(sqlite3.connect(target)) as db:
        assert db.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL").fetchone()[0] == 1


def test_voice_consent_and_unconfigured_live(monkeypatch, tmp_path):
    acceptance = run_acceptance.Acceptance(run_acceptance.parse_args(["--outdir", str(tmp_path)]))
    try:
        acceptance.voice_session = 3
        post = Mock(return_value=httpx.Response(503, json={"error": {"code": "TTS_NOT_CONFIGURED"}}))
        monkeypatch.setattr(acceptance.a, "post", post)
        with pytest.raises(common.Blocked):
            acceptance.test_voice_validation()
        post.assert_not_called()
        acceptance.args.voice_live = True
        result = acceptance.rec.case("TC-P6-VOICE-002", "测试", "AC-07", acceptance.test_voice_validation)
        assert result.status == "BLOCKED"
        post.assert_called_once()
    finally:
        acceptance.a.close()
        acceptance.anon.close()


def test_voice_auth_checks_do_not_submit_synthesis(monkeypatch):
    acceptance = run_acceptance.Acceptance(run_acceptance.parse_args([]))
    try:
        monkeypatch.setattr(acceptance, "_create_session", lambda: {"readingSessionId": 1})
        def handle(path, **kwargs):
            if path.endswith("/events"):
                return httpx.Response(201, json={})
            assert path.endswith("/transcriptions")
            return httpx.Response(403, json={"error": {"code": "CSRF_INVALID"}})
        monkeypatch.setattr(acceptance.a, "post", handle)
        monkeypatch.setattr(acceptance.anon, "post", lambda *a, **k: httpx.Response(
            401, json={"error": {"code": "AUTH_REQUIRED"}}))
        assert "403" in acceptance.test_voice_auth()
    finally:
        acceptance.a.close()
        acceptance.anon.close()


def test_external_runner_does_not_guess_voice_or_print_password(tmp_path, monkeypatch, capsys):
    def accept(argv, **kwargs):
        archive = tmp_path / "acceptance-test.json"
        archive.write_text("{}", "utf-8")
        Path(argv[argv.index("--report-path-file") + 1]).write_text(str(archive), "utf-8")
        return type("Run", (), {"returncode": 0})()
    child = Mock(side_effect=accept)
    monkeypatch.setattr(run_p6.subprocess, "run", child)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    assert run_p6.main(["--base-url", "http://127.0.0.1:8765", "--outdir", str(tmp_path),
                        "--account", "qa", "--password", "fixture-only-password",
                        "--skip-defects", "--skip-model"]) == 0
    argv = child.call_args.args[0]
    assert "--voice-live" not in argv and "--voice-expect-unconfigured" not in argv
    assert "--tested-commit" not in argv and "fixture-only-password" not in argv
    assert child.call_args.kwargs["env"]["P6_ACCEPTANCE_PASSWORD"] == "fixture-only-password"
    assert "fixture-only-password" not in capsys.readouterr().out


def test_demo_without_voice_consent_makes_no_requests():
    client = Mock()
    demo_flow.demo_voice(client, 1, 2, enabled=False)
    assert not client.mock_calls


def test_custom_outdir_feeds_its_own_defect_ledger(tmp_path, monkeypatch):
    failure = case("TC-P6-LOCAL-999")
    (tmp_path / "acceptance-latest.json").write_text(json.dumps({"results": [failure]}), "utf-8")
    def run(argv, **kwargs):
        assert Path(argv[1]).name == "track_defects.py"
        return track_defects.main(argv[2:])
    monkeypatch.setattr(run_p6, "run", run)
    assert run_p6.main(["--base-url", "https://qa.example", "--outdir", str(tmp_path),
                        "--skip-acceptance", "--skip-model"]) == 0
    assert failure["case_id"] in (tmp_path / "defect-ledger.md").read_text("utf-8")


def test_only_unmodified_seed_content_is_approved(tmp_path, monkeypatch, capsys):
    path = tmp_path / "demo.sqlite3"
    demo.ensure_database(path)
    seed = demo.load_seed(demo.SEED_DIR)
    first, second, third = seed["story_versions"][:3]
    knowledge = seed["knowledge_items"][0]
    with closing(demo.connect(path)) as db, db:
        db.execute("UPDATE story_pages SET body_text=? WHERE version_id=? AND page_no=1",
                   ("改为待审核内容", first["id"]))
        db.execute("UPDATE knowledge_items SET content=? WHERE item_key=?", ("待审核内容", knowledge["item_key"]))
        for table, record in (("storybooks", {**seed["storybooks"][0], "id": 9876}),
                              ("story_versions", {**first, "id": 9876, "storybook_id": 9876})):
            db.execute(f"INSERT INTO {table} ({', '.join(record)}) VALUES ({', '.join('?' for _ in record)})",
                       tuple(record.values()))
        extra = {**knowledge, "item_key": "qa-review-fixture"}
        fields = ", ".join(extra)
        db.execute(f"INSERT INTO knowledge_items ({fields}) VALUES ({', '.join('?' for _ in extra)})",
                   tuple(extra.values()))
    monkeypatch.setattr(sys, "argv", ["prepare", "--yes", "--db", str(path), "--limit", "2",
                                      "--password", "local-fixture-password"])
    assert demo.main() == demo.main() == 0
    assert "local-fixture-password" not in capsys.readouterr().out
    with closing(demo.connect(path)) as db:
        statuses = dict(db.execute("SELECT id,status FROM story_versions"))
        assert [statuses[v["id"]] for v in (first, second, third)] == ["DRAFT", "PUBLISHED", "PUBLISHED"]
        assert db.execute("SELECT COUNT(*) FROM story_versions v JOIN accounts a ON a.id=v.reviewed_by_id "
                          "WHERE a.account='seed:course-originals' AND v.status='PUBLISHED'").fetchone()[0] == 20
        assert statuses[9876] == "DRAFT"
        draft_keys = set(row[0] for row in db.execute("SELECT item_key FROM knowledge_items WHERE status='DRAFT'"))
        assert draft_keys == {knowledge["item_key"], "qa-review-fixture"}


def test_demo_reports_only_new_publications_and_branches(tmp_path, monkeypatch, capsys):
    path = tmp_path / "demo.sqlite3"
    demo.ensure_database(path)
    with closing(demo.connect(path)) as db, db:
        # One legacy draft and one already-published original both need demo branches.
        db.execute("UPDATE story_versions SET status='DRAFT', reviewed_by_id=NULL, reviewed_at=NULL WHERE id=2")
    monkeypatch.setattr(sys, "argv", ["prepare", "--db", str(path), "--yes", "--limit", "2"])

    for expected_ids, expected_branches in (([2], 2), ([], 0)):
        assert demo.main() == 0
        result = json.loads(capsys.readouterr().out)
        assert result["storyVersionIds"] == expected_ids
        assert result["publishedCount"] == len(expected_ids)
        assert result["demoBranchesCreated"] == expected_branches
        assert result["check"]["publishedVersions"] == 20


def test_preserve_manual_scores_and_reject_changed_plan(tmp_path):
    args = ["--outdir", str(tmp_path), "--limit", "1"]
    assert run_model_compare.main(args) == 0
    path = tmp_path / "scoring.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns, rows = reader.fieldnames, list(reader)
    rows[0].update({key: "0" for key in columns if key.startswith("rater1_")})
    rows[0]["rater1_note"] = "保留评审意见"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(reversed(rows))
    saved = path.read_bytes()
    assert run_model_compare.main(args) == 0
    with pytest.raises(SystemExit, match="独立 --outdir"):
        run_model_compare.main([*args, "--conditions", "baseline,page_only,domain"])
    assert path.read_bytes() == saved


def test_equal_raters_and_partial_scores():
    plan = {"caseCount": 1, "callBudget": 2, "conditions": ["baseline", "domain"],
            "cases": [{"caseId": "C"}], "dimensions": ["x"]}
    plan["planHash"] = summarize_model_compare.plan_fingerprint(plan)
    scores = [dict(caseId="C", condition=c, rater1_x=a, rater2_x=b)
              for c, a, b in [("baseline", 2, 0), ("domain", 0, 2)]]
    results = [dict(caseId="C", condition=c, runId="run-A", planHash=plan["planHash"],
                    model="fixture", success=True, output=c)
               for c in plan["conditions"]]
    for row, output in zip(scores, results):
        row["outputKey"] = summarize_model_compare.output_key(output)
    for unscorable in ([], [dict(row, success=False) for row in results],
                       [dict(row, runId="run-B") for row in results], [dict(row, output="changed") for row in results],
                       [results[0], dict(results[1], runId="run-B")]):
        rejected = summarize_model_compare.summarize(plan, unscorable, scores, ["x"])
        assert rejected["scoredRows"] == 0 and not rejected["paired"]["cases"]
    result = summarize_model_compare.summarize(plan, results, scores, ["x"])
    assert result["scoredRows"] == 2
    assert result["paired"]["cases"] == [{"caseId": "C", "baseline": 1, "domain": 1, "outcome": "平"}]
    other_model = [results[0], dict(results[1], model="other")]
    other_scores = [scores[0], dict(scores[1], outputKey=summarize_model_compare.output_key(other_model[1]))]
    assert not summarize_model_compare.summarize(plan, other_model, other_scores, ["x"])["paired"]["cases"]
    scores[0]["rater2_x"] = None
    partial = summarize_model_compare.summarize(plan, results, scores, ["x"])
    assert partial["scoredRows"] == 2 and not partial["paired"]["cases"]


def test_document_uses_report_results_and_version():
    template = build_test_doc.TEMPLATE.read_text("utf-8")
    payload = {"meta": {"testedCommit": "1122334", "counts": {"FAIL": 0}}, "results": [case()]}
    text, commit, summary = build_test_doc.render(template, payload)
    assert commit == "1122334" and "1 项，0 通过、1 失败" in summary
    assert summary in text and "| 失败（FAIL） | 1 |" in text
    assert "| 结论 | FAIL（失败） |" in text and "| 实际 | 样本观测 |" in text
    assert "本轮黑盒验收有失败" in text and "32 通过、0 失败" not in text
    assert "本轮未执行" in text and "{{" not in text
    with pytest.raises(ValueError, match="不一致"):
        build_test_doc.render(template, payload, "7654321")
    empty, missing, _ = build_test_doc.render(template, {"results": []})
    assert missing == "未记录" and "本轮没有黑盒用例记录" in empty
    short_id, _, _ = build_test_doc.render(template, {"results": [case("FAIL-ONLY")]})
    assert "| OTHER | 1 |" in short_id and "本轮黑盒验收有失败" in short_id


def test_committed_archives_honestly_record_branch_scope_and_cookie_flags():
    reports = sorted((Path(__file__).parent / "reports").glob("acceptance-*.json"))
    assert len(reports) >= 2
    for path in reports:
        report = json.loads(path.read_text("utf-8"))
        for row in report["results"]:
            for attrs in row["evidence"].get("cookieFlags", {}).values():
                assert set(attrs) == {"secure", "httpOnly", "sameSiteLax"}
                assert all(type(value) is bool for value in attrs.values())
        if path.name == "acceptance-20261118-212206.json":
            branch = next(r for r in report["results"] if r["case_id"] == "TC-P6-BRANCH-001")
            assert branch["status"] == "WARN" and "无环" in branch["detail"]



def test_bound_scoring_sheet_preserves_each_run(tmp_path, capsys):
    assert run_model_compare.main(["--outdir", str(tmp_path), "--limit", "1"]) == 0
    plan = json.loads((tmp_path / "model-compare-plan.json").read_text("utf-8"))
    case_id = plan["cases"][0]["caseId"]
    def report(run):
        path = tmp_path / f"results-{run}.jsonl"
        rows = [dict(caseId=case_id, condition=c, runId=run, planHash=plan["planHash"],
                     model="fixture", success=True, output="sample")
                for c in plan["conditions"]]
        path.write_text("\n".join(json.dumps(row) for row in rows), "utf-8")
        return ["--outdir", str(tmp_path), "--results", str(path)]

    first = report("A")
    assert summarize_model_compare.main(first) == 0
    sheet = next(tmp_path.glob("scoring-A-*.csv"))
    with sheet.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns, rows = reader.fieldnames, list(reader)
    for row in rows:
        for rater in ("rater1", "rater2"):
            for dimension in plan["dimensions"]:
                row[f"{rater}_{dimension}"] = "2"
    with sheet.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    saved = sheet.read_bytes()
    assert summarize_model_compare.main(first) == 0
    summary = tmp_path / "model-compare-summary.json"
    assert json.loads(summary.read_text("utf-8"))["paired"]["ties"] == 1
    summary_files = {p: p.read_bytes() for p in tmp_path.glob("model-compare-summary.*")}
    missing = "rater1_" + plan["dimensions"][0]
    incomplete = tmp_path / "missing-column.csv"
    with incomplete.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[c for c in columns if c != missing], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    assert summarize_model_compare.main([*first, "--scores", str(incomplete)]) == 2
    assert missing in capsys.readouterr().err
    assert all(p.read_bytes() == original for p, original in summary_files.items())
    shifted = tmp_path / "extra-cell.csv"
    with shifted.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for line, row in enumerate(rows, start=2):
            values = [row[col] for col in columns]
            if row["condition"] == "domain":
                values.insert(columns.index("outputExcerpt") + 1, "0")
                bad_line = line
            writer.writerow(values)
    assert summarize_model_compare.main([*first, "--scores", str(shifted)]) == 2
    error = capsys.readouterr().err
    assert shifted.name in error and f"第 {bad_line} 行" in error
    assert all(p.read_bytes() == original for p, original in summary_files.items())
    second = report("B")
    assert summarize_model_compare.main(second) == 0
    assert json.loads(summary.read_text("utf-8"))["scoredRows"] == 0
    assert summarize_model_compare.main([*second, "--scores", str(sheet)]) == 0
    assert json.loads(summary.read_text("utf-8"))["unmatchedScoreRows"] == 2
    assert sheet.read_bytes() == saved


@pytest.mark.parametrize("allow_model", [False, True])
def test_local_runner_only_uses_its_own_service(tmp_path, monkeypatch, allow_model):
    monkeypatch.setattr(run_p6, "local_revision", lambda _: "abcdef0")
    server = Mock()
    server.poll.side_effect = [None, 1, 1]
    launcher = Mock(return_value=server)
    monkeypatch.setattr(run_p6.subprocess, "Popen", launcher)
    monkeypatch.setenv("MODEL_API_KEY", "fixture-only-marker")
    response = MagicMock()
    response.__enter__.return_value.status = 200
    response.__enter__.return_value.headers = {"X-P6-Instance": "another-run"}
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(run_p6.urllib.request, "build_opener", lambda *args: opener)
    monkeypatch.setattr(run_p6.time, "sleep", lambda _: None)
    run = Mock()
    monkeypatch.setattr(run_p6, "run", run)
    flags = ["--text-model-live"] if allow_model else []
    assert run_p6.main(["--start-local", "--skip-prepare", "--outdir", str(tmp_path), *flags]) == 2
    run.assert_not_called()
    assert launcher.call_args.kwargs["env"]["MODEL_API_KEY"] == ("fixture-only-marker" if allow_model else "")
    server.poll.side_effect = None
    server.poll.return_value = None
    response.__enter__.return_value.headers = {"X-P6-Instance": "this-run"}
    assert run_p6.wait_health("http://127.0.0.1", server, "this-run")


def test_rate_limited_login_preserves_full_report_and_independent_checks(tmp_path, monkeypatch):
    client = Mock()
    client.post.return_value = httpx.Response(429)
    monkeypatch.setattr(run_acceptance, "ApiSession", Mock(return_value=client))
    independent = ("test_health", "test_unknown_route", "test_method_not_allowed", "test_anonymous_protected",
                   "test_security_headers", "test_openapi_coverage", "test_frontend", "test_public_tls", "test_http_redirect")
    for name in independent:
        monkeypatch.setattr(run_acceptance.Acceptance, name, Mock(return_value="fixture"))
    result = run_acceptance.main(["--profile", "public", "--base-url", "https://fixture.example", "--account", "fixture",
                                  "--http-url", "http://fixture.example", "--frontend-url", "https://fixture.example",
                                  "--outdir", str(tmp_path)])
    assert result == 2
    rows = json.loads((tmp_path / "acceptance-latest.json").read_text("utf-8"))["results"]
    statuses = {row["case_id"]: row["status"] for row in rows}
    assert len(rows) == len(statuses) == 42
    for name in independent:
        getattr(run_acceptance.Acceptance, name).assert_called_once()
    assert all(statuses[key] == "BLOCKED" for key in ("TC-P6-AUTH-004", "TC-P6-SESS-005", "TC-P6-REPORT-001"))
    assert statuses["TC-P6-PUB-001"] == statuses["TC-P6-PUB-002"] == "PASS"


def test_report_reads_terminal_state_and_activity_fields(monkeypatch):
    acceptance = run_acceptance.Acceptance.__new__(run_acceptance.Acceptance)
    acceptance.args = run_acceptance.parse_args([])
    acceptance.session_id = 4
    acceptance.a = Mock()
    def reply(status, activities):
        return httpx.Response(200, json={"data": {"report": {"status": status, "activities": activities}}})
    activity = {"materials": ["纸"], "steps": ["画一幅图"]}
    acceptance.a.get.side_effect = [reply("PENDING", []), reply("READY", [activity])]
    monkeypatch.setattr(run_acceptance.time, "sleep", lambda _: None)
    assert "就绪" in acceptance.test_report_status()
    assert "1 项活动" in acceptance.test_activities()
    assert acceptance.a.get.call_count == 2
    acceptance.report["activities"] = [{"materials": [], "steps": ["画一幅图"]}]
    with pytest.raises(AssertionError, match="materials"):
        acceptance.test_activities()
    acceptance.a.get.side_effect = [reply("FAILED", [])]
    assert isinstance(acceptance.test_report_status(), common.Warn)
    acceptance.a.get.side_effect = None
    acceptance.a.get.return_value = reply("PENDING", [])
    acceptance.args.timeout = 0.002
    with pytest.raises(common.Blocked, match="超时"):
        acceptance.test_report_status()


def test_pinned_story_never_falls_back_to_first_page():
    books = [{"storyVersionId": 2, "pages": [1, 2, 3, 4]}]
    client = Mock()
    assert common.select_story(client, books, None) == books[0]
    client.get.assert_not_called()
    client.get.return_value = httpx.Response(404, json={})
    with pytest.raises(common.Blocked, match="99"):
        common.select_story(client, books, 99)
    client.get.assert_called_once_with("/api/v1/story-versions/99")
    client.get.return_value = httpx.Response(200, json={"data": {"storyVersionId": 99, "pages": [1]}})
    assert common.select_story(client, books, 99)["storyVersionId"] == 99


def test_defect_ids_identify_the_whole_case():
    cases = [case("TC-P6-API-001"), case("TC-P6-PROF-001")]
    defects = track_defects.auto_defects({"results": cases})
    assert {d["id"] for d in defects} == {"DEF-P6-AUTO-API-001", "DEF-P6-AUTO-PROF-001"}


@pytest.mark.parametrize(("account", "role"), [("fixture-admin", "GUARDIAN"), ("fixture", "ADMIN")])
def test_demo_role_conflict_rolls_back(tmp_path, monkeypatch, account, role):
    path = tmp_path / "demo.sqlite3"
    demo.ensure_database(path)
    with closing(demo.connect(path)) as db, db:
        demo.ensure_account(db, account, "fixture-password", role)
    monkeypatch.setattr(sys, "argv", ["prepare_demo_data.py", "--db", str(path), "--account", "fixture", "--yes"])
    with pytest.raises(SystemExit, match="现有角色"):
        demo.main()
    with closing(demo.connect(path)) as db:
        rows = db.execute("SELECT account, role FROM accounts WHERE account LIKE 'fixture%'").fetchall()
        assert [tuple(row) for row in rows] == [(account, role)]
        assert db.execute("SELECT count(*) FROM story_versions WHERE status='PUBLISHED'").fetchone()[0] == 20
