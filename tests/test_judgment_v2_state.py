"""J2: structured v2 state (judgment_context.v1) + docs-aligned QUESTION_SET.

RED-first contract tests for the Jev fixture rebuild (card t_b07f0b99):
every v2 case's replay state must be a judgment_context.v1 snapshot, and
QUESTION_SET must follow the Jev docs wire shape (criteria-less noul,
J1 priority option set).
"""
import json
from pathlib import Path

import pytest

from sentinel.evaluation import QUESTION_SET
from sentinel.judgment import state_snapshot, validate_judgment_state

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "judgment"

PRIORITY_OPTIONS = {"ignore", "low", "medium", "high", "critical"}


def _load_cases():
    with (FIXTURES / "cases.v2.jsonl").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ---------------------------------------------------------------------------
# judgment_context.v1 validation + structured state conversion
# ---------------------------------------------------------------------------

def test_validate_judgment_state_accepts_canonical_snapshot():
    ctx = state_snapshot(
        {"hazards": [{"id": "h1", "object": "rock", "confidence": 0.9,
                      "position_relation": "on_trail", "resolved": False}],
         "patrollers": [], "trail_status": "open", "workflow": "survey"},
        {"detection_id": "h1", "source": "drone_1"})
    assert validate_judgment_state(ctx) == ctx  # pure check, returns input


def test_validate_judgment_state_rejects_prose_and_wrong_version():
    with pytest.raises(ValueError):
        validate_judgment_state({"observation": "prose blob", "mission": "x", "operations": []})
    ctx = state_snapshot(
        {"hazards": [{"id": "h1", "object": "rock", "confidence": 0.9,
                      "position_relation": "on_trail", "resolved": False}],
         "patrollers": [], "trail_status": "open", "workflow": "survey"},
        {"detection_id": "h1", "source": "drone_1"})
    bad = dict(ctx, context_version="judgment_context.v9")
    with pytest.raises(ValueError):
        validate_judgment_state(bad)


def test_every_v2_case_state_validates_as_judgment_context_v1():
    for case in _load_cases():
        validate_judgment_state(case["state"])  # raises on prose/legacy shape


def test_v2_case_state_carries_structured_detection_and_context():
    for case in _load_cases():
        state = case["state"]
        assert state["context_version"] == "judgment_context.v1"
        obs = state["observation"]
        assert isinstance(obs.get("object"), str) and obs["object"]
        conf = obs.get("perception_confidence")
        assert conf is None or (isinstance(conf, (int, float)) and 0.0 <= conf <= 1.0)
        assert obs.get("position_relation") in {"on_trail", "off_trail", "trail_edge",
                                                "inside_zone_boundary", "at_emergency_gate",
                                                "near_treeline", "trail_junction", "off_corridor"}
        assert isinstance(state["operations"].get("patroller_available"), bool)
        assert isinstance(state["mission"].get("trail_status"), str)
        assert "observation_prose" not in state  # prose must not ride along


def test_state_snapshot_projects_size_m():
    mountain = {"hazards": [{"id": "h1", "object": "fallen_tree", "confidence": 0.97,
                             "position_relation": "on_trail", "size_m": 4.0, "resolved": False}],
                "patrollers": [], "trail_status": "closed_pending_inspection",
                "workflow": "daily_opening_inspection"}
    ctx = state_snapshot(mountain, {"detection_id": "h1", "source": "drone_1"})
    assert ctx["observation"]["size_m"] == 4.0


# ---------------------------------------------------------------------------
# QUESTION_SET: Jev-docs wire shape
# ---------------------------------------------------------------------------

def test_question_set_noul_questions_have_no_criteria_dicts():
    by_name = {q["name"]: q for q in QUESTION_SET}
    for name in ("hazard_relevant", "human_review_needed", "more_evidence_needed"):
        assert by_name[name]["type"] == "noul"
        assert by_name[name]["criteria"] is None, f"{name} must not carry criteria (Jev docs)"


def test_question_set_priority_options_match_j1_option_set():
    by_name = {q["name"]: q for q in QUESTION_SET}
    assert by_name["priority"]["type"] == "choice"
    assert set(by_name["priority"]["criteria"]) == PRIORITY_OPTIONS
    # score-type keeps its consistent criteria list
    assert by_name["priority_scored"]["type"] == "score"
    assert by_name["priority_scored"]["criteria"] == ["not a hazard", "minor", "needs inspection", "urgent"]


def test_question_set_replay_through_jev_judge_sends_criteria_less_noul():
    """Seam test: the wire payload must not carry criteria dicts on noul."""
    from sentinel.judgment import JevJudge

    seen = {}

    def transport(payload):
        seen["payload"] = payload
        return {"answers": {
            q["name"]: ({"type": "noul", "noul": 0.5} if q["type"] == "noul"
                        else {"type": "choice", "choice": "low",
                              "probabilities": {k: 1.0 / len(q["criteria"]) for k in q["criteria"]}}
                        if q["type"] == "choice"
                        else {"type": "score", "score": 1.0,
                              "legend": {"0": "not a hazard", "1": "minor",
                                         "2": "needs inspection", "3": "urgent"}})
            for q in QUESTION_SET}}

    monkey = pytest.MonkeyPatch()
    monkey.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    try:
        judge = JevJudge(api_key_env="SENTINEL_TEST_KEY", transport=transport)
        judge.evaluate({"context_version": "judgment_context.v1"}, QUESTION_SET)
    finally:
        monkey.undo()

    questions = seen["payload"]["questions"]
    for name in ("hazard_relevant", "human_review_needed", "more_evidence_needed"):
        assert "criteria" not in questions[name], f"{name} sent criteria on the wire"
    assert set(questions["priority"]["criteria"]) == PRIORITY_OPTIONS