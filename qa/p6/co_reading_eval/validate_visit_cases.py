"""Sanity-check the synthetic visit fixture; this is not a model evaluation."""

import json
from pathlib import Path


def main() -> None:
    data = json.loads(Path(__file__).with_name("visit-cases-v2.json").read_text(encoding="utf-8"))
    assert data["dataOrigin"] == "SYNTHETIC_ONLY"
    assert set(data["ageBands"]) == {"AGE_3_4", "AGE_4_5", "AGE_5_6"}
    story = data["story"]
    assert set(story["pages"]) == {"1", "2", "3"}
    assert story["startVisit"] == [1, 2] and story["revisitPath"] == [1, 2, 3, 2]
    scenarios = data["scenarios"]
    assert len(scenarios) == len({case["id"] for case in scenarios}) == 11
    assert sum(not case["mockOnly"] for case in scenarios) == 6
    assert {
        "MULTI_TURN", "SEMANTIC_DUPLICATE", "ALREADY_ANSWERED", "NATURAL_CLOSE",
        "CAUSAL_ERROR", "TIMEOUT", "INVALID_JSON", "PRIVACY", "VISIT_ISOLATION",
    } <= {case["category"] for case in scenarios}
    for case in scenarios:
        assert case["expect"].strip()
        assert 1 <= len(case["steps"]) <= 3
        for step in case["steps"]:
            assert 1 <= len(step["question"].strip()) <= 200
            assert 1 <= len(step["answer"].strip()) <= 200
            assert "adapter" not in step or case["mockOnly"]
    print("OK: 11 synthetic visit scenarios, 3 ages; fixture structure only")


if __name__ == "__main__":
    main()
