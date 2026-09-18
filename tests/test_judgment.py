import pytest

from sentinel.judgment import BoundedJudge, StaticJudge


def test_static_judge_boolean_matches_configured_rule():
    judge = StaticJudge(
        booleans={"hazard_relevant": (True, 0.92)},
        choices={"priority": ("high", {"ignore": 0.05, "low": 0.1, "medium": 0.25, "high": 0.6})},
    )
    r = judge.boolean({"detection": "rock"}, "Is this a hazard?", name="hazard_relevant")
    assert r == {"value": True, "probability": 0.92}
    c = judge.choice({"mission": {}}, "Pick priority.", options=["ignore", "low", "medium", "high"], name="priority")
    assert c["value"] == "high"
    assert set(c["probabilities"]) == {"ignore", "low", "medium", "high"}


def test_static_judge_unknown_name_fails_closed():
    judge = StaticJudge(booleans={}, choices={})
    with pytest.raises(KeyError):
        judge.boolean({}, "q?", name="missing")


def test_jev_judge_unavailable_is_explicit(monkeypatch):
    # JevJudge must never be the silent default; constructing one without a key env is an error.
    from sentinel.judgment import JevJudge
    with pytest.raises(ValueError):
        JevJudge(api_key_env="")  # empty -> fail closed at construction
