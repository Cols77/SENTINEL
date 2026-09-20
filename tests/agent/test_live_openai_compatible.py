"""B10 live LLM acceptance gate: opt-in flag semantics only.

The scenario-driven acceptance gate itself -- the bounded-horizon evaluator
that graded a model's tool sequence -- has moved to
``tests/agent/test_live_gate_real_loop.py`` (P1 Task 7, D1/D10): it used to
build ``AgentRequest`` objects by hand from ``live_scenarios.json``, with
ids (task_id, perception_result_id, evidence_artifact_id, ...) smuggled
straight into the fixture's ``event_context``/``evidence_context`` because
nothing actually computed them. That fixture shape is gone; ids now come
from the real coordinator repository through ``DecisionState``/
``legal_set``, read by driving the real ``AgentLoop`` against the real
gateway, so the LLM (and RULE, and Jev) is judged on the fixed loop, not a
hand-rolled substitute for it. See ``test_live_gate_real_loop.py`` for the
evaluator, the scenarios, and the opt-in live-provider tests.

This module keeps only the gate-flag semantics: whether the opt-in live
run would be skipped or attempted.

Run the opt-in live gate (operator consent required):
    SENTINEL_RUN_LIVE_LLM_ACCEPTANCE=1 uv run python -m pytest \
        tests/agent/test_live_gate_real_loop.py -q -o addopts=''
"""

from __future__ import annotations

GATE_FLAG = "SENTINEL_RUN_LIVE_LLM_ACCEPTANCE"


def _gate_turned_on() -> bool:
    import os

    return os.environ.get(GATE_FLAG) == "1"


def test_gate_flag_missing_skips_explicitly():
    """Without the flag the gate never runs and says so explicitly."""
    import os

    import pytest

    if _gate_turned_on():
        pytest.skip("gate is enabled in this session; skip-check is not applicable")
    assert os.environ.get(GATE_FLAG) != "1"
    assert not _gate_turned_on()
