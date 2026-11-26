"""Replay local v6 synthetic cases through its real HTTP entry point.

This runner does not fabricate browser timing. The server must record evidence.
Use a brand-new record directory and a separate browser batch, if needed.
"""
import argparse
import json
from pathlib import Path
import time
from urllib.request import ProxyHandler, Request, build_opener


def replay(port, mode, record_dir, case_ids, *, version=6):
    base = f"http://127.0.0.1:{port}"
    journal = record_dir / f"v{version}-evidence.jsonl"
    if not record_dir.is_dir():
        raise ValueError("Start the demo with this --record-dir first.")
    http = build_opener(ProxyHandler({}))

    def request(route, data=None):
        req = Request(base + route, data=None if data is None else json.dumps(data).encode(),
                      headers={"Origin": base, "Content-Type": "application/json"})
        with http.open(req, timeout=10) as res:
            return json.load(res)

    def await_upstream(session, number):
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            if journal.exists():
                for line in journal.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # May be reading the last line during append.
                    if (row.get("session_id"), row.get("turn_number"), row.get("event")) == (
                        session, number, "upstream",
                    ):
                        return
            time.sleep(0.2)
        raise ValueError("Late upstream result not recorded; stop this batch.")

    catalogue = request("/v6/catalog")
    if catalogue["mode"] != mode or catalogue.get("version", 6) != version:
        raise ValueError("Server mode differs from --mode. No turns were sent.")
    selected = [case for case in catalogue["cases"] if not case_ids or case["id"] in case_ids]
    if not selected or (case_ids and {case["id"] for case in selected} != set(case_ids)):
        raise ValueError("Unknown case ID in --case.")
    for case in selected:
        state = request("/v6/start", {"case_id": case["id"]})
        if state["mode"] != mode:
            raise ValueError("Server mode changed. Stop this batch.")
        session = state["session"]
        for number, turn in enumerate(case["turns"]):
            if state["page_id"] != turn["page"]:
                state = request("/v6/page", {"session": session, "page_id": turn["page"]})
            if state["mode"] != mode or state["session"] != session or state["page_id"] != turn["page"]:
                raise ValueError("Server mode/session/page mismatch.")
            receipt = request("/v6/turn", {"session": session, "number": number})
            if (receipt["session"], receipt["number"], receipt["page_id"], receipt["child"]) != (
                session, number, turn["page"], turn["child"],
            ) or receipt["mode"] != mode:
                raise ValueError("Receipt does not match the fixed synthetic turn.")
            reason = receipt.get("fallback_reason")
            print(case["id"], number + 1, receipt["page_id"], receipt["kind"], reason or "-", flush=True)
            if reason == "UPSTREAM_BUSY":
                raise ValueError("Another upstream call is pending; stop without retrying this turn.")
            if reason == "DEMO_DEADLINE":
                await_upstream(session, number)
    if not journal.is_file():
        raise ValueError("Evidence journal missing. This run cannot be reviewed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--mode", choices=("MOCK", "LIVE"), required=True)
    parser.add_argument("--record-dir", type=Path, required=True)
    parser.add_argument("--case", action="append", help="Repeat to select several case IDs; omit for all.")
    parser.add_argument("--version", choices=(6, 7), type=int, default=6)
    args = parser.parse_args()
    try:
        replay(args.port, args.mode, args.record_dir, args.case, version=args.version)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
