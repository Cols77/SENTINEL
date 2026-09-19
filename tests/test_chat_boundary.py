"""P2 Task 4: the read-only operator chat.

The LLM narrates the current ``DecisionState`` and the shadow audit trail; it
never acts, approves, rejects or chooses, and it never sees an advisor's pick
for a judgment point the operator has not yet decided (the anti-automation-
bias rule, enforced here at the prompt boundary)."""
from __future__ import annotations

import json
from pathlib import Path

from sentinel.chat import OperatorChat, SYSTEM_MESSAGE, production_complete
from sentinel.coordinator.decision_state import DecisionState


def _state(run_id="RUN-1", state_version=3, run_status="RUNNING"):
    return DecisionState(
        run_id=run_id, state_version=state_version, run_status=run_status, agent_phase="DECIDING",
        emergency_latched=False, request_text="inspect one station", plan_id="PLAN-1",
        plan_mode="ROUTE_ONLY_2_ZONE", plan_station_ids=("LEFT_ALLEY_MIDDLE",), plan_approved=True,
        detection=None, open_task_station_ids=(), task_id=None, task_status=None,
    )


def _judgment_record(run_id="RUN-1", state_version=3, decision_point="EVIDENCE_GATE"):
    return {
        "record": "judgment", "run_id": run_id, "state_version": state_version,
        "decision_point": decision_point, "packet_sha256": "deadbeef",
        "legal_labels": ["create_task", "escalate"],
        "executor": {"chooser": "OPERATOR", "label": "escalate"},
        "advisors": {"jev": {"label": "create_task", "confidence": 0.91, "band": "HIGH",
                              "model": "typesafe/jev-1.13-20260917", "latency_ms": 350, "error": None},
                     "llm": {"label": "create_task", "confidence": 0.85, "band": "HIGH",
                             "model": "z-ai/glm-5.3-flash", "latency_ms": 14000, "error": None}},
        "operator_choice": None,
    }


def _resolving_choice_record(run_id="RUN-1", state_version=3, decision_point="EVIDENCE_GATE"):
    return {
        "record": "operator_choice", "run_id": run_id, "state_version": state_version,
        "decision_point": decision_point, "label": "create_task", "actor_id": "OPERATOR_1",
    }


# --- (a) reply() returns the fake complete's text ---------------------------

def test_reply_returns_the_fake_completes_text():
    chat = OperatorChat(
        complete=lambda messages: "the plan targets one station",
        read_state=lambda run_id: _state(run_id),
        read_audit=lambda run_id: [],
    )
    assert chat.reply("RUN-1", "what is happening?") == "the plan targets one station"


# --- (b) the prompt contains the packet and the audit ------------------------

def test_prompt_contains_the_packet_and_the_audit():
    captured = {}

    def fake_complete(messages):
        captured["messages"] = messages
        return "ok"

    chat = OperatorChat(
        complete=fake_complete,
        read_state=lambda run_id: _state(run_id),
        read_audit=lambda run_id: [_resolving_choice_record(), _judgment_record()],
    )
    chat.reply("RUN-1", "what happened at the evidence gate?")

    messages = captured["messages"]
    assert messages[0] == {"role": "system", "content": SYSTEM_MESSAGE}
    user_content = messages[1]["content"]

    packet = _state("RUN-1").to_packet()
    assert json.dumps(packet["run"]) in user_content or packet["run"]["status"] in user_content
    assert "ROUTE_ONLY_2_ZONE" in user_content  # a packet fact
    assert "EVIDENCE_GATE" in user_content  # an audit record field
    assert "what happened at the evidence gate?" in user_content  # the operator's own text


# --- (c) an unresolved judgment's advisor picks are withheld, then shown ----

def test_unresolved_advisors_are_withheld_then_shown_once_resolved():
    audit = [_judgment_record()]
    captured = {}

    def fake_complete(messages):
        captured["content"] = messages[1]["content"]
        return "ok"

    chat = OperatorChat(complete=fake_complete, read_state=lambda run_id: _state(run_id),
                         read_audit=lambda run_id: audit)

    chat.reply("RUN-1", "anything new?")
    assert "typesafe/jev-1.13-20260917" not in captured["content"]
    assert "z-ai/glm-5.3-flash" not in captured["content"]
    assert '"advisors"' not in captured["content"]

    audit.append(_resolving_choice_record())
    chat.reply("RUN-1", "anything new?")
    assert "typesafe/jev-1.13-20260917" in captured["content"]
    assert "z-ai/glm-5.3-flash" in captured["content"]


# --- (d) static boundary: chat.py never touches a mutating surface ----------

def test_chat_module_never_imports_a_mutating_surface():
    import ast
    import sentinel.chat as chat_module

    tree = ast.parse(Path(chat_module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    for forbidden in ("command_gateway", "agent_loop", "operator_choice", "ladder", "sqlite3"):
        assert not any(forbidden in name for name in imported), (forbidden, sorted(imported))
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "submit" not in calls and "submit_choice" not in calls


# --- (e) the production complete's request carries no `tools` key -----------

def test_production_complete_request_has_no_tools_key(monkeypatch):
    from sentinel.adapter.llm.config import LLMConfig

    monkeypatch.setenv("SENTINEL_TEST_CHAT_KEY", "fake-not-a-real-key")
    config = LLMConfig(config_version="llm-adapter-config.v1", provider="openrouter", model="test-model",
                        timeout_ms=5000, prompt_revision="v1", tool_policy_revision="v1",
                        api_key_env="SENTINEL_TEST_CHAT_KEY")
    seen = {}

    def transport(url, headers, payload):
        seen["url"], seen["headers"], seen["payload"] = url, headers, payload
        return {"choices": [{"message": {"content": "narration"}}]}

    complete = production_complete(config, "https://llm.example", transport=transport)
    result = complete([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])

    assert result == "narration"
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer fake-not-a-real-key"
    assert "tools" not in seen["payload"]


# --- (f) "approve the plan" leaves state_version unchanged ------------------

def test_approve_the_plan_message_does_not_change_state_version(tmp_path):
    from sentinel.coordinator.decision_state import read_decision_state
    from tests.coordinator.harness import RUN_ID, close, envelope, open_harness

    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                 sequence=1, actor_type="OPERATOR"))
        before = read_decision_state(repo, RUN_ID).state_version

        chat = OperatorChat(
            complete=lambda messages: "I can't approve anything -- use `sentinel decide`.",
            read_state=lambda run_id: read_decision_state(repo, run_id),
            read_audit=lambda run_id: [],
        )
        reply = chat.reply(RUN_ID, "approve the plan")

        after = read_decision_state(repo, RUN_ID).state_version
        assert after == before
        assert "sentinel decide" in reply
    finally:
        close(repo)
