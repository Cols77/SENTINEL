"""P2 Task 5: shadow advisor scoring (``score``) with Wilson lower bounds.

``score`` consumes the same ``judgment``-record shape ``ShadowLadder``
writes (Task 2) -- optionally carrying a ``turn_id`` for fixture-driven
sweeps, or bare ``run_id``/``state_version``/``decision_point`` for live
audit files (Task 6) -- and a truth mapping keyed either way, so the exact
same scorer works for the offline fixture sweep and for live rehearsal data
scored against operator choices."""
from __future__ import annotations

from sentinel.shadow_eval import REPORT_HEADER, score, wilson_lower_95


def _record(*, turn_id, decision_point, label, band, error=None, latency_ms=10,
            advisor="test", confidence=0.9, run_id="RUN-1", state_version=1, repeat=0):
    return {
        "record": "judgment", "turn_id": turn_id, "run_id": run_id, "state_version": state_version,
        "decision_point": decision_point, "repeat": repeat,
        "advisors": {advisor: {"label": label, "confidence": confidence, "band": band,
                                "model": "test-model", "latency_ms": latency_ms, "error": error}},
    }


# --- wilson_lower_95 ---------------------------------------------------

def test_wilson_lower_95_24_of_24():
    assert abs(wilson_lower_95(24, 24) - 0.862) < 0.01


def test_wilson_lower_95_60_of_60():
    assert abs(wilson_lower_95(60, 60) - 0.940) < 0.01


def test_wilson_lower_95_zero_n_is_zero():
    assert wilson_lower_95(0, 0) == 0.0


# --- score(): a perfect advisor -----------------------------------------

def test_perfect_advisor_gives_agreement_1_and_false_act_0():
    truth = {f"sp-{i:03d}": ("PLAN_A" if i % 2 == 0 else "escalate") for i in range(10)}
    records = [
        _record(turn_id=turn_id, decision_point="SELECT_PLAN", label=label, band="HIGH")
        for turn_id, label in truth.items()
    ]
    report = score(records, truth)
    stats = report["test"]["SELECT_PLAN"]
    assert stats["n"] == 10
    assert stats["agreement"] == 1.0
    assert stats["false_act"] == 0
    assert stats["selective_accuracy"] == 1.0
    assert stats["error_rate"] == 0.0


# --- score(): always-create_task at HIGH confidence ----------------------

def test_always_create_task_false_act_equals_number_of_escalate_labelled_turns():
    truth = {}
    records = []
    for i in range(20):
        turn_id = f"eg-{i:03d}"
        label = "escalate" if i % 3 == 0 else "create_task"
        truth[turn_id] = label
        records.append(_record(turn_id=turn_id, decision_point="EVIDENCE_GATE",
                                label="create_task", band="HIGH", confidence=0.95))
    expected_false_act = sum(1 for v in truth.values() if v == "escalate")
    report = score(records, truth)
    stats = report["test"]["EVIDENCE_GATE"]
    assert stats["false_act"] == expected_false_act
    assert stats["escalation_rate"] == 0.0
    assert stats["coverage_high"] == 1.0


# --- score(): error_rate --------------------------------------------------

def test_advisor_with_errors_gives_correct_error_rate():
    truth = {f"sp-{i:03d}": "escalate" for i in range(8)}
    records = []
    for i, turn_id in enumerate(truth):
        errored = i < 3
        records.append(_record(
            turn_id=turn_id, decision_point="SELECT_PLAN",
            label=None if errored else "escalate", band=None if errored else "HIGH",
            error="TimeoutError: advisor timed out" if errored else None,
        ))
    report = score(records, truth)
    stats = report["test"]["SELECT_PLAN"]
    assert stats["n"] == 8
    assert stats["error_rate"] == 3 / 8


# --- score(): stability across repeats ------------------------------------

def test_stability_counts_label_flips_across_repeats_of_the_same_turn():
    truth = {"sp-001": "PLAN_A"}
    records = [
        _record(turn_id="sp-001", decision_point="SELECT_PLAN", label="PLAN_A", band="HIGH", repeat=0),
        _record(turn_id="sp-001", decision_point="SELECT_PLAN", label="PLAN_A", band="HIGH", repeat=1),
        _record(turn_id="sp-001", decision_point="SELECT_PLAN", label="PLAN_B", band="MID", repeat=2),
    ]
    report = score(records, truth)
    stats = report["test"]["SELECT_PLAN"]
    assert stats["stability"] < 1.0  # the one turn flipped across its 3 repeats


def test_stability_is_1_when_every_repeat_of_every_turn_agrees():
    truth = {"sp-001": "PLAN_A", "sp-002": "escalate"}
    records = [
        _record(turn_id="sp-001", decision_point="SELECT_PLAN", label="PLAN_A", band="HIGH", repeat=0),
        _record(turn_id="sp-001", decision_point="SELECT_PLAN", label="PLAN_A", band="HIGH", repeat=1),
        _record(turn_id="sp-002", decision_point="SELECT_PLAN", label="escalate", band="HIGH", repeat=0),
        _record(turn_id="sp-002", decision_point="SELECT_PLAN", label="escalate", band="HIGH", repeat=1),
    ]
    report = score(records, truth)
    assert report["test"]["SELECT_PLAN"]["stability"] == 1.0


# --- score(): truth keyed by (run_id, state_version, decision_point) -----

def test_truth_can_be_keyed_by_run_id_state_version_decision_point_tuple():
    record = {
        "record": "judgment", "run_id": "RUN-9", "state_version": 4, "decision_point": "EVIDENCE_GATE",
        "advisors": {"jev": {"label": "create_task", "confidence": 0.9, "band": "HIGH",
                              "model": "m", "latency_ms": 5, "error": None}},
    }
    truth = {("RUN-9", 4, "EVIDENCE_GATE"): "create_task"}
    report = score([record], truth)
    assert report["jev"]["EVIDENCE_GATE"]["agreement"] == 1.0


# --- score(): records with no matching truth are excluded ----------------

def test_records_with_no_matching_truth_are_excluded():
    truth = {"sp-001": "PLAN_A"}
    records = [
        _record(turn_id="sp-001", decision_point="SELECT_PLAN", label="PLAN_A", band="HIGH"),
        _record(turn_id="sp-999", decision_point="SELECT_PLAN", label="PLAN_A", band="HIGH"),
    ]
    report = score(records, truth)
    assert report["test"]["SELECT_PLAN"]["n"] == 1


# --- score(): multiple advisors and decision points are reported separately

def test_multiple_advisors_and_decision_points_are_reported_independently():
    truth = {"sp-001": "PLAN_A", "eg-001": "create_task"}
    records = [
        {"record": "judgment", "turn_id": "sp-001", "decision_point": "SELECT_PLAN",
         "advisors": {
             "jev": {"label": "PLAN_A", "confidence": 0.9, "band": "HIGH", "model": "m", "latency_ms": 5, "error": None},
             "llm": {"label": "escalate", "confidence": 0.9, "band": "HIGH", "model": "m", "latency_ms": 5, "error": None},
         }},
        {"record": "judgment", "turn_id": "eg-001", "decision_point": "EVIDENCE_GATE",
         "advisors": {
             "jev": {"label": "create_task", "confidence": 0.9, "band": "HIGH", "model": "m", "latency_ms": 5, "error": None},
             "llm": {"label": "create_task", "confidence": 0.9, "band": "HIGH", "model": "m", "latency_ms": 5, "error": None},
         }},
    ]
    report = score(records, truth)
    assert set(report) == {"jev", "llm"}
    assert set(report["jev"]) == {"SELECT_PLAN", "EVIDENCE_GATE"}
    assert report["jev"]["SELECT_PLAN"]["agreement"] == 1.0
    assert report["llm"]["SELECT_PLAN"]["agreement"] == 0.0


# --- report header ---------------------------------------------------------

def test_report_header_states_no_authority_and_promotion_criteria():
    assert REPORT_HEADER == (
        "Shadow only. No advisor has authority. Promotion criteria live in P3 "
        "and require n ≥ 60 HIGH-band picks per decision point."
    )
