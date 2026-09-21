"""Integration evidence for architecture E's approval-gated proposer flow."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sentinel.contracts import AgentModelResult, AgentRequest
from sentinel.coordinator import AgentLoop, LlmProposer
from sentinel.coordinator.architecture import ArchitectureRouter, build_policy
from sentinel.coordinator.boundary import CoordinatorBoundary
from sentinel.coordinator.decision_state import DecisionState
from sentinel.coordinator.driver import drive_run
from sentinel.coordinator.legal_tools import SELECT_PLAN, legal_set, load_catalogue_plans
from sentinel.coordinator.policy import PolicyContext, bind_context
from sentinel.coordinator.shadow import MemoryAuditSink

from .harness import AGENT_RUN_ID, FakeWorker, envelope, open_harness
from .p3_helpers import P3Run


ROUTE_PLAN = "PLAN_ROUTE_ONLY_2_ZONE"
FULL_PLAN = "PLAN_MARKER_VERIFIED_6"
CATALOGUE = load_catalogue_plans()


@pytest.mark.parametrize("max_turns", [1, 8])
def test_real_e_loop_clears_adapter_memory_at_return_boundaries(tmp_path, max_turns):
    from sentinel.adapter.llm.config import load_config
    from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
    from sentinel.adapter.llm.parser import wire_tool_name
    from sentinel.coordinator.agent_loop import AgentLoop
    from sentinel.coordinator.boundary import CoordinatorBoundary
    from sentinel.coordinator.policy import bind_context

    env = {
        "SENTINEL_LLM_PROVIDER": "openrouter",
        "SENTINEL_LLM_MODEL": "test-model",
        "SENTINEL_LLM_TIMEOUT_MS": "1000",
        "SENTINEL_LLM_PROMPT_REVISION": "test-prompt",
        "SENTINEL_LLM_TOOL_POLICY_REVISION": "test-tools",
        "SENTINEL_LLM_API_KEY_ENV": "TEST_API_KEY",
        "TEST_API_KEY": "offline-test-only",
    }
    response = {"choices": [{"message": {"tool_calls": [{
        "type": "function", "function": {
            "name": wire_tool_name("inspection.propose_plan"),
            "arguments": json.dumps({"catalogue_plan_id": ROUTE_PLAN, "reason": "route coverage"}),
        },
    }]}}]}
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: response,
    )))
    adapter = OpenAICompatibleAgentModel(
        load_config(env), client_factory=lambda **kwargs: client, environ=env,
    )
    sink = MemoryAuditSink()
    policy = build_policy("E", advisors=[], sink=sink, proposer=LlmProposer(adapter, sink))
    run = P3Run(tmp_path, "proposer-cleanup")
    try:
        result = AgentLoop(
            CoordinatorBoundary(run.repo, run.gateway),
            bind_context(policy, run.repo, CATALOGUE),
            max_turns=max_turns, repository=run.repo, catalogue=CATALOGUE,
        ).run(run.run_id, AGENT_RUN_ID, "inspect the route")
        assert run.state().run_status == "WAITING_FOR_APPROVAL"
        assert result["error_code"] == ("MAX_TURNS" if max_turns == 1 else "WAITING_FOR_APPROVAL")
        assert adapter._pending == {}
        assert adapter._history == {}
    finally:
        policy.close()
        run.close()


class _LegalProposalAdapter:
    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        self.calls: list[tuple[AgentRequest, PolicyContext]] = []

    def propose(self, request: AgentRequest, context: PolicyContext) -> AgentModelResult:
        self.calls.append((request, context))
        return AgentModelResult.from_dict({
            "contract_version": "sentinel.v0",
            "wire_type": "agent_result",
            "agent_run_id": request.agent_run_id,
            "status": "TOOL_CALLS",
            "tool_calls": [{
                "contract_version": "sentinel.v0",
                "wire_type": "agent_tool_call",
                "tool_name": "inspection.propose_plan",
                "arguments": {
                    "catalogue_plan_id": self.plan_id,
                    "reason": "narrowest catalogue plan covering the requested route",
                },
            }],
        })


class _CountingFallback:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, request: dict, context: PolicyContext) -> dict:
        self.calls += 1
        raise AssertionError("architecture F must not handle explicit proposer escalation")


def _latest_plan_reference(repo, run_id: str) -> dict:
    row = repo.connection.execute(
        "SELECT plan_id, catalogue_id, catalogue_revision, plan_sha256, station_ids_json "
        "FROM plans WHERE run_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    return {
        "plan_id": row["plan_id"],
        "catalogue_id": row["catalogue_id"],
        "catalogue_revision": row["catalogue_revision"],
        "plan_sha256": row["plan_sha256"],
        "station_ids": json.loads(row["station_ids_json"]),
    }


def test_e_proposal_crosses_agent_loop_and_gateway_before_two_operator_approvals(tmp_path):
    repo, gateway = open_harness(tmp_path)
    run_id = "77777777-7777-4777-8777-777777777777"
    adapter = _LegalProposalAdapter(ROUTE_PLAN)
    sink = MemoryAuditSink()
    policy = build_policy(
        "E",
        advisors=[],
        sink=sink,
        proposer=LlmProposer(adapter, sink),
    )
    approval_states: list[DecisionState] = []

    def approve(state: DecisionState) -> None:
        approval_states.append(state)
        if not state.plan_approved:
            event_types = [event["event_type"] for event in repo.read_events(run_id, 0, 30)]
            assert event_types == [
                "RUN_CREATED",
                "USER_MESSAGE_RECEIVED",
                "PLAN_PROPOSED",
                "PLAN_CREATED",
                "AGENT_INTENT_PROPOSED",
            ]
            assert repo.connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE run_id = ?", (run_id,)
            ).fetchone()[0] == 0
            assert state.run_status == "WAITING_FOR_APPROVAL"
            assert state.proposed_catalogue_plan_ids == (ROUTE_PLAN,)

            reference = _latest_plan_reference(repo, run_id)
            gateway.submit(envelope(
                "OPERATOR_DECIDE",
                {"decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference}},
                sequence=10,
                run_id=run_id,
                actor_type="OPERATOR",
                expected=state.state_version,
            ))
            worker = FakeWorker(gateway)
            worker.preflight(run_id, reference["plan_id"], reference["plan_sha256"])
            worker.dispatch(
                run_id,
                reference["plan_id"],
                "LEFT_ALLEY_MIDDLE",
                repo.read_state(run_id).to_dict()["state_version"],
            )
            worker.capture_and_perceive(run_id, reference["plan_id"], reference["plan_sha256"])
            return

        assert state.task_status == "ASSIGNMENT_PROPOSED"
        gateway.submit(envelope(
            "OPERATOR_DECIDE",
            {"decision": "CONFIRM", "target": {"kind": "TASK", "task_id": state.task_id}},
            sequence=30,
            run_id=run_id,
            actor_type="OPERATOR",
            expected=state.state_version,
        ))

    try:
        result = drive_run(
            repo=repo,
            gateway=gateway,
            policy=policy,
            catalogue=CATALOGUE,
            run_id=run_id,
            agent_run_id=AGENT_RUN_ID,
            request_text="route-only survey of the alley stations",
            on_approval=approve,
            on_judgment=lambda pending: (_ for _ in ()).throw(
                AssertionError(f"architecture E unexpectedly asked for an operator choice: {pending}")
            ),
        )

        assert result["status"] == "COMPLETED"
        assert result["approvals"] == 2
        assert result["judgments"] == 0
        assert len(approval_states) == 2
        assert len(adapter.calls) == 1
        assert adapter.calls[0][1].legal.decision_point == SELECT_PLAN
        attempt, recommendation, route = sink.records[:3]
        assert attempt["record"] == "proposal_attempt"
        assert attempt["gate_outcome"] == "accepted"
        assert recommendation["record"] == "proposal_recommendation"
        assert recommendation["label"] == ROUTE_PLAN
        assert route["record"] == "architecture_route"
        assert route["route"] == "proposer"
        proposal_event = next(
            event for event in repo.read_events(run_id, 0, 30)
            if event["event_type"] == "AGENT_INTENT_PROPOSED"
        )
        assert proposal_event["payload"] == {
            "intent": "REQUEST_OPERATOR_APPROVAL",
            "catalogue_plan_id": ROUTE_PLAN,
        }
    finally:
        repo.close()


def test_real_e_loop_normalizes_llm_escalation_without_persisting_a_proposal(tmp_path):
    from sentinel.adapter.llm.config import load_config
    from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
    from sentinel.adapter.llm.parser import wire_tool_name

    repo, gateway = open_harness(tmp_path)
    run_id = "88888888-8888-4888-8888-888888888888"
    provider_summary = "provider cannot safely choose a plan"
    response = {"choices": [{"message": {"tool_calls": [{
        "type": "function", "function": {
            "name": wire_tool_name("finish"),
            "arguments": json.dumps({
                "status": "NEEDS_REVIEW", "summary": provider_summary,
            }),
        },
    }]}}]}
    env = {
        "SENTINEL_LLM_PROVIDER": "openrouter",
        "SENTINEL_LLM_MODEL": "test-model",
        "SENTINEL_LLM_TIMEOUT_MS": "1000",
        "SENTINEL_LLM_PROMPT_REVISION": "test-prompt",
        "SENTINEL_LLM_TOOL_POLICY_REVISION": "test-tools",
        "SENTINEL_LLM_API_KEY_ENV": "TEST_API_KEY",
        "TEST_API_KEY": "offline-test-only",
    }
    transport_calls: list[dict] = []

    def create(**kwargs):
        transport_calls.append(kwargs)
        return response

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    adapter = OpenAICompatibleAgentModel(
        load_config(env), client_factory=lambda **kwargs: client, environ=env,
    )
    sink = MemoryAuditSink()
    fallback = _CountingFallback()
    try:
        gateway.submit(envelope(
            "RUN_CREATE", {"request_text": "unsupported request"}, sequence=1,
            run_id=run_id, actor_type="OPERATOR",
        ))
        events_before = repo.read_events(run_id, 0, 30)
        router = ArchitectureRouter(fallback, LlmProposer(adapter, sink), sink)

        result = AgentLoop(
            CoordinatorBoundary(repo, gateway),
            bind_context(router, repo, CATALOGUE),
            repository=repo,
            catalogue=CATALOGUE,
        ).run(run_id, AGENT_RUN_ID, "unsupported request")

        assert result == {
            "status": "NEEDS_REVIEW",
            "summary": "plan proposal needs operator review",
        }
        assert len(transport_calls) == 1
        assert wire_tool_name("finish") in {
            tool["function"]["name"] for tool in transport_calls[0]["tools"]
        }
        rendered_request = json.loads(adapter.last_request["messages"][-1]["content"])
        assert rendered_request["agent_run_id"] == AGENT_RUN_ID
        assert AGENT_RUN_ID not in json.dumps(response)
        assert repo.read_events(run_id, 0, 30) == events_before
        assert repo.read_state(run_id).to_dict()["run_status"] == "RUNNING"
        for table in ("plans", "plan_approvals", "tasks"):
            count = repo.connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            assert count == 0
        attempts = [record for record in sink.records if record["record"] == "proposal_attempt"]
        assert len(attempts) == 1
        assert attempts[0]["gate_outcome"] == "accepted"
        assert attempts[0]["raw_choice"] == "escalate"
        assert attempts[0]["policy_label"] == "escalate"
        assert attempts[0]["provider_summary"] == provider_summary
        assert not [record for record in sink.records if record["record"] == "proposal_recommendation"]
        routes = [record for record in sink.records if record["record"] == "architecture_route"]
        assert len(routes) == 1
        assert routes[0]["route"] == "proposer_escalation"
        assert routes[0]["label"] == "escalate"
        assert fallback.calls == 0
    finally:
        repo.close()


def test_rejected_catalogue_plan_is_omitted_before_another_real_plan_is_confirmed(tmp_path):
    run = P3Run(tmp_path, "proposer-flow-rejection")
    try:
        run.propose_plan(ROUTE_PLAN)
        assert run.state().run_status == "WAITING_FOR_APPROVAL"

        run.decide_latest_plan("REJECT")
        state = run.state()
        legal = legal_set(state, CATALOGUE)

        assert state.run_status == "RUNNING"
        assert state.plan_id is None
        assert legal.decision_point == SELECT_PLAN
        assert legal.labels == (FULL_PLAN, "escalate")

        run.propose_plan(FULL_PLAN)
        run.decide_latest_plan("CONFIRM")

        confirmed = run.state()
        assert confirmed.plan_approved is True
        assert confirmed.proposed_catalogue_plan_ids == (ROUTE_PLAN, FULL_PLAN)
        assert len(confirmed.plan_station_ids) == 6
    finally:
        run.close()
