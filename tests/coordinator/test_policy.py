"""D8/D9: the policy seam. ``one_call`` builds a schema-valid single-tool-call
result, and ``bind_context`` hands a ``ContextualPolicy`` the live
``DecisionState``/``LegalSet`` instead of the bare wire request."""
from __future__ import annotations

from sentinel.contracts import AgentModelResult
from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.legal_tools import SELECT_PLAN, load_catalogue_plans
from sentinel.coordinator.policy import PolicyContext, bind_context, one_call

from .harness import AGENT_RUN_ID, RUN_ID, CoordinatorBoundary, close, envelope, open_harness


def _request(run_id: str = RUN_ID) -> dict:
    return {
        "contract_version": "sentinel.v0", "wire_type": "agent_request",
        "agent_run_id": AGENT_RUN_ID, "run_id": run_id, "state_version": 1,
        "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
        "deadline": "2099-09-17T00:00:00Z", "phase": "PLAN",
        "event_context": [], "evidence_context": [], "legal_tools": ["wait_for_event"],
        "previous_result": None, "user_request": "inspect one station",
    }


def test_one_call_builds_a_schema_valid_single_tool_call_result():
    """(a) one_call(req, "wait_for_event", {...}) validates as AgentModelResult.
    Deviation from the plan snippet: the real wire schema requires
    wait_for_event's arguments to carry event_types alongside reason."""
    arguments = {"reason": "x", "event_types": ["RUN_CREATED"]}
    result = one_call(_request(), "wait_for_event", arguments)
    validated = AgentModelResult.from_dict(result)  # raises if schema-invalid
    assert validated.status == "TOOL_CALLS"
    assert validated.agent_run_id == AGENT_RUN_ID
    assert len(validated.tool_calls) == 1
    assert validated.tool_calls[0].tool_name == "wait_for_event"
    assert validated.tool_calls[0].arguments == arguments


def test_bind_context_hands_the_contextual_policy_the_live_legal_set(tmp_path):
    """(b) a ContextualPolicy fake receives a PolicyContext whose
    legal.decision_point matches the harness state."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        catalogue = load_catalogue_plans()

        received: dict = {}

        class FakeContextualPolicy:
            def decide(self, request, context):
                received["request"] = request
                received["context"] = context
                return one_call(request, "wait_for_event", {"reason": "recorded", "event_types": ["RUN_CREATED"]})

        policy = bind_context(FakeContextualPolicy(), repo, catalogue)
        result = policy(_request())

        assert isinstance(received["context"], PolicyContext)
        assert received["context"].legal.decision_point == SELECT_PLAN
        assert received["context"].state.run_id == RUN_ID
        assert received["context"].state.plan_id is None
        assert received["request"]["run_id"] == RUN_ID
        assert AgentModelResult.from_dict(result).status == "TOOL_CALLS"
    finally:
        close(repo)


class _RecordingPolicy:
    """A policy that also exposes the optional ``forget(agent_run_id)`` hook
    (D8), like ``OpenAICompatibleAgentModel``, and records every call to it."""

    def __init__(self, result_fn):
        self._result_fn = result_fn
        self.forgotten: list[str] = []

    def __call__(self, request: dict) -> dict:
        return self._result_fn(request)

    def forget(self, agent_run_id: str) -> None:
        self.forgotten.append(agent_run_id)


def test_agent_loop_forgets_the_policy_when_the_model_calls_finish(tmp_path):
    """D8: AgentLoop calls the policy's ``forget`` hook when run() returns
    via a ``finish`` tool call."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        boundary = CoordinatorBoundary(repo, gateway)
        policy = _RecordingPolicy(
            lambda request: one_call(request, "finish", {"status": "NEEDS_REVIEW", "summary": "hand back"})
        )

        AgentLoop(boundary, policy, max_turns=2).run(RUN_ID, AGENT_RUN_ID, "inspect one station")

        assert policy.forgotten == [AGENT_RUN_ID]
    finally:
        close(repo)


def test_agent_loop_forgets_the_policy_on_waiting_for_approval(tmp_path):
    """D8: run() also returns via WAITING_FOR_APPROVAL without the model ever
    proposing ``finish`` (the canonical resume boundary exercised by
    test_one_station_flow.py) -- the policy must still be told the run is
    over, since a resumed run may reuse the same agent_run_id against the
    same long-lived policy instance."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        boundary = CoordinatorBoundary(repo, gateway)
        policy = _RecordingPolicy(
            lambda request: one_call(request, "inspection.propose_plan", {"catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"})
        )

        result = AgentLoop(boundary, policy, max_turns=2).run(RUN_ID, AGENT_RUN_ID, "inspect one station")

        assert result["error_code"] == "WAITING_FOR_APPROVAL"
        assert policy.forgotten == [AGENT_RUN_ID]
    finally:
        close(repo)


def test_agent_loop_forgets_the_policy_on_max_turns_exhaustion(tmp_path):
    """D8: MAX_TURNS exhaustion is also a run() return that never reaches
    ``finish``."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        boundary = CoordinatorBoundary(repo, gateway)
        policy = _RecordingPolicy(lambda request: one_call(request, "state.get_summary", {}))

        result = AgentLoop(boundary, policy, max_turns=1).run(RUN_ID, AGENT_RUN_ID, "inspect one station")

        assert result["error_code"] == "MAX_TURNS"
        assert policy.forgotten == [AGENT_RUN_ID]
    finally:
        close(repo)
