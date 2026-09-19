"""B10 live LLM acceptance gate (opt-in only).

Runs against the configured real OpenAI-compatible provider. Skipped unless
SENTINEL_RUN_LIVE_LLM_ACCEPTANCE=1. Requires real provider configuration via
sentinel.adapter.llm.config.load_config (which auto-loads the nearest .env).
The official openai client is used directly - no fakes, no recorded
responses. Nothing here can move hardware or mutate production data: the
coordinator side is a bounded in-memory fake.

Run:
    SENTINEL_RUN_LIVE_LLM_ACCEPTANCE=1 uv run python -m pytest \
        tests/agent/test_live_openai_compatible.py -q -m live_llm
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from sentinel.adapter.llm.config import AdapterConfigError, load_config
from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
from sentinel.adapter.llm.parser import APPROVED_TOOL_NAMES
from sentinel.contracts import AgentModelResult, AgentRequest, AgentToolCall, validate_document
from sentinel.contracts.schemas import ContractValidationError

GATE_FLAG = "SENTINEL_RUN_LIVE_LLM_ACCEPTANCE"
SCENARIOS_PATH = Path(__file__).resolve().parent / "live_scenarios.json"

STATE_INSPECTION_TOOLS = ("state.get_summary", "state.get_recent_events")
DEFAULT_MAX_TURNS = 4
FINISH_TOOL = "finish"


def _gate_turned_on() -> bool:
    import os

    return os.environ.get(GATE_FLAG) == "1"


def _load_scenarios() -> list[dict[str, Any]]:
    document = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenarios = document["scenarios"]
    assert isinstance(scenarios, list) and scenarios, "live_scenarios.json must define scenarios"
    return scenarios


def _catalogue_events(scenario: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Encode a scenario's catalogue block as schema-legal event_context items.

    The AgentRequest wire schema has no catalogue field, so the catalogue plan
    list rides in event_context summaries (event_type <= 64 chars, summary
    <= 512 chars, additionalProperties false). The model cannot reliably name
    a catalogue_plan_id it has never seen, so every plan - including the
    excluded decoy with its exclusion reason - is embedded here.
    """
    catalogue = scenario.get("catalogue")
    if not isinstance(catalogue, Mapping):
        return []
    start = max(
        (int(event.get("sequence_no", 0)) for event in scenario.get("event_context", [])),
        default=0,
    )
    events: list[dict[str, Any]] = []
    for offset, plan in enumerate(catalogue.get("plans", []), start=1):
        reason = ""
        if plan.get("excluded"):
            reason = (
                " requires marker verification which is unavailable for this run"
                if plan.get("requires_marker_verification")
                else " excluded by catalogue"
            )
        summary = (
            f"Catalogue {catalogue.get('catalogue_id', '')} plan {plan.get('id', '')} "
            f"mode {plan.get('mode', '')} stations {plan.get('station_count', 0)} "
            f"requires_marker_verification {str(bool(plan.get('requires_marker_verification'))).lower()}"
            f"{reason}"
        )
        events.append(
            {
                "sequence_no": start + offset,
                "event_type": "RUN_CATALOGUE",
                "summary": summary[:512],
            }
        )
    return events


def _build_request(scenario: Mapping[str, Any], agent_run_id: str) -> AgentRequest:
    previous = scenario.get("previous_result")
    return AgentRequest.from_dict(
        {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_request",
            "agent_run_id": agent_run_id,
            "run_id": scenario.get("run_id", "11111111-1111-4111-8111-111111111111"),
            "state_version": int(scenario["state_version"]),
            "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
            "deadline": "2099-09-17T00:00:00Z",
            "phase": scenario["phase"],
            "event_context": list(scenario.get("event_context", [])) + _catalogue_events(scenario),
            "evidence_context": list(scenario.get("evidence_context", [])),
            "legal_tools": list(scenario["legal_tools"]),
            "previous_result": previous,
            "user_request": scenario["user_request"],
        }
    )


def _previous_result_for_next_turn(
    scenario: Mapping[str, Any], call: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Build the sanitized coordinator feedback for the next reasoning turn."""
    feedback = scenario.get("coordinator_feedback")
    if not isinstance(feedback, Mapping):
        return None
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_tool_result",
        "tool_name": call["tool_name"],
        "status": "ACCEPTED",
        "error_code": None,
        "state_version": int(scenario["state_version"]) + 1,
        "event_cursor": 1,
        "legal_tools": list(scenario["legal_tools"]),
        "safe_summary": str(feedback["safe_summary"])[:1024],
        "result": {"kind": feedback.get("kind", "empty")},
    }


def _expectation_matches(expectations: Mapping[str, Any], call: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    arguments = call.get("arguments", {})
    for dotted, expected in expectations.items():
        section, _, key = dotted.partition(".")
        actual = arguments if section == "arguments" else call
        actual = actual.get(key) if key else actual
        allowed = expected if isinstance(expected, list) else [expected]
        if actual not in allowed:
            failures.append(f"{dotted}: expected one of {allowed}, got {actual!r}")
    return failures


def _describe_call(call: Mapping[str, Any]) -> dict[str, Any]:
    return {"tool_name": call.get("tool_name"), "arguments": dict(call.get("arguments", {}))}


# ---------------------------------------------------------------------------
# Hermetic tests for the bounded-horizon gate evaluator (no provider needed).
# ---------------------------------------------------------------------------


class _ScriptedAgentModel:
    """Minimal adapter stub: returns one canned AgentModelResult per turn."""

    def __init__(self, tool_calls: list[tuple[str, dict[str, Any]]]):
        self._tool_calls = list(tool_calls)
        self.last_request: dict[str, Any] | None = None

    def propose(self, request: AgentRequest) -> Any:
        self.last_request = {"messages": [{"role": "user", "content": json.dumps(request.to_dict(), sort_keys=True)}]}
        if not self._tool_calls:
            raise AssertionError("script exhausted")
        tool_name, arguments = self._tool_calls.pop(0)
        # Built directly (not from_dict): scripted bad-argument cases are
        # deliberately schema-invalid and must reach the evaluator.
        return AgentModelResult(
            agent_run_id=request.agent_run_id,
            status="TOOL_CALLS",
            tool_calls=[AgentToolCall(tool_name=tool_name, arguments=arguments)],
        )


def _scenario_by_name(name: str) -> dict[str, Any]:
    return next(item for item in _load_scenarios() if item["name"] == name)


def test_bounded_horizon_passes_when_deliverable_fulfilled_late():
    """Deliverable on turn 3 of a 4-turn horizon passes; inspection turns are free."""
    scenario = _scenario_by_name("initial_plan_one_station")
    model = _ScriptedAgentModel(
        [
            ("state.get_summary", {}),
            ("state.get_recent_events", {"after_sequence": 0, "limit": 8}),
            ("inspection.propose_plan", {"catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"}),
            ("wait_for_event", {"event_types": ["PLAN_APPROVED"], "reason": "awaiting approval"}),
        ]
    )
    outcome = run_bounded_horizon_scenario(model.propose, scenario, AGENT_RUN_ID)
    assert outcome["failures"] == []
    assert [call["tool_name"] for call in outcome["calls"]][:3] == [
        "state.get_summary",
        "state.get_recent_events",
        "inspection.propose_plan",
    ]


def test_bounded_horizon_fails_with_horizon_exhausted():
    """A deliverable never fulfilled within the horizon is HORIZON_EXHAUSTED."""
    scenario = _scenario_by_name("initial_plan_one_station")
    model = _ScriptedAgentModel(
        [("state.get_summary", {})] * DEFAULT_MAX_TURNS  # inspection forever
    )
    outcome = run_bounded_horizon_scenario(model.propose, scenario, AGENT_RUN_ID)
    assert any(f.startswith("HORIZON_EXHAUSTED") for f in outcome["failures"]), outcome["failures"]


def test_bounded_horizon_classifies_premature_finish():
    scenario = _scenario_by_name("initial_plan_one_station")
    model = _ScriptedAgentModel([("finish", {"status": "DONE", "summary": "done"})])
    outcome = run_bounded_horizon_scenario(model.propose, scenario, AGENT_RUN_ID)
    assert any("PREMATURE_FINISH" in f for f in outcome["failures"]), outcome["failures"]


def test_bounded_horizon_classifies_wrong_tool_and_missing_arguments():
    scenario = _scenario_by_name("qualifying_evidence_to_task")
    wrong = _ScriptedAgentModel(
        [("patroller.rank_for_task", {"task_id": TASK_ID})]
    )
    outcome = run_bounded_horizon_scenario(wrong.propose, scenario, AGENT_RUN_ID)
    assert any("WRONG_TOOL" in f for f in outcome["failures"]), outcome["failures"]

    bad_args = _ScriptedAgentModel(
        [("task.create", {"perception_result_id": "55555555-5555-4555-8555-555555555555"})]
    )
    outcome = run_bounded_horizon_scenario(bad_args.propose, scenario, AGENT_RUN_ID)
    assert any("MISSING_ARGUMENTS" in f for f in outcome["failures"]), outcome["failures"]
    assert any(f.startswith("HORIZON_EXHAUSTED") for f in outcome["failures"]), outcome["failures"]


def test_bounded_horizon_state_inspection_never_fails_scenario():
    """Inspection calls inside the horizon are legal and consume no deliverable."""
    scenario = _scenario_by_name("qualifying_evidence_to_task")
    model = _ScriptedAgentModel(
        [
            ("state.get_summary", {}),
            ("task.create", {
                "perception_result_id": "55555555-5555-4555-8555-555555555555",
                "section_id": "LEFT_ALLEY_MIDDLE",
                "evidence_artifact_id": "33333333-3333-4333-8333-333333333333",
            }),
            ("patroller.rank_for_task", {"task_id": TASK_ID}),
        ]
    )
    outcome = run_bounded_horizon_scenario(model.propose, scenario, AGENT_RUN_ID)
    assert outcome["failures"] == []


def test_bounded_horizon_respects_scenario_max_turns_override():
    scenario = dict(_scenario_by_name("qualifying_evidence_to_task"))
    scenario["max_turns"] = 3
    model = _ScriptedAgentModel(
        [
            ("state.get_summary", {}),
            ("task.create", {
                "perception_result_id": "55555555-5555-4555-8555-555555555555",
                "section_id": "LEFT_ALLEY_MIDDLE",
                "evidence_artifact_id": "33333333-3333-4333-8333-333333333333",
            }),
            ("patroller.rank_for_task", {"task_id": TASK_ID}),
        ]
    )
    outcome = run_bounded_horizon_scenario(model.propose, scenario, AGENT_RUN_ID)
    assert outcome["failures"] == []


def _skip_message() -> str:
    return (
        "live LLM acceptance gate is opt-in; set "
        f"{GATE_FLAG}=1 and provide provider configuration (.env or environment)"
    )


@pytest.fixture(scope="module")
def scenarios() -> list[dict[str, Any]]:
    if not _gate_turned_on():
        pytest.skip(_skip_message())
    return _load_scenarios()


@pytest.fixture(scope="module")
def adapter() -> OpenAICompatibleAgentModel:
    """Real provider configuration and the official openai client."""
    if not _gate_turned_on():  # pragma: no cover - defensive
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


def run_bounded_horizon_scenario(
    propose: Any,
    scenario: Mapping[str, Any],
    agent_run_id: str,
    allowed_tools: tuple[str, ...] = APPROVED_TOOL_NAMES,
) -> dict[str, Any]:
    """Drive one scenario as a bounded horizon of at most N reasoning turns.

    Instead of asserting the right tool at exact turn indexes, the loop runs up
    to ``max_turns`` (per-scenario, default 4) turns, feeding the sanitized
    coordinator feedback as previous_result after each turn. The scenario PASSES
    when its full ordered deliverable sequence (expected_turns: right tool +
    exact required arguments) is fulfilled within the horizon, in order.
    State-inspection calls (state.get_summary, state.get_recent_events) are
    legal anywhere inside the horizon and never count as deliverables.

    Failures are classified: HORIZON_EXHAUSTED (a deliverable was never
    fulfilled within N turns), PREMATURE_FINISH (finish called while
    deliverables remained unfulfilled), WRONG_TOOL (a non-inspection,
    non-finish tool was recorded against a pending deliverable without
    matching it), MISSING_ARGUMENTS (right tool but argument mismatch).
    """
    request = _build_request(scenario, agent_run_id)
    expected_turns = list(scenario["expected_turns"])
    max_turns = int(scenario.get("max_turns", DEFAULT_MAX_TURNS))
    pending = 0  # index of the next unfulfilled deliverable
    calls: list[dict[str, Any]] = []
    failures: list[str] = []
    previous_result: dict[str, Any] | None = None

    for turn_index in range(max_turns):
        if pending >= len(expected_turns):
            break
        if previous_result is not None:
            request = AgentRequest.from_dict(
                {**request.to_dict(), "previous_result": previous_result}
            )
        started = time.monotonic()
        result = propose(request)
        latency_ms = int((time.monotonic() - started) * 1000)

        # The model must embed the Sentinel context supplied by the request.
        # The caller reads adapter.last_request (set during propose) via
        # _assert_context_embedded before this check sees the payload.
        _assert_context_embedded(propose, request)

        if result.status != "TOOL_CALLS" or len(result.tool_calls) != 1:
            failures.append(
                f"turn {turn_index}: expected exactly one TOOL_CALLS entry, got status={result.status}"
            )
            break
        call = result.tool_calls[0].to_dict()
        try:
            validate_document(result.to_dict())
        except ContractValidationError as exc:
            failures.append(
                f"turn {turn_index}: MISSING_ARGUMENTS - model call is not schema-valid: "
                + str(exc).split(": ", 1)[-1][:256]
            )
            break
        tool = call["tool_name"]

        if tool not in allowed_tools:
            failures.append(f"turn {turn_index}: tool {tool!r} is outside the V0 set")
        if tool not in request.legal_tools:
            failures.append(
                f"turn {turn_index}: tool {tool!r} is not legal for the supplied state"
            )

        expected = expected_turns[pending]
        expected_tool = expected["expected_tool"]
        allowed_expected = expected_tool if isinstance(expected_tool, list) else [expected_tool]
        if tool == FINISH_TOOL and pending < len(expected_turns):
            failures.append(
                f"turn {turn_index}: PREMATURE_FINISH - finish called while deliverable "
                f"{pending} ({allowed_expected}) is unfulfilled"
            )
            break  # finish is terminal; nothing further can fulfill the horizon
        elif tool in STATE_INSPECTION_TOOLS:
            pass  # legal verification inside the horizon; never a deliverable
        elif tool in allowed_expected:
            argument_failures = _expectation_matches(expected["expectations"], call)
            if argument_failures:
                failures.append(
                    f"turn {turn_index}: MISSING_ARGUMENTS - " + "; ".join(argument_failures)
                )
                break
            else:
                pending += 1  # deliverable fulfilled
        else:
            failures.append(
                f"turn {turn_index}: WRONG_TOOL - deliverable {pending} expects "
                f"{allowed_expected}, model selected {tool!r}"
            )
            break

        calls.append(
            {
                "scenario": scenario["name"],
                "turn": turn_index,
                "tool_name": tool,
                "latency_ms": latency_ms,
                "schema_valid": True,
                "coordinator_status": "ACCEPTED",
            }
        )
        previous_result = _previous_result_for_next_turn(scenario, call)

    if pending < len(expected_turns):
        failures.append(
            f"HORIZON_EXHAUSTED - only {pending}/{len(expected_turns)} deliverables "
            f"fulfilled within {max_turns} turns"
        )
    return {"calls": calls, "failures": failures}


def _assert_context_embedded(propose: Any, request: AgentRequest) -> None:
    """The adapter must have sent the Sentinel state context just dispatched."""
    payload_context = getattr(propose, "__self__", propose).last_request  # bound-method aware
    if payload_context is None:
        return  # stubs without last_request: context embedding is not testable here
    context_blob = json.dumps(payload_context["messages"], sort_keys=True)
    for required in (
        request.agent_run_id,
        str(request.state_version),
        request.phase,
    ):
        assert required in context_blob, "adapter must send Sentinel state context"


class RecordingFakeAgentLoop:
    """Bounded fake coordinator: records sanitized results, no side effects."""

    def __init__(self, adapter: OpenAICompatibleAgentModel, allowed_tools: tuple[str, ...] = APPROVED_TOOL_NAMES):
        self.adapter = adapter
        self.allowed_tools = tuple(allowed_tools)
        self.turns: list[dict[str, Any]] = []

    def run_scenario(self, scenario: Mapping[str, Any], agent_run_id: str) -> dict[str, Any]:
        outcome = run_bounded_horizon_scenario(
            self.adapter.propose,
            scenario,
            agent_run_id,
            allowed_tools=self.allowed_tools,
        )
        self.turns.extend(outcome["calls"])
        assert not outcome["failures"], "live reasoning gate failures: " + "; ".join(
            outcome["failures"]
        )
        return {
            "scenario": scenario["name"],
            "turns": len(outcome["calls"]),
            "tool_calls": outcome["calls"],
        }


AGENT_RUN_ID = "22222222-2222-4222-8222-222222222222"


@pytest.mark.live_llm
def test_initial_plan_decision(scenarios, adapter):
    """Turn 1 proposes the excluded-constraint-satisfying plan; turn 2 waits for approval."""
    scenario = next(item for item in scenarios if item["name"] == "initial_plan_one_station")
    RecordingFakeAgentLoop(adapter).run_scenario(scenario, AGENT_RUN_ID)


@pytest.mark.live_llm
def test_qualifying_evidence_to_task(scenarios, adapter):
    """Turn 1 creates the task from qualifying evidence; turn 2 ranks for the task."""
    scenario = next(item for item in scenarios if item["name"] == "qualifying_evidence_to_task")
    RecordingFakeAgentLoop(adapter).run_scenario(scenario, AGENT_RUN_ID)


@pytest.mark.live_llm
def test_approval_wait(scenarios, adapter):
    """Both turns must wait (or finish) while operator approval is pending."""
    scenario = next(item for item in scenarios if item["name"] == "approval_wait")
    RecordingFakeAgentLoop(adapter).run_scenario(scenario, AGENT_RUN_ID)


def test_coordinator_feedback_is_schema_valid_for_all_scenarios():
    """Hermetic two-turn regression: the sanitized coordinator feedback for
    every scenario must validate as an AgentToolResult document and round-trip
    through AgentRequest.from_dict as the next turn's previous_result."""
    from sentinel.contracts import AgentRequest as _AR  # local import keeps module import cheap

    for scenario in _load_scenarios():
        feedback_spec = scenario.get("coordinator_feedback")
        if not isinstance(feedback_spec, Mapping):
            continue
        expected_tool = scenario["expected_turns"][0]["expected_tool"]
        tool_name = expected_tool[0] if isinstance(expected_tool, list) else expected_tool
        request = _build_request(scenario, AGENT_RUN_ID)
        feedback = _previous_result_for_next_turn(
            scenario, {"tool_name": tool_name, "arguments": {}}
        )
        assert feedback is not None
        # The result payload must carry exactly the feedback kind - no extra keys.
        assert feedback["result"] == {"kind": feedback_spec["kind"]}
        # The whole document must validate against the WP0 wire schema.
        validate_document(feedback)
        # And it must round-trip as the next turn's previous_result.
        _AR.from_dict({**request.to_dict(), "previous_result": feedback})


def test_catalogue_plan_ids_reach_request_event_context():
    """Every scenario defining a catalogue must expose each plan id - including
    the excluded decoy with its exclusion reason - via schema-legal
    event_context summaries, and the whole request must validate."""
    from sentinel.contracts import validate_document as _vd

    for scenario in _load_scenarios():
        catalogue = scenario.get("catalogue")
        if not isinstance(catalogue, Mapping):
            continue
        request = _build_request(scenario, AGENT_RUN_ID)
        summaries = [event["summary"] for event in request.event_context]
        for plan in catalogue["plans"]:
            plan_id = plan["id"]
            matching = [s for s in summaries if plan_id in s]
            assert matching, f"{scenario['name']}: plan {plan_id} absent from event_context"
            if plan.get("excluded"):
                assert any("requires marker verification" in s for s in matching), (
                    f"{scenario['name']}: excluded decoy {plan_id} lacks exclusion reason"
                )
        for event in request.event_context:
            assert len(event["event_type"]) <= 64
            assert len(event["summary"]) <= 512
        _vd(request.to_dict())


def test_catalogue_plan_ids_reach_adapter_payload():
    """build_chat_request output (what the model actually sees) must contain
    every catalogue plan id in the serialized context messages."""
    from sentinel.adapter.llm.prompt import build_chat_request as _bcr

    for scenario in _load_scenarios():
        catalogue = scenario.get("catalogue")
        if not isinstance(catalogue, Mapping):
            continue
        request = _build_request(scenario, AGENT_RUN_ID)
        payload = _bcr(
            request,
            prompt_revision="test-prompt-rev",
            tool_policy_revision="test-tool-policy-rev",
        )
        blob = json.dumps(payload["messages"], sort_keys=True)
        for plan in catalogue["plans"]:
            assert plan["id"] in blob, (
                f"{scenario['name']}: plan {plan['id']} not visible in adapter messages"
            )


TASK_ID = "22222222-2222-4222-8222-222222222222"


def test_created_task_id_reaches_request_event_context():
    """The scenario whose turn 2 expects patroller.rank_for_task must expose
    the created task id via schema-legal event_context summaries, and the
    whole request must validate."""
    from sentinel.contracts import validate_document as _vd

    scenario = next(
        item
        for item in _load_scenarios()
        if item["name"] == "qualifying_evidence_to_task"
    )
    request = _build_request(scenario, AGENT_RUN_ID)
    summaries = [event["summary"] for event in request.event_context]
    assert any(TASK_ID in s for s in summaries), (
        f"{scenario['name']}: created task id {TASK_ID} absent from event_context"
    )
    for event in request.event_context:
        assert len(event["event_type"]) <= 64
        assert len(event["summary"]) <= 512
    _vd(request.to_dict())


def test_created_task_id_reaches_adapter_payload():
    """build_chat_request output (what the model actually sees) must contain
    the created task id in the serialized context messages."""
    from sentinel.adapter.llm.prompt import build_chat_request as _bcr

    scenario = next(
        item
        for item in _load_scenarios()
        if item["name"] == "qualifying_evidence_to_task"
    )
    request = _build_request(scenario, AGENT_RUN_ID)
    payload = _bcr(
        request,
        prompt_revision="test-prompt-rev",
        tool_policy_revision="test-tool-policy-rev",
    )
    blob = json.dumps(payload["messages"], sort_keys=True)
    assert TASK_ID in blob, (
        f"{scenario['name']}: created task id {TASK_ID} not visible in adapter messages"
    )


def test_gate_flag_missing_skips_explicitly():
    """Without the flag the gate never runs and says so explicitly."""
    import os

    if _gate_turned_on():
        pytest.skip("gate is enabled in this session; skip-check is not applicable")
    assert os.environ.get(GATE_FLAG) != "1"
    assert not _gate_turned_on()
