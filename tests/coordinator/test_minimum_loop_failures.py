"""A16 fail-closed minimum-loop assertions."""
from __future__ import annotations

import pytest

from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.errors import CoordinatorError
from sentinel.coordinator.evidence import EvidencePolicy
from sentinel.coordinator.repository import CoordinatorRepository

from .harness import AGENT_RUN_ID, RUN_ID, close, envelope, open_harness, perception_result


def test_approval_stop_prevents_model_call(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        repo.create_run(RUN_ID, status="WAITING_FOR_APPROVAL")
        calls = []
        result = AgentLoop(type("Port", (), {
            "read_state": lambda _, run_id: repo.read_state(run_id).to_dict(),
            "read_events": lambda _, run_id, after, limit: repo.read_events(run_id, after, limit),
        })(), lambda request: calls.append(request)).run(RUN_ID, AGENT_RUN_ID, "inspect")
        assert result["error_code"] == "WAITING_FOR_APPROVAL"
        assert calls == []
    finally:
        close(repo)


def test_malformed_model_tool_is_rejected_without_mutation(tmp_path):
    repo, _gateway = open_harness(tmp_path)
    try:
        repo.create_run(RUN_ID, status="RUNNING")
        class Port:
            def read_state(self, run_id): return repo.read_state(run_id).to_dict()
            def read_events(self, *args): return []
            def submit(self, command): raise AssertionError("invalid tool must not cross boundary")
        result = AgentLoop(Port(), lambda request: {
            "contract_version": "sentinel.v0", "wire_type": "agent_result",
            "agent_run_id": AGENT_RUN_ID, "status": "TOOL_CALLS", "tool_calls": [{
                "contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                "tool_name": "not-a-tool", "arguments": {},
            }],
        }).run(RUN_ID, AGENT_RUN_ID, "inspect")
        assert result["error_code"] == "INVALID_AGENT_RESULT"
    finally:
        close(repo)


def test_dispatch_before_approval_is_rejected(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        repo.create_run(RUN_ID, status="RUNNING")
        with pytest.raises(CoordinatorError, match="approved immutable plan"):
            gateway.submit(envelope("INSPECTION_DISPATCH", {
                "section_id": "LEFT_ALLEY_MIDDLE", "mission_id": "55555555-5555-4555-8555-555555555555",
                "worker_id": "SIMULATED_WORKER",
            }, sequence=2, actor_type="WORKER", expected=0))
        assert repo.read_events(RUN_ID, 0, 10) == []
    finally:
        close(repo)


def test_stale_operator_action_is_rejected(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect"}, sequence=1, actor_type="OPERATOR"))
        with pytest.raises(CoordinatorError) as caught:
            gateway.submit(envelope("OPERATOR_DECIDE", {
                "decision": "CONFIRM", "target": {"kind": "TASK", "task_id": "55555555-5555-4555-8555-555555555555"},
            }, sequence=2, actor_type="OPERATOR", expected=0))
        assert caught.value.code == "STALE_STATE_VERSION"
    finally:
        close(repo)


def test_provider_error_and_low_confidence_never_create_actionable_evidence():
    policy = EvidencePolicy()
    provider_error = perception_result(RUN_ID, "a" * 64)
    provider_error["status"] = "PROVIDER_ERROR"
    provider_error["source_class"] = "unknown"
    provider_error["raw_score"] = None
    assert policy.evaluate(provider_error, artifact_sha256="a" * 64, threshold=0.5, judged=False).status == "FAILED"
    low = perception_result(RUN_ID, "a" * 64)
    low["status"] = "UNSCORED"
    low["source_class"] = "unknown"
    low["raw_score"] = None
    assert policy.evaluate(low, artifact_sha256="a" * 64, threshold=0.5, judged=False).status == "NEEDS_REVIEW"


def test_low_confidence_detected_evidence_creates_no_task_via_gateway(tmp_path):
    """The threshold guardrail must hold at the real gateway boundary, not only in EvidencePolicy."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect"}, sequence=1, actor_type="OPERATOR"))
        low = perception_result("77777777-7777-4777-8777-777777777778", "a" * 64)
        low["raw_score"] = 0.1  # below any sensible threshold; schema-legal DETECTED
        gateway.submit(envelope("PERCEPTION_INGEST", {"perception_result": low}, sequence=2, actor_type="WORKER"))
        with pytest.raises(CoordinatorError) as caught:
            gateway.submit(envelope("TASK_CREATE", {
                "perception_result_id": low["result_id"],
                "section_id": "LEFT_ALLEY_MIDDLE",
                "evidence_artifact_id": low["artifact_id"],
            }, sequence=3))
        assert caught.value.code == "INVALID_STATE_TRANSITION"
        assert repo.connection.execute("SELECT COUNT(*) FROM tasks WHERE run_id = ?", (RUN_ID,)).fetchone()[0] == 0
    finally:
        close(repo)


def test_emergency_latch_blocks_ordinary_commands(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        repo.create_run(RUN_ID, status="RUNNING")
        gateway.submit(envelope("EMERGENCY_SIGNAL", {
            "signal_id": "77777777-7777-4777-8777-777777777777",
        }, sequence=1, actor_type="WORKER"))
        with pytest.raises(CoordinatorError) as caught:
            gateway.submit(envelope("TASK_RANK", {"task_id": "55555555-5555-4555-8555-555555555555"}, sequence=2))
        assert caught.value.code == "EMERGENCY_LATCHED"
    finally:
        close(repo)
