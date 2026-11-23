"""Check synthetic evaluation fixture structure, not model quality."""

import json
from pathlib import Path
from runpy import run_path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "qa" / "p6" / "co_reading_eval"
VALIDATOR = run_path(str(FIXTURE_DIR / "validate_visit_cases.py"))["validate"]


@pytest.fixture
def visit_fixture():
    return json.loads((FIXTURE_DIR / "visit-cases-v2.json").read_text(encoding="utf-8"))


def test_checked_in_visit_fixture_is_valid(visit_fixture):
    VALIDATOR(visit_fixture)


@pytest.mark.parametrize("defect", ["duplicate-id", "real-adapter-override", "empty-answer", "non-boolean-mock-flag"])
def test_malformed_visit_fixture_is_rejected(visit_fixture, defect):
    first, second = visit_fixture["scenarios"][:2]
    if defect == "duplicate-id":
        second["id"] = first["id"]
    elif defect == "real-adapter-override":
        first["steps"][0]["adapter"] = "TIMEOUT"
    elif defect == "empty-answer":
        first["steps"][0]["answer"] = " "
    else:
        first["mockOnly"] = 0
    with pytest.raises(AssertionError):
        VALIDATOR(visit_fixture)
