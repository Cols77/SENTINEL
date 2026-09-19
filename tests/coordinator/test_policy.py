"""D8/D9: the policy seam. ``one_call`` builds a schema-valid single-tool-call
result, and ``bind_context`` hands a ``ContextualPolicy`` the live
``DecisionState``/``LegalSet`` instead of the bare wire request."""
from __future__ import annotations

from sentinel.contracts import AgentModelResult
from sentinel.coordinator.legal_tools import SELECT_PLAN, load_catalogue_plans
from sentinel.coordinator.policy import PolicyContext, bind_context, one_call

from .harness import AGENT_RUN_ID, RUN_ID, close, envelope, open_harness


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
