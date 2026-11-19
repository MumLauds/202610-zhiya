"""Checks common to the deployment and restored-browser evidence."""
import re


TRANSCRIPT = "小船停在哪里"
EDITED = "纸船为什么停在绿叶旁？"


def check_login(record):
    if (record.get("loginHttp") != 201 or record.get("secureSessionCookie") is not True
            or record.get("httpOnlySessionCookie") is not True or not record.get("frontendAsset", "").startswith("/assets/")
            or not re.fullmatch(r"[0-9a-f]{64}", record.get("frontendSha256", ""))):
        raise ValueError("Incomplete browser login, session cookie or frontend asset evidence")


def check_history(label, reader_hash, sid):
    if reader_hash != f"#reader/{sid}" or label != f"阅读记录 #{sid} · 浏览 1 页 · 已完成":
        raise ValueError("Browser history or reader address differs from the completed session")


def check_detail(detail, row, stage):
    session = detail["session"]
    report = detail["report"]
    if (session["readingSessionId"] != row["sessionId"] or session["storyVersionId"] != row["versionId"]
            or session["revision"] != row["revision"] or session["status"] != row["status"]
            or session["visitedPageNumbers"] != row["pages"] or report["readingSessionId"] != row["sessionId"]
            or report["summary"] != row["summary"] or report["status"] != row["reportStatus"]):
        raise ValueError(f"Browser reading or report differs from the {stage} database snapshot")
    observed = [(e["interactionEventId"], e["eventType"], e["pageNumber"]) for e in detail["events"]]
    expected = [(e["id"], e["eventType"], e["pageNumber"]) for e in row["events"]]
    if observed != expected:
        raise ValueError(f"Browser events differ from the {stage} database snapshot")
