"""Scenario replay harness: WP1 live_scenarios.json through a BoundedJudge.

Mirrors the B10 live-acceptance pass criteria (read-only reuse of the
wp1-integration-kanban worktree's executed gate): each scenario turn maps to
ONE batched judge evaluate() call; the turn passes iff the judge's tool
choice matches the turn's expected_tool AND every asserted argument matches.
Zero network — StaticJudge doubles only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from sentinel.judgment import JudgeUnavailable, StaticJudge, replay_scenarios

# Read-only fixture copy of the WP1 gate's scenario document (shape reference).
SCENARIOS_PATH = Path(__file__).parent / "fixtures" / "scenario_replay_live.json"


def _write_scenarios(path: Path, document: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_replay_passes_when_static_judge_pinned_to_expected_answers(tmp_path):
    scenarios = {
        "scenario_set": "sentinel.live-llm-acceptance.v1",
        "scenarios": [
            {
                "name": "initial_plan_one_station",
                "user_request": "Survey only the LEFT_ALLEY_MIDDLE station.",
                "phase": "PLAN",
                "state_version": 1,
                "legal_tools": ["inspection.propose_plan", "wait_for_event", "finish"],
                "event_context": [{"sequence_no": 1, "event_type": "RUN_CREATED",
                                   "summary": "one-station survey"}],
                "evidence_context": [],
                "catalogue": {"catalogue_id": "SURVEY_CATALOGUE_V1", "plans": [
                    {"id": "PLAN_MARKER_VERIFIED_6", "mode": "MARKER_VERIFIED",
                     "station_count": 6, "requires_marker_verification": True, "excluded": True},
                    {"id": "PLAN_ROUTE_ONLY_2_ZONE", "mode": "ROUTE_ONLY_2_ZONE",
                     "station_count": 2, "requires_marker_verification": False, "excluded": False},
                ]},
                "expected_turns": [
                    {"expected_tool": "inspection.propose_plan",
                     "expectations": {"arguments.catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"}},
                    {"expected_tool": "wait_for_event", "expectations": {}},
                ],
                "coordinator_feedback": {"kind": "plan", "run_status": "WAITING_FOR_APPROVAL",
                                         "safe_summary": "plan submitted; approval required"},
            }
        ],
    }
    path = _write_scenarios(tmp_path / "live_scenarios.json", scenarios)

    class ScriptedJudge:
        """Per-turn pinned answers; each evaluate() consumes one script entry."""

        def __init__(self, per_turn):
            self._per_turn = list(per_turn)
            self.calls: list[list[str]] = []

        def evaluate(self, state, questions):
            self.calls.append([q["name"] for q in questions])
            return dict(self._per_turn.pop(0))

    judge = ScriptedJudge([
        {"tool_choice": {"value": "inspection.propose_plan"},
         "arg_choice": {"value": "PLAN_ROUTE_ONLY_2_ZONE"}},
        {"tool_choice": {"value": "wait_for_event"}},
    ])
    report = replay_scenarios(path, judge)
    assert judge.calls[0] == ["tool_choice", "arg_choice"]
    assert judge.calls[1] == ["tool_choice"]
    assert report["scenarios"][0]["passed"] is True
    assert report["scenarios"][0]["turns"][0]["passed"] is True
    assert report["scenarios"][0]["turns"][0]["tool_choice"] == "inspection.propose_plan"
    assert report["scenarios"][0]["turns"][1]["tool_choice"] == "wait_for_event"


def test_replay_fails_when_tool_choice_wrong(tmp_path):
    scenarios = {
        "scenario_set": "s", "scenarios": [{
            "name": "approval_wait", "user_request": "Wait.", "phase": "AWAITING_APPROVAL",
            "state_version": 4, "legal_tools": ["state.get_summary", "wait_for_event", "finish"],
            "event_context": [], "evidence_context": None,
            "expected_turns": [{"expected_tool": ["wait_for_event", "finish"], "expectations": {}}],
        }]
    }
    path = _write_scenarios(tmp_path / "s.json", scenarios)
    judge = StaticJudge(answers={"tool_choice": {"value": "state.get_summary"}})
    report = replay_scenarios(path, judge)
    turn = report["scenarios"][0]["turns"][0]
    assert turn["passed"] is False
    assert "state.get_summary" in turn["failures"][0]


def test_arg_choice_criteria_are_catalogue_plan_ids_and_missing_arg_fails(tmp_path):
    scenarios = {
        "scenario_set": "s", "scenarios": [{
            "name": "plan", "user_request": "u", "phase": "PLAN", "state_version": 1,
            "legal_tools": ["inspection.propose_plan", "finish"],
            "event_context": [], "evidence_context": [],
            "catalogue": {"catalogue_id": "C", "plans": [
                {"id": "PLAN_A", "mode": "M", "station_count": 1,
                 "requires_marker_verification": False, "excluded": False},
                {"id": "PLAN_B", "mode": "M", "station_count": 2,
                 "requires_marker_verification": True, "excluded": True},
            ]},
            "expected_turns": [
                {"expected_tool": "inspection.propose_plan",
                 "expectations": {"arguments.catalogue_plan_id": "PLAN_B"}},
            ],
        }]
    }
    path = _write_scenarios(tmp_path / "s.json", scenarios)
    seen: dict[str, Any] = {}

    class RecordingJudge:
        def evaluate(self, state, questions):
            seen["questions"] = questions
            seen["state_keys"] = sorted(state)
            return {q["name"]: {"value": "PLAN_A"} for q in questions}

    report = replay_scenarios(path, RecordingJudge())
    names = [q["name"] for q in seen["questions"]]
    assert names == ["tool_choice", "arg_choice"]
    arg_q = seen["questions"][1]
    assert arg_q["type"] == "choice"
    assert set(arg_q["criteria"]) == {"PLAN_A", "PLAN_B"}
    assert sorted(seen["state_keys"]) == [
        "catalogue", "event_context", "evidence_context",
        "legal_tools", "phase", "state_version", "user_request"]
    turn = report["scenarios"][0]["turns"][0]
    assert turn["passed"] is False
    assert any("inspection.propose_plan" in f for f in turn["failures"])
    assert any("PLAN_B" in f and "PLAN_A" in f for f in turn["failures"])


def test_no_catalogue_argument_assertion_still_scored_not_silently_passed(tmp_path):
    """Mirrors the real qualifying_evidence_to_task shape: no catalogue key.

    A turn asserting arguments.* without a catalogue must still produce an
    arg_choice question (criteria = expected values) and fail when the judge
    picks outside the expectation — never pass on tool match alone.
    """
    scenarios = {
        "scenario_set": "s", "scenarios": [{
            "name": "qualifying_evidence_to_task", "user_request": "u",
            "phase": "EVIDENCE_REVIEW", "state_version": 2,
            "legal_tools": ["task.create_from_evidence", "finish"],
            "event_context": [], "evidence_context": [{"id": "EV1"}],
            "expected_turns": [{
                "expected_tool": "task.create_from_evidence",
                "expectations": {"arguments.perception_result_id": "PERC_9",
                                 "arguments.task_id": "TASK_A"},
            }],
        }]
    }
    path = _write_scenarios(tmp_path / "s.json", scenarios)
    seen: dict[str, Any] = {}

    class RecordingJudge:
        def evaluate(self, state, questions):
            seen["questions"] = questions
            # Correct tool, wrong argument value on every asserted field.
            return {q["name"]: {"value": "PERC_OTHER"} for q in questions}

    report = replay_scenarios(path, RecordingJudge())
    names = [q["name"] for q in seen["questions"]]
    assert names[0] == "tool_choice"
    assert all(n.startswith("arg_choice") for n in names[1:])
    # Criteria = the expected argument values (no catalogue fallback),
    # one question per asserted field.
    assert {c for q in seen["questions"][1:] for c in q["criteria"]} == {"PERC_9", "TASK_A"}
    turn = report["scenarios"][0]["turns"][0]
    assert turn["passed"] is False
    assert any("PERC_9" in f for f in turn["failures"])


def test_multi_argument_assertions_scored_independently(tmp_path):
    scenarios = {
        "scenario_set": "s", "scenarios": [{
            "name": "multi", "user_request": "u", "phase": "PLAN", "state_version": 1,
            "legal_tools": ["inspection.propose_plan", "finish"],
            "event_context": [], "evidence_context": [],
            "expected_turns": [{
                "expected_tool": "inspection.propose_plan",
                "expectations": {"arguments.catalogue_plan_id": "PLAN_A",
                                 "arguments.task_id": "TASK_1"},
            }],
        }]
    }
    path = _write_scenarios(tmp_path / "s.json", scenarios)

    class RecordingJudge:
        def evaluate(self, state, questions):
            seen["names"] = [q["name"] for q in questions]
            return {q["name"]: {"value": "PLAN_A"} for q in questions}

    seen: dict[str, Any] = {}
    report = replay_scenarios(path, RecordingJudge())
    turn = report["scenarios"][0]["turns"][0]
    assert turn["passed"] is False
    assert any("task_id" in f for f in turn["failures"])


def test_judge_missing_arg_answer_fails_closed(tmp_path):
    scenarios = {
        "scenario_set": "s", "scenarios": [{
            "name": "n", "user_request": "u", "phase": "PLAN", "state_version": 1,
            "legal_tools": ["t"], "event_context": [], "evidence_context": [],
            "expected_turns": [{
                "expected_tool": "t",
                "expectations": {"arguments.perception_result_id": "P1"},
            }],
        }]
    }
    path = _write_scenarios(tmp_path / "s.json", scenarios)
    judge = StaticJudge(answers={"tool_choice": {"value": "t"}})
    report = replay_scenarios(path, judge)
    turn = report["scenarios"][0]["turns"][0]
    assert turn["passed"] is False
    assert any("no answer" in f for f in turn["failures"])


def test_coordinator_feedback_only_after_first_turn(tmp_path):
    scenarios = {
        "scenario_set": "s", "scenarios": [{
            "name": "fb", "user_request": "u", "phase": "PLAN", "state_version": 1,
            "legal_tools": ["wait_for_event"], "event_context": [],
            "evidence_context": [],
            "expected_turns": [{"expected_tool": "wait_for_event", "expectations": {}},
                               {"expected_tool": "wait_for_event", "expectations": {}}],
            "coordinator_feedback": {"kind": "wait", "safe_summary": "still pending"},
        }]
    }
    path = _write_scenarios(tmp_path / "s.json", scenarios)
    states: list[dict] = []

    class RecordingJudge:
        def evaluate(self, state, questions):
            states.append(state)
            return {q["name"]: {"value": "wait_for_event"} for q in questions}

    replay_scenarios(path, RecordingJudge())
    assert "coordinator_feedback" not in states[0]
    assert states[1]["coordinator_feedback"]["safe_summary"] == "still pending"


def test_no_catalogue_argument_assertions_are_never_silently_dropped(tmp_path):
    """Mirrors the real 'qualifying_evidence_to_task' shape: arguments
    asserted with NO catalogue key. B10 fails the turn for every unmet
    expectation; the replay must ask and score each one (fail closed)."""
    scenarios = {
        "scenario_set": "s", "scenarios": [{
            "name": "qualifying_evidence_to_task", "user_request": "u",
            "phase": "EVIDENCE_QUALIFIED", "state_version": 7,
            "legal_tools": ["task.create", "finish"],
            "event_context": [], "evidence_context": [],
            "expected_turns": [{
                "expected_tool": "task.create",
                "expectations": {
                    "arguments.perception_result_id": "5555",
                    "arguments.task_id": "2222",
                },
            }],
        }]
    }
    path = _write_scenarios(tmp_path / "s.json", scenarios)
    seen: dict[str, Any] = {}

    class RecordingJudge:
        def evaluate(self, state, questions):
            seen["questions"] = {q["name"]: q["criteria"] for q in questions}
            return {q["name"]: {"value": "WRONG"} for q in questions}

    report = replay_scenarios(path, RecordingJudge())
    turn = report["scenarios"][0]["turns"][0]
    # every asserted argument got its own question whose criteria include
    # the expected values even without a catalogue
    assert "arg_choice_0_perception_result_id" in seen["questions"]
    assert seen["questions"]["arg_choice_0_perception_result_id"] == {"5555": "5555"}
    assert "arg_choice_1_task_id" in seen["questions"]
    assert seen["questions"]["arg_choice_1_task_id"] == {"2222": "2222"}
    # and every unmet assertion fails the turn (B10 semantics)
    assert turn["passed"] is False
    assert any("perception_result_id" in f and "5555" in f for f in turn["failures"])
    assert any("task_id" in f and "2222" in f for f in turn["failures"])

    # with matching answers the same shape passes
    class GoodJudge:
        def evaluate(self, state, questions):
            return {q["name"]: {"value": next(iter(q["criteria"]))} for q in questions}

    report = replay_scenarios(path, GoodJudge())
    assert report["scenarios"][0]["passed"] is True


def test_judge_unavailable_propagates_fail_closed(tmp_path):
    scenarios = {"scenario_set": "s", "scenarios": [{
        "name": "n", "user_request": "u", "phase": "PLAN", "state_version": 1,
        "legal_tools": ["finish"], "event_context": [], "evidence_context": [],
        "expected_turns": [{"expected_tool": "finish", "expectations": {}}],
    }]}
    path = _write_scenarios(tmp_path / "s.json", scenarios)

    class BrokenJudge:
        def evaluate(self, state, questions):
            raise JudgeUnavailable("env var KEY not set")

    report = replay_scenarios(path, BrokenJudge())
    assert report["scenarios"][0]["passed"] is False
    assert "env var KEY not set" in report["scenarios"][0]["error"]
