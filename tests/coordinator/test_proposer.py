from __future__ import annotations

import json

from sentinel.contracts import AgentModelResult, AgentRequest, AgentToolCall
from sentinel.coordinator import LlmProposer
from sentinel.coordinator.decision_state import DecisionState
from sentinel.coordinator.legal_tools import EVIDENCE_GATE, SELECT_PLAN, Candidate, LegalSet
from sentinel.coordinator.policy import ContextualPolicy, PolicyContext
from sentinel.coordinator.proposer import JevProposer
from sentinel.coordinator.shadow import JsonlAuditSink, MemoryAuditSink

from .harness import AGENT_RUN_ID, RUN_ID


PLAN_ID = "PLAN_ROUTE_ONLY_2_ZONE"
PLAN_DESCRIPTION = "inspect both route stations"


def _request() -> dict:
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_request",
        "agent_run_id": AGENT_RUN_ID,
        "run_id": RUN_ID,
        "state_version": 7,
        "allowed_intent_schema_id":
            "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
        "deadline": "2099-09-17T00:00:00Z",
        "phase": "PLAN",
        "event_context": [],
        "evidence_context": [],
        "legal_tools": ["state.get_summary", "task.create"],
        "previous_result": None,
        "user_request": "inspect one station",
    }


def _state() -> DecisionState:
    return DecisionState(
        run_id=RUN_ID,
        state_version=7,
        run_status="AGENT_TURN",
        agent_phase="PLAN",
        emergency_latched=False,
        request_text="inspect one station",
        plan_id=None,
        plan_mode=None,
        plan_station_ids=(),
        plan_approved=False,
        detection=None,
        open_task_station_ids=(),
        task_id=None,
        task_status=None,
    )


def _select_plan_context() -> PolicyContext:
    legal = LegalSet(
        SELECT_PLAN,
        (
            Candidate(
                "inspection.propose_plan",
                {"catalogue_plan_id": PLAN_ID},
                PLAN_ID,
                PLAN_DESCRIPTION,
            ),
            Candidate(
                "finish",
                {"status": "NEEDS_REVIEW", "summary": "no plan fits"},
                "escalate",
                "ask the operator to choose",
            ),
        ),
    )
    return PolicyContext(_state(), legal)


def _result(tool_name: str, arguments: dict) -> AgentModelResult:
    return AgentModelResult.from_dict(
        {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_result",
            "agent_run_id": AGENT_RUN_ID,
            "status": "TOOL_CALLS",
            "tool_calls": [
                {
                    "contract_version": "sentinel.v0",
                    "wire_type": "agent_tool_call",
                    "tool_name": tool_name,
                    "arguments": arguments,
                }
            ],
        }
    )


class FakeAdapter:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[tuple[AgentRequest, PolicyContext]] = []
        self.forgotten: list[str] = []

    def propose(self, request: AgentRequest, context: PolicyContext):
        self.calls.append((request, context))
        return self.result

    def forget(self, agent_run_id: str) -> None:
        self.forgotten.append(agent_run_id)


class RaisingAdapter:
    def propose(self, request: AgentRequest, context: PolicyContext):
        raise RuntimeError("provider unavailable")


def _assert_local_escalation(result: dict) -> None:
    assert result["status"] == "TOOL_CALLS"
    assert len(result["tool_calls"]) == 1
    call = result["tool_calls"][0]
    assert call["tool_name"] == "finish"
    assert call["arguments"]["status"] == "NEEDS_REVIEW"
    assert len(call["arguments"]["summary"]) <= 256


def test_proposer_is_a_contextual_policy_and_narrows_legal_tools_without_mutating_request():
    adapter = FakeAdapter(_result(
        "inspection.propose_plan",
        {"catalogue_plan_id": PLAN_ID, "reason": "best coverage"},
    ))
    request = _request()
    original = dict(request)
    proposer = LlmProposer(adapter, MemoryAuditSink())

    proposer.decide(request, _select_plan_context())

    assert isinstance(proposer, ContextualPolicy)
    typed_request, context = adapter.calls[0]
    assert isinstance(typed_request, AgentRequest)
    assert typed_request.legal_tools == ["inspection.propose_plan", "finish"]
    assert context.legal.decision_point == SELECT_PLAN
    assert request == original


def test_valid_current_proposal_preserves_optional_reason_and_writes_exact_audit_record():
    reason = "  best coverage for the operator request  "
    adapter = FakeAdapter(_result(
        "inspection.propose_plan",
        {"catalogue_plan_id": PLAN_ID, "reason": reason},
    ))
    sink = MemoryAuditSink()
    context = _select_plan_context()

    result = LlmProposer(adapter, sink).decide(_request(), context)

    assert result["tool_calls"][0]["arguments"] == {
        "catalogue_plan_id": PLAN_ID,
        "reason": "best coverage for the operator request",
    }
    assert [r for r in sink.records if r["record"] == "proposal_recommendation"] == [
        {
            "record": "proposal_recommendation",
            "run_id": RUN_ID,
            "state_version": 7,
            "decision_point": SELECT_PLAN,
            "label": PLAN_ID,
            "reason": "best coverage for the operator request",
            "facts": {
                "operator_request": "inspect one station",
                "candidate_description": PLAN_DESCRIPTION,
            },
        }
    ]


def test_model_finish_is_coerced_to_the_proposers_own_escalation():
    model_result = _result("finish", {"status": "DONE", "summary": "model says done"})
    result = LlmProposer(FakeAdapter(model_result), MemoryAuditSink()).decide(
        _request(), _select_plan_context()
    )

    _assert_local_escalation(result)
    assert result != model_result.to_dict()


def test_forbidden_mutating_tool_is_coerced_to_local_escalation():
    result = LlmProposer(
        FakeAdapter(_result("task.create", {
            "perception_result_id": "33333333-3333-4333-8333-333333333333",
            "section_id": "LEFT_ALLEY_MIDDLE",
            "evidence_artifact_id": "44444444-4444-4444-8444-444444444444",
        })),
        MemoryAuditSink(),
    ).decide(_request(), _select_plan_context())

    _assert_local_escalation(result)


def test_unknown_plan_is_coerced_to_local_escalation():
    result = LlmProposer(
        FakeAdapter(_result("inspection.propose_plan", {
            "catalogue_plan_id": "PLAN_UNKNOWN", "reason": "invented plan"
        })),
        MemoryAuditSink(),
    ).decide(_request(), _select_plan_context())

    _assert_local_escalation(result)


def test_missing_or_blank_reason_is_coerced_to_local_escalation():
    for arguments in (
        {"catalogue_plan_id": PLAN_ID},
        {"catalogue_plan_id": PLAN_ID, "reason": "   "},
    ):
        result = LlmProposer(
            FakeAdapter(_result("inspection.propose_plan", arguments)),
            MemoryAuditSink(),
        ).decide(_request(), _select_plan_context())
        _assert_local_escalation(result)


def test_malformed_multi_and_no_call_results_are_coerced_to_local_escalation():
    malformed_results = (
        {"status": "MALFORMED"},
        {"status": "TOOL_CALLS", "tool_calls": []},
        {
            "status": "TOOL_CALLS",
            "tool_calls": [{
                "tool_name": "inspection.propose_plan",
                "arguments": {"catalogue_plan_id": PLAN_ID, "reason": "missing envelope"},
            }],
        },
        {
            "status": "TOOL_CALLS",
            "tool_calls": [
                {"tool_name": "inspection.propose_plan", "arguments": {
                    "catalogue_plan_id": PLAN_ID, "reason": "first"
                }},
                {"tool_name": "inspection.propose_plan", "arguments": {
                    "catalogue_plan_id": PLAN_ID, "reason": "second"
                }},
            ],
        },
    )
    for adapter_result in malformed_results:
        result = LlmProposer(FakeAdapter(adapter_result), MemoryAuditSink()).decide(
            _request(), _select_plan_context()
        )
        _assert_local_escalation(result)


def test_adapter_exception_and_non_mapping_output_are_coerced_to_local_escalation():
    for adapter in (RaisingAdapter(), FakeAdapter(["not", "an", "agent_result"])):
        sink = MemoryAuditSink()
        result = LlmProposer(adapter, sink).decide(
            _request(), _select_plan_context()
        )
        _assert_local_escalation(result)
        assert len(sink.records) == 1
        attempt = sink.records[0]
        assert attempt["record"] == "proposal_attempt"
        assert attempt["advisor"] == "llm"
        assert attempt["policy_label"] == "escalate"
        assert attempt["gate_outcome"] in {"provider_error", "invalid_answer"}
        assert attempt["error_category"] == attempt["gate_outcome"]
        assert attempt["latency_ms"] >= 0
        assert "provider unavailable" not in str(attempt)


def test_forged_agent_model_result_is_revalidated_and_coerced_to_local_escalation():
    forged = AgentModelResult(
        agent_run_id=AGENT_RUN_ID,
        status="TOOL_CALLS",
        tool_calls=[AgentToolCall(
            tool_name="inspection.propose_plan",
            arguments={"catalogue_plan_id": [PLAN_ID], "reason": "not schema-valid"},
        )],
    )

    result = LlmProposer(FakeAdapter(forged), MemoryAuditSink()).decide(
        _request(), _select_plan_context()
    )

    _assert_local_escalation(result)


def test_current_plan_membership_uses_canonical_candidate_label():
    context = _select_plan_context()
    divergent = Candidate(
        "inspection.propose_plan",
        {"catalogue_plan_id": "STALE_PAYLOAD_ID"},
        PLAN_ID,
        PLAN_DESCRIPTION,
    )
    context = PolicyContext(context.state, LegalSet(SELECT_PLAN, (divergent,)))
    adapter = FakeAdapter(_result(
        "inspection.propose_plan",
        {"catalogue_plan_id": PLAN_ID, "reason": "canonical current label"},
    ))

    result = LlmProposer(adapter, MemoryAuditSink()).decide(_request(), context)

    assert result["tool_calls"][0]["tool_name"] == "inspection.propose_plan"
    assert result["tool_calls"][0]["arguments"]["catalogue_plan_id"] == PLAN_ID


def test_adapter_is_not_called_outside_select_plan():
    adapter = FakeAdapter(_result(
        "inspection.propose_plan",
        {"catalogue_plan_id": PLAN_ID, "reason": "should not be used"},
    ))
    context = PolicyContext(
        _state(),
        LegalSet(EVIDENCE_GATE, (Candidate(
            "finish",
            {"status": "NEEDS_REVIEW", "summary": "review evidence"},
            "escalate",
            "ask the operator",
        ),)),
    )

    result = LlmProposer(adapter, MemoryAuditSink()).decide(_request(), context)

    _assert_local_escalation(result)
    assert adapter.calls == []


def test_forget_forwards_when_adapter_supports_it():
    adapter = FakeAdapter(_result(
        "inspection.propose_plan",
        {"catalogue_plan_id": PLAN_ID, "reason": "best coverage"},
    ))
    proposer = LlmProposer(adapter, MemoryAuditSink())

    proposer.forget(AGENT_RUN_ID)

    assert adapter.forgotten == [AGENT_RUN_ID]


def test_forget_is_a_noop_when_adapter_has_no_forget_hook():
    class AdapterWithoutForget:
        def propose(self, request: AgentRequest, context: PolicyContext):
            return _result(
                "inspection.propose_plan",
                {"catalogue_plan_id": PLAN_ID, "reason": "best coverage"},
            )

    LlmProposer(AdapterWithoutForget(), MemoryAuditSink()).forget(AGENT_RUN_ID)


def test_forget_is_a_noop_when_adapter_forget_attribute_is_not_callable():
    class AdapterWithNonCallableForget:
        forget = "not callable"

    LlmProposer(AdapterWithNonCallableForget(), MemoryAuditSink()).forget(AGENT_RUN_ID)


def test_shared_jsonl_sink_preserves_failed_and_accepted_attempts(tmp_path):
    path = tmp_path / "audit.jsonl"

    class FailedJudge:
        def choice(self, **kwargs):
            raise RuntimeError("secret provider payload")

    JevProposer(FailedJudge(), JsonlAuditSink(path)).decide(
        _request(), _select_plan_context()
    )
    LlmProposer(FakeAdapter(_result("inspection.propose_plan", {
        "catalogue_plan_id": PLAN_ID, "reason": "current plan",
    })), JsonlAuditSink(path)).decide(_request(), _select_plan_context())
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["record"] for r in records] == [
        "proposal_attempt", "proposal_attempt", "proposal_recommendation",
    ]
    assert records[0]["raw_choice"] is None
    assert records[0]["gate_outcome"] == "provider_error"
    assert records[1]["raw_choice"] == PLAN_ID
    assert records[1]["policy_label"] == PLAN_ID
    assert records[1]["gate_outcome"] == "accepted"
    assert "secret provider payload" not in path.read_text()


def test_typed_unavailable_result_records_provider_failure_without_diagnostic():
    unavailable = AgentModelResult.from_dict({
        "contract_version": "sentinel.v0", "wire_type": "agent_result",
        "agent_run_id": AGENT_RUN_ID, "status": "UNAVAILABLE",
        "diagnostic": "private provider diagnostic",
    })
    sink = MemoryAuditSink()
    _assert_local_escalation(LlmProposer(FakeAdapter(unavailable), sink).decide(
        _request(), _select_plan_context()
    ))
    attempt, = sink.records
    assert attempt["gate_outcome"] == "provider_error"
    assert attempt["error_category"] == "provider_error"
    assert "private provider diagnostic" not in str(attempt)
