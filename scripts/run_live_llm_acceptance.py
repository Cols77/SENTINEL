"""B10 live LLM acceptance runner (opt-in, Colin-executed).

Loads real provider configuration via sentinel.adapter.llm.config.load_config
(auto-loads the nearest .env), drives OpenAICompatibleAgentModel against the
configured OpenAI-compatible endpoint, feeds each result into a bounded fake
coordinator loop, and writes a REDACTED acceptance report.

The report contains ONLY: provider, model, prompt/tool-policy revisions, tool
names, schema-validity results, coordinator result codes, turn count, and
latency. It NEVER contains API keys, authorization headers, raw prompts, raw
responses, or model chain-of-thought.

Usage:
    SENTINEL_RUN_LIVE_LLM_ACCEPTANCE=1 uv run python scripts/run_live_llm_acceptance.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from sentinel.adapter.llm.config import AdapterConfigError, load_config
from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
from sentinel.adapter.llm.parser import APPROVED_TOOL_NAMES
from sentinel.contracts import AgentRequest, validate_document

GATE_FLAG = "SENTINEL_RUN_LIVE_LLM_ACCEPTANCE"
SCENARIOS_PATH = REPO_ROOT / "tests" / "agent" / "live_scenarios.json"
AGENT_RUN_ID = "22222222-2222-4222-8222-222222222222"


def _build_request(scenario: dict[str, Any]) -> AgentRequest:
    return AgentRequest.from_dict(
        {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_request",
            "agent_run_id": AGENT_RUN_ID,
            "run_id": scenario.get("run_id", "11111111-1111-4111-8111-111111111111"),
            "state_version": int(scenario["state_version"]),
            "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
            "deadline": "2099-09-17T00:00:00Z",
            "phase": scenario["phase"],
            "event_context": list(scenario.get("event_context", [])),
            "evidence_context": list(scenario.get("evidence_context", [])),
            "legal_tools": list(scenario["legal_tools"]),
            "previous_result": None,
            "user_request": scenario["user_request"],
        }
    )


def _expected_tool_names(expected: Any) -> list[str]:
    return list(expected) if isinstance(expected, list) else [expected]


def _argument_expectations(expected_turn: dict[str, Any]) -> dict[str, Any]:
    return dict(expected_turn.get("expectations", {}))


def _feedback_for_next_turn(scenario: dict[str, Any], tool_name: str) -> dict[str, Any]:
    feedback = scenario.get("coordinator_feedback", {})
    result: dict[str, Any] = {"kind": feedback.get("kind", "empty")}
    if isinstance(feedback.get("task_id"), str):
        result["task_id"] = feedback["task_id"]
    result["run_status"] = feedback.get("run_status", "UNKNOWN")
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_tool_result",
        "tool_name": tool_name,
        "status": "ACCEPTED",
        "error_code": None,
        "state_version": int(scenario["state_version"]) + 1,
        "event_cursor": 1,
        "legal_tools": list(scenario["legal_tools"]),
        "safe_summary": str(feedback.get("safe_summary", "bounded coordinator result"))[:1024],
        "result": result,
    }


def run_scenario(
    adapter: OpenAICompatibleAgentModel, scenario: dict[str, Any]
) -> dict[str, Any]:
    request = _build_request(scenario)
    turn_records: list[dict[str, Any]] = []
    failures: list[str] = []

    for turn_index, expected_turn in enumerate(scenario["expected_turns"]):
        started = time.monotonic()
        result = adapter.propose(request)
        latency_ms = int((time.monotonic() - started) * 1000)

        turn: dict[str, Any] = {
            "turn": turn_index,
            "latency_ms": latency_ms,
            "status": result.status,
            "schema_valid": False,
            "coordinator_result_code": "NOT_EXECUTED",
        }

        if result.status == "TOOL_CALLS":
            try:
                validate_document(result.to_dict())
                turn["schema_valid"] = True
            except Exception as exc:  # noqa: BLE001 - report bounded failure only
                turn["coordinator_result_code"] = "INVALID_CONTRACT"
                failures.append(f"turn {turn_index}: result failed contract validation ({type(exc).__name__})")

        if result.status != "TOOL_CALLS" or len(result.tool_calls) != 1:
            failures.append(
                f"turn {turn_index}: expected exactly one tool call, got status={result.status}"
                f" diagnostic={result.diagnostic!r}"
            )
            turn_records.append(turn)
            break

        call = result.tool_calls[0]
        turn["tool_name"] = call.tool_name
        if call.tool_name not in APPROVED_TOOL_NAMES:
            failures.append(f"turn {turn_index}: {call.tool_name!r} is outside the V0 tool set")
        if call.tool_name not in request.legal_tools:
            failures.append(f"turn {turn_index}: {call.tool_name!r} is not legal for this state")

        for dotted, expected in _argument_expectations(expected_turn).items():
            _, _, key = dotted.partition(".")
            actual = call.arguments.get(key)
            allowed = expected if isinstance(expected, list) else [expected]
            if actual not in allowed:
                failures.append(f"turn {turn_index}: {dotted} expected {allowed}, got {actual!r}")
        if call.tool_name not in _expected_tool_names(expected_turn["expected_tool"]):
            failures.append(
                f"turn {turn_index}: expected {expected_turn['expected_tool']},"
                f" model selected {call.tool_name!r}"
            )

        turn["coordinator_result_code"] = "ACCEPTED"
        turn_records.append(turn)

        feedback = _feedback_for_next_turn(scenario, call.tool_name)
        request = AgentRequest.from_dict({**request.to_dict(), "previous_result": feedback})

    return {
        "scenario": scenario["name"],
        "turn_count": len(turn_records),
        "turns": turn_records,
        "passed": not failures,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "live-llm-acceptance-report.json"),
        help="path for the redacted acceptance report",
    )
    args = parser.parse_args(argv)

    import os

    if os.environ.get(GATE_FLAG) != "1":
        print(
            f"refusing to run: this gate is opt-in; set {GATE_FLAG}=1 to enable",
            file=sys.stderr,
        )
        return 2

    try:
        config = load_config()
    except AdapterConfigError as exc:
        print(f"provider configuration is not loadable: {exc}", file=sys.stderr)
        return 2

    scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
    adapter = OpenAICompatibleAgentModel(config)
    scenario_reports: list[dict[str, Any]] = []
    try:
        scenario_reports = [run_scenario(adapter, scenario) for scenario in scenarios]
    except AdapterConfigError as exc:
        print(f"live gate cannot run: {exc}", file=sys.stderr)
        return 2

    report = {
        "report_version": "sentinel.live-llm-acceptance.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "redaction": "no keys, auth headers, raw prompts, raw responses, or chain-of-thought",
        "provider": config.provider,
        "model": config.model,
        "prompt_revision": config.prompt_revision,
        "tool_policy_revision": config.tool_policy_revision,
        "allowed_tools": list(APPROVED_TOOL_NAMES),
        "scenarios": scenario_reports,
        "accepted": all(item["passed"] for item in scenario_reports),
    }
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"report written to {args.output}; accepted={report['accepted']}")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
