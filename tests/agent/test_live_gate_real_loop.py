"""Task 7 (D1, D10): fair re-evaluation of the LLM agent (architecture B).

The six failed live runs (P1 plan, "why this task") graded models that saw
only event types (D1) and ids hand-smuggled into ``live_scenarios.json``
(D10) to compensate for context the loop never actually computed. This
module drives the *real* coordinator -- the same gateway, repository,
``DecisionState`` and ``legal_set`` the production loop uses -- through
``AgentLoop(repository=..., catalogue=...)`` for every scenario, so a policy
(RULE, Jev, or -- opt-in, live, Step 4 -- the LLM adapter) is judged on the
fixed loop, not on a hand-rolled substitute.

``live_scenarios.json`` therefore names only the operator request, the
starting harness steps that put the real repository into the scenario's
starting state, and the expected ordered tool-name sequence: no
``event_context``, no UUID, nothing smuggled.

Run the opt-in live gate (Step 4, operator consent required -- never run
from this module without it):
    SENTINEL_RUN_LIVE_LLM_ACCEPTANCE=1 uv run python -m pytest \
        tests/agent/test_live_gate_real_loop.py -q -o addopts=''
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from sentinel.adapter.llm.config import AdapterConfigError, load_config
from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
from sentinel.contracts import AgentRequest, validate_document
from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.legal_tools import EVIDENCE_GATE, SELECT_PLAN, load_catalogue_plans
from sentinel.coordinator.policy import ContextualPolicy, PolicyContext, bind_context, one_call

from tests.coordinator.harness import (
    AGENT_RUN_ID,
    RUN_ID,
    CoordinatorBoundary,
    FakeWorker,
    close,
    envelope,
    open_harness,
)

GATE_FLAG = "SENTINEL_RUN_LIVE_LLM_ACCEPTANCE"
SCENARIOS_PATH = Path(__file__).resolve().parent / "live_scenarios.json"
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_MINIMAL_SCENARIO_KEYS = {
    "name", "user_request", "setup", "expected_tool_sequence",
    "expected_result_error_code", "max_turns",
}


def _gate_turned_on() -> bool:
    import os

    return os.environ.get(GATE_FLAG) == "1"


def _skip_message() -> str:
    return (
        "live LLM acceptance gate is opt-in; set "
        f"{GATE_FLAG}=1 and provide provider configuration (.env or environment)"
    )


def _load_scenarios() -> list[dict[str, Any]]:
    document = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenarios = document["scenarios"]
    assert isinstance(scenarios, list) and scenarios, "live_scenarios.json must define scenarios"
    return scenarios


def _scenario_by_name(name: str) -> dict[str, Any]:
    return next(item for item in _load_scenarios() if item["name"] == name)


# ---------------------------------------------------------------------------
# Starting harness steps (named, composable) -- the only thing a scenario may
# use to reach its starting state. Every id a policy needs (plan_id, task_id,
# perception_result_id, artifact_id, ...) is read back live from the
# repository by DecisionState/legal_set; none of it is hand-authored here.
# ---------------------------------------------------------------------------


def _plan_reference(repo) -> dict[str, Any]:
    plan = repo.connection.execute("SELECT * FROM plans").fetchone()
    return {
        "plan_id": plan["plan_id"],
        "catalogue_id": plan["catalogue_id"],
        "catalogue_revision": plan["catalogue_revision"],
        "plan_sha256": plan["plan_sha256"],
        "station_ids": json.loads(plan["station_ids_json"]),
    }


def _step_create_run(repo, gateway, user_request: str) -> None:
    gateway.submit(envelope("RUN_CREATE", {"request_text": user_request}, sequence=1, actor_type="OPERATOR"))


def _step_propose_route_only_plan(repo, gateway, user_request: str) -> None:
    gateway.submit(envelope("AGENT_INTENT_PROPOSE", {
        "intent": "REQUEST_OPERATOR_APPROVAL", "catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE",
    }, sequence=2, actor_type="AGENT"))


def _step_approve_pending_plan(repo, gateway, user_request: str) -> None:
    reference = _plan_reference(repo)
    gateway.submit(envelope("OPERATOR_DECIDE", {
        "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
    }, sequence=3, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))


def _step_report_preflight(repo, gateway, user_request: str) -> None:
    reference = _plan_reference(repo)
    FakeWorker(gateway).preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])


def _step_dispatch_station(repo, gateway, user_request: str) -> None:
    reference = _plan_reference(repo)
    FakeWorker(gateway).dispatch(
        RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE", repo.read_state(RUN_ID).to_dict()["state_version"]
    )


def _step_capture_and_perceive(repo, gateway, user_request: str) -> None:
    reference = _plan_reference(repo)
    FakeWorker(gateway).capture_and_perceive(RUN_ID, reference["plan_id"], reference["plan_sha256"])


SETUP_STEPS: dict[str, Callable[[Any, Any, str], None]] = {
    "create_run": _step_create_run,
    "propose_route_only_plan": _step_propose_route_only_plan,
    "approve_pending_plan": _step_approve_pending_plan,
    "report_preflight": _step_report_preflight,
    "dispatch_station": _step_dispatch_station,
    "capture_and_perceive": _step_capture_and_perceive,
}


def run_real_loop_scenario(name: str, policy: Any, *, tmp_path: Path) -> dict[str, Any]:
    """Drive ``name`` through the real coordinator gateway and ``AgentLoop``.

    ``policy`` is either a plain ``Policy`` (``dict -> dict``, e.g. a bound
    LLM adapter call) or a ``ContextualPolicy`` (``.decide(request,
    PolicyContext)``, e.g. ``RulePolicy``/``JevPolicy``/a scripted fake), in
    which case it is adapted with ``bind_context`` against this scenario's
    own repository -- the same seam Task 4/5 policies use, so the LLM is
    judged through the identical path.
    """
    scenario = _scenario_by_name(name)
    repo, gateway = open_harness(tmp_path)
    try:
        for step in scenario["setup"]:
            SETUP_STEPS[step](repo, gateway, scenario["user_request"])
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()
        base_policy = bind_context(policy, repo, catalogue) if isinstance(policy, ContextualPolicy) else policy

        calls: list[str] = []
        requests: list[dict[str, Any]] = []

        def recording_policy(request: dict[str, Any]) -> dict[str, Any]:
            requests.append(request)
            result = base_policy(request)
            if result.get("status") == "TOOL_CALLS" and result.get("tool_calls"):
                calls.append(result["tool_calls"][0]["tool_name"])
            return result

        loop = AgentLoop(
            boundary, recording_policy, max_turns=int(scenario.get("max_turns", 4)),
            repository=repo, catalogue=catalogue,
        )
        result = loop.run(RUN_ID, AGENT_RUN_ID, scenario["user_request"])
        return {"calls": calls, "requests": requests, "result": result}
    finally:
        close(repo)


# ---------------------------------------------------------------------------
# Hermetic (Steps 1-3): a scripted, correct policy must reach the expected
# tool sequence on the real loop for every scenario.
# ---------------------------------------------------------------------------


_READ_TOOLS = ("state.get_summary", "state.get_recent_events")


def assert_deliverables(calls, expected, outcome):
    """Operator scoring decision, 2026-09-20: a scenario passes when its
    deliverables appear **in order** within the turn budget. Reading state is a
    legal, bounded act -- every read still burns a turn -- so it must not fail a
    run by itself. Exact-list matching graded tool discipline instead of task
    completion, and made the read tools unusable by construction.

    A call that is neither a deliverable nor a read is still a failure: a wrong
    mutating call must never pass.
    """
    remaining = list(expected)
    for call in calls:
        if remaining and call == remaining[0]:
            remaining.pop(0)
            continue
        assert call in _READ_TOOLS or call == "finish", (
            f"unexpected call {call!r}; expected deliverables {expected} in order", outcome)
    assert not remaining, (f"missing deliverables {remaining} (calls: {calls})", outcome)


class _ScriptedCorrectPolicy:
    """A ``ContextualPolicy`` that picks the operator-intended candidate at
    each judgment point (by *label*, never by hand-filled arguments -- the
    real ``legal_set()`` already fills correct arguments, Task 2) and acts on
    the sole candidate everywhere else."""

    def __init__(self, choices: Mapping[str, str]):
        self._choices = choices

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        legal = context.legal
        if legal.decision_point in self._choices:
            candidate = legal.by_label(self._choices[legal.decision_point])
        elif len(legal.candidates) == 1:
            candidate = legal.candidates[0]
        else:
            raise AssertionError(
                f"script has no choice for ambiguous decision point {legal.decision_point!r} "
                f"(labels={legal.labels!r})"
            )
        return one_call(request, candidate.tool_name, candidate.arguments)


_SCRIPTED_CHOICES: dict[str, dict[str, str]] = {
    "initial_plan_one_station": {SELECT_PLAN: "PLAN_ROUTE_ONLY_2_ZONE"},
    "qualifying_evidence_to_task": {EVIDENCE_GATE: "create_task"},
    # approval_wait: the run starts WAITING_FOR_APPROVAL, so AgentLoop
    # returns before ever calling the policy (D9 fixed at the loop level,
    # not by asking the model to recognize the wait itself).
    "approval_wait": {},
}


@pytest.mark.parametrize("name", [scenario["name"] for scenario in _load_scenarios()])
def test_scripted_correct_policy_passes_every_scenario_on_the_real_loop(name, tmp_path):
    scenario = _scenario_by_name(name)
    policy = _ScriptedCorrectPolicy(_SCRIPTED_CHOICES[name])
    outcome = run_real_loop_scenario(name, policy, tmp_path=tmp_path)

    assert_deliverables(outcome["calls"], scenario["expected_tool_sequence"], outcome)
    if "expected_result_error_code" in scenario:
        assert outcome["result"].get("error_code") == scenario["expected_result_error_code"], outcome["result"]
    for request in outcome["requests"]:
        validate_document(request)


def test_scenario_fixture_has_no_smuggled_context():
    """D10: the fixture names only user_request + starting harness steps +
    the expected tool sequence. No event_context, no evidence_context, no
    legal_tools override, no coordinator_feedback -- and no UUID anywhere,
    since every id now comes from the real loop reading the real repository."""
    raw_text = SCENARIOS_PATH.read_text(encoding="utf-8")
    document = json.loads(raw_text)
    for scenario in document["scenarios"]:
        assert set(scenario) <= _MINIMAL_SCENARIO_KEYS, scenario.keys()
        assert "event_context" not in scenario
        assert "evidence_context" not in scenario
    assert not _UUID_RE.search(raw_text), "scenario fixture must not smuggle any UUID"


def test_gate_flag_missing_skips_explicitly():
    """Without the flag the live gate never runs and says so explicitly."""
    import os

    if _gate_turned_on():
        pytest.skip("gate is enabled in this session; skip-check is not applicable")
    assert os.environ.get(GATE_FLAG) != "1"
    assert not _gate_turned_on()


# ---------------------------------------------------------------------------
# Live (Step 4, operator consent required): the real OpenAI-compatible
# provider, driven through the same real-loop scenario runner. Skipped
# unless SENTINEL_RUN_LIVE_LLM_ACCEPTANCE=1. This module never sets that
# flag itself.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def adapter() -> OpenAICompatibleAgentModel:
    if not _gate_turned_on():
        pytest.skip(_skip_message())
    try:
        config = load_config()
    except AdapterConfigError as exc:
        pytest.fail(
            "live LLM acceptance gate is enabled but provider configuration is "
            f"not loadable: {exc}",
            pytrace=False,
        )
    return OpenAICompatibleAgentModel(config)


def _llm_policy(model: OpenAICompatibleAgentModel) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _policy(request: dict[str, Any]) -> dict[str, Any]:
        result = model.propose(AgentRequest.from_dict(request))
        return result.to_dict()

    return _policy


@pytest.mark.live_llm
@pytest.mark.parametrize("name", [scenario["name"] for scenario in _load_scenarios()])
def test_live_scenario_on_the_real_loop(name, adapter, tmp_path):
    scenario = _scenario_by_name(name)
    outcome = run_real_loop_scenario(name, _llm_policy(adapter), tmp_path=tmp_path)
    assert_deliverables(outcome["calls"], scenario["expected_tool_sequence"], outcome)
