#!/usr/bin/env python
"""Canonical inspection capability evaluation. Default is a no-call dry run.

Scripted and recorded executions are reproducibility checks, not fresh model
quality measurements. Live execution requires the dry-run manifest digest and
the existing provider-specific consent variables. No mode flies hardware.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from sentinel.adapter.llm.config import LLMConfig, load_config
from sentinel.adapter.llm.openai_compatible import _observation_from_mapping
from sentinel.coordinator.proposer_factory import build_proposer, require_proposer_consent, JEV_PROPOSER_MODEL
from sentinel.decision_prompt import DECISION_PROMPT_REVISION
from sentinel.evaluation_run import (CallBudget, RawStream, RecordedTransport, MeasuredTransport,
    build_manifest, canonical, file_digest, manifest_digest, require_authorization, sanitized)
from sentinel.proposal_eval import load_case_bundle, run_case, score_outcome, aggregate, make_scripted_transport

CATALOGUE = ROOT / "docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json"
PINNED_CASES = ROOT / "fixtures/proposal_eval/generated-requests-v1.json"
PINNED_FIXTURE_PATH = "fixtures/proposal_eval/generated-requests-v1.json"
PINNED_FIXTURE_SHA256 = "1b1089694fd74a82924c020bacfefc2e7a8b905077f05b8bbe01135777822f70"
REQUIRED_DECISION_PROMPT_REVISION = "select_plan.complete_constraints.v2"
LLM_PROMPT_REVISION = "prompt-v4-complete-constraints"
LLM_TOOL_POLICY_REVISION = "legal-tools-v2"
MODEL_ID_MAX_LENGTH = 256
TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")
PINNED_CASE_IDS = (
    "generated_middle_only", "generated_short_route", "generated_no_ends",
    "generated_all_tags", "generated_complete_verified", "generated_not_short",
    "generated_left_only", "generated_front_pair", "generated_verified_middle",
    "generated_unspecified", "generated_conflicting_coverage", "generated_visible_computers",
)


def _arm_mappings(values, selected_arms, option, *, max_length=None):
    mappings = {}
    selected = {arm.lower() for arm in selected_arms}
    for item in values:
        if "=" not in item:
            raise ValueError(f"{option} must use ARM=VALUE")
        arm, value = item.split("=", 1)
        arm = arm.strip().lower()
        value = value.strip()
        if arm not in {"jev", "llm"} or arm not in selected:
            raise ValueError(f"{option} arm {arm!r} is not a selected model arm")
        if not value:
            raise ValueError(f"{option} requires a non-empty value for {arm}")
        if max_length is not None and len(value) > max_length:
            raise ValueError(f"{option} value for {arm} exceeds {max_length} characters")
        if arm in mappings:
            raise ValueError(f"{option} repeats arm {arm}")
        mappings[arm] = value
    return mappings


def _fixture_path(path):
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _classify_identity(observed, expected, expected_observations):
    values = [value for value in observed
              if isinstance(value, str) and value and len(value) <= MODEL_ID_MAX_LENGTH]
    if any(value != expected for value in values):
        return "mismatched"
    if not values:
        return "unverified"
    if len(values) == expected_observations:
        return "verified"
    return "partially_verified"


def _sum_usage(observations, expected_calls):
    usage = {}
    for field in TOKEN_FIELDS:
        values = [row.get("usage", {}).get(field) for row in observations]
        usage[field] = sum(values) if expected_calls and len(values) == expected_calls and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) for value in values
        ) else None
    return usage


def _sum_trial_usage(records):
    calls = sum(record.get("provider_calls", 0) for record in records)
    result = {}
    for field in TOKEN_FIELDS:
        values = [record.get("token_usage", {}).get(field) for record in records]
        result[field] = sum(values) if calls and all(value is not None for value in values) else None
    return result


def _evaluate_live_gate(manifest, trials, *, identity_status):
    fixture_matches = (
        manifest.get("fixture_path") == PINNED_FIXTURE_PATH
        and manifest.get("fixture_sha256") == PINNED_FIXTURE_SHA256
        and manifest.get("pinned_fixture_sha256") == PINNED_FIXTURE_SHA256
    )
    trial_keys = [(row.get("case_id"), row.get("repeat")) for row in trials]
    complete_trials = (
        len(trials) == 36
        and sorted(trial_keys) == sorted(
            (case_id, repeat) for repeat in range(1, 4) for case_id in PINNED_CASE_IDS)
        and all(row.get("event_counts") == {
            "provider_request": 1, "provider_result": 1,
            "proposal_attempt": 1, "case_outcome": 1,
        } for row in trials)
    )
    applicable = (
        manifest.get("mode") == "live"
        and len(manifest.get("arms", [])) == 1
        and manifest.get("arms", [None])[0].lower() in {"jev", "llm"}
        and manifest.get("repeats") == 3
        and manifest.get("max_calls") == 36
        and fixture_matches
        and complete_trials
    )
    supported = [row for row in trials if row.get("supported")]
    escalations = [row for row in trials if not row.get("supported")]
    supported_correct = sum(row.get("correct_supported_trial") is True for row in supported)
    safe_escalation = sum(row.get("safe_escalation_trial") is True for row in escalations)
    false_proposals = sum(bool(row.get("unsupported_false_proposal")) for row in escalations)
    f_fallbacks = sum(int(row.get("architecture_f_fallbacks", 0)) for row in trials)
    reasons = []
    if not fixture_matches:
        reasons.append("fixture_mismatch")
    if len(trials) != 36:
        reasons.append("terminal_trial_count")
    if trials and not complete_trials:
        reasons.append("trial_event_shape")
    if identity_status != "verified":
        reasons.append("identity_" + identity_status)
    if supported_correct != 18:
        reasons.append("supported_correct")
    if safe_escalation != 18:
        reasons.append("safe_escalation")
    if false_proposals:
        reasons.append("unsupported_false_proposals")
    if f_fallbacks:
        reasons.append("architecture_f_fallbacks")
    passed = applicable and not reasons
    return {
        "live_gate_applicable": applicable,
        "passed": passed,
        "supported_correct": supported_correct,
        "supported_denominator": 18,
        "safe_escalation": safe_escalation,
        "escalation_denominator": 18,
        "unsupported_false_proposals": false_proposals,
        "unsupported_denominator": 18,
        "architecture_f_fallbacks": f_fallbacks,
        "f_credit": 0,
        "terminal_trials": len(trials),
        "failure_reasons": reasons,
    }


def _http_evidence(response, stream, *, error_category=None):
    if response is None:
        stream.write({"record": "provider_wire_result", "http_status": None,
                      "response": None, "error_category": error_category or "transport_error"})
        return None
    try:
        body = response.json()
    except ValueError:
        text = response.text
        body = {"unparsed_body": text[:8192], "body_truncated": len(text) > 8192}
        error_category = error_category or "malformed_response"
    stream.write({"record": "provider_wire_result", "http_status": response.status_code,
                  "response": body, "error_category": error_category})
    return body


def _live_transport(arm, config, stream, observations):
    """One HTTP request per measured call; SDK retries are explicitly disabled."""
    def observe(row):
        stream.write({"record": "provider_observation", **row})
        observations.append(row)

    if arm == "jev":
        import os
        from sentinel.judgment import JevJudge
        def post(payload):
            import httpx
            stream.write({"record": "provider_wire_request", "request": payload})
            try:
                response = httpx.post("https://openrouter.ai/api/alpha/decisions",
                                     headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]},
                                     json=payload, timeout=5.0)
            except Exception:
                _http_evidence(None, stream)
                raise
            _http_evidence(response, stream, error_category="http_error" if response.status_code >= 400 else None)
            response.raise_for_status()
            raw = response.json()
            return raw
        return JevJudge(api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai",
                        model=JEV_PROPOSER_MODEL, transport=post,
                        observation_callback=observe)
    from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
    def client_factory(**kwargs):
        from openai import OpenAI
        client = OpenAI(max_retries=0, **kwargs)
        def create(**request):
            stream.write({"record": "provider_wire_request", "request": request})
            try:
                response = client.chat.completions.create(**request)
            except Exception as exc:
                _http_evidence(getattr(exc, "response", None), stream, error_category="provider_error")
                raise
            raw = response.model_dump()
            stream.write({"record": "provider_wire_result", "response": raw})
            return response
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return OpenAICompatibleAgentModel(
        config, client_factory=client_factory, observation_callback=observe)


def _render(manifest, records, metrics, telemetry, live_gate):
    lines = ["# Inspection capability baseline", "",
             f"Mode: **{manifest['mode']}**. Revision: `{manifest['revision']}`; dirty: {manifest['dirty']}.",
             f"Manifest: `{manifest_digest(manifest)}`.", "",
             "This is an outcome benchmark through the real policies, AgentLoop, repository and gateway.",
             "Approvals and judgment choices use a scripted operator policy; these are simulated approval tests, not measured human effort.",
             "Synthetic cases establish harness behavior only. Recorded responses are not fresh model measurements.",
             "No numerical model-release thresholds or autonomous-action promotion are established.", "",
             "| Case | Arm | Repeat | Success | Fresh provider calls | Prompt tokens | Completion tokens | Total tokens | Latency ms | Estimated cost USD | Failure reasons |",
             "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|"]
    for row in records:
        score = row["metrics"]
        usage = row.get("token_usage", {})
        latency = sum(row.get("provider_latency_ms", []))
        lines.append(f"| {row['case']['case_id']} | {row['arm']} | {row['repeat']} | {score['outcome_success']} | {row['provider_calls']} | {usage.get('prompt_tokens')} | {usage.get('completion_tokens')} | {usage.get('total_tokens')} | {latency:.3f} | {row.get('estimated_cost_usd')} | {str(score['failure_reasons']).replace('|', '/')} |")
    lines += ["", "Aggregate metrics (explicit counts and denominators):", "", "```json",
              json.dumps(metrics, indent=2), "```", "",
              "Provider telemetry:", "", "```json", json.dumps(telemetry, indent=2), "```", "",
              "Live gate (fixed denominators):", "",
              "| Applicable | Passed | Supported correct / 18 | Safe escalation / 18 | Unsupported false proposals / 18 | F credit |",
              "|---|---|---:|---:|---:|---:|",
              f"| {live_gate['live_gate_applicable']} | {live_gate['passed']} | {live_gate['supported_correct']} / 18 | {live_gate['safe_escalation']} / 18 | {live_gate['unsupported_false_proposals']} / 18 | 0 |", "",
              "Rationale completeness is measured separately; factual support remains unassessed unless independently checked.",
              "Resolved model identities are in each case's telemetry. Missing provider identity is unverified.",
              "Historical direct-adapter and shadow reports remain unchanged, are legacy regression evidence, and are not pooled here.",
              "Survey counting is outside the supported inspection catalogue; see the separate survey readiness report."]
    return "\n".join(lines) + "\n"


def _load_recording(path, manifest, cases, arms, repeats):
    captured, identities = defaultdict(list), defaultdict(list)
    source_manifest, pending = None, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("record") == "run_manifest":
            if source_manifest is not None:
                raise ValueError("recording contains multiple run manifests")
            source_manifest = row["manifest"]
            continue
        if "case_id" not in row or "arm" not in row:
            continue
        key = (row["case_id"], row["arm"], row.get("repeat", 1))
        if row.get("record") == "provider_request":
            if key in pending:
                raise ValueError("recording has a request without its result")
            pending[key] = row["request"]
        if row.get("record") == "provider_result":
            if key not in pending:
                raise ValueError("recorded result lacks its request")
            captured[key].append({**row, "request": pending.pop(key)})
        if row.get("record") == "provider_wire_result":
            model = row.get("response", {}).get("model") if isinstance(row.get("response"), dict) else None
            if isinstance(model, str):
                identities[key].append(_observation_from_mapping({"model": model})["model"])
    if pending:
        raise ValueError("recording ends with an unfinished request")
    if not source_manifest or source_manifest.get("fixture_sha256") != manifest["fixture_sha256"] or source_manifest.get("catalogue_sha256") != manifest["catalogue_sha256"]:
        raise ValueError("recording requires matching source fixture and catalogue manifests")
    requested = {(c.case_id, a.lower(), r) for c in cases for a in arms for r in range(1, repeats + 1)}
    if set(captured) - requested:
        raise ValueError("recording contains cases/arms/repeats outside this run")
    return captured, identities, source_manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("scripted", "recorded", "live"), default="scripted")
    parser.add_argument("--arms", nargs="+", choices=("F", "llm", "jev"), default=["F", "llm", "jev"])
    parser.add_argument("--fixtures", type=Path, default=PINNED_CASES)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--cost-per-call-usd", type=float, help="operator-supplied estimate, not a provider price guarantee")
    parser.add_argument("--expected-resolved-model", action="append", default=[], metavar="ARM=MODEL_ID",
                        help="reviewed exact provider model identity; repeat once per model arm")
    parser.add_argument("--identity-decision-note", action="append", default=[], metavar="ARM=TEXT",
                        help="review note for the expected identity; repeat once per model arm")
    parser.add_argument("--recording", type=Path, help="previous raw.jsonl containing provider_result rows")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/proposal-eval/baseline")
    parser.add_argument("--execute", action="store_true", help="execute after validation; default only prints dry-run manifest")
    parser.add_argument("--dry-run", action="store_true", help="explicitly validate and print the manifest without execution")
    args = parser.parse_args(argv)
    try:
        if DECISION_PROMPT_REVISION != REQUIRED_DECISION_PROMPT_REVISION:
            raise ValueError(
                "decision prompt revision must be "
                f"{REQUIRED_DECISION_PROMPT_REVISION}, got {DECISION_PROMPT_REVISION}")
        pinned_runtime_digest = file_digest(PINNED_CASES)
        if pinned_runtime_digest != PINNED_FIXTURE_SHA256:
            raise ValueError(
                "canonical fixture SHA-256 must be "
                f"{PINNED_FIXTURE_SHA256}, got {pinned_runtime_digest}")
        cases, excluded = load_case_bundle(args.fixtures)
        if not cases:
            raise ValueError("no evaluable cases")
        if args.mode == "recorded" and args.recording is None:
            raise ValueError("recorded mode requires --recording")
        if args.recording is not None and args.mode != "recorded":
            raise ValueError("--recording is only valid in recorded mode")
        if args.mode == "live" and args.repeats != 3:
            raise ValueError("initial live baseline requires three repeats")
        arms = list(dict.fromkeys(args.arms))
        expected_models = _arm_mappings(
            args.expected_resolved_model, arms, "--expected-resolved-model",
            max_length=MODEL_ID_MAX_LENGTH)
        identity_notes = _arm_mappings(
            args.identity_decision_note, arms, "--identity-decision-note")
        model_arms = {arm.lower() for arm in arms if arm.lower() != "f"}
        if args.mode == "live":
            missing_expected = sorted(model_arms - set(expected_models))
            missing_notes = sorted(model_arms - set(identity_notes))
            missing = []
            if missing_expected:
                missing.append("--expected-resolved-model required for " + ", ".join(missing_expected))
            if missing_notes:
                missing.append("--identity-decision-note required for " + ", ".join(missing_notes))
            if missing:
                raise ValueError("; ".join(missing))
        llm_config = load_config() if args.mode == "live" and "llm" in arms else LLMConfig(
            "llm-adapter-config.v1", "openrouter", "offline", 1000,
            LLM_PROMPT_REVISION, LLM_TOOL_POLICY_REVISION)
        if "llm" in arms and (
            llm_config.prompt_revision != LLM_PROMPT_REVISION
            or llm_config.tool_policy_revision != LLM_TOOL_POLICY_REVISION
        ):
            raise ValueError(
                f"llm requires prompt revision {LLM_PROMPT_REVISION} and "
                f"tool policy revision {LLM_TOOL_POLICY_REVISION}")
        configurations = {
            arm: ({"provider": "scripted_operator", "requested_model": None} if arm.lower() == "f" else
                  {"provider": "openrouter", "requested_model": JEV_PROPOSER_MODEL,
                   # Historical readers consume prompt_revision. It is the same
                   # semantic SELECT_PLAN revision recorded explicitly below.
                   "prompt_revision": DECISION_PROMPT_REVISION} if arm == "jev" else
                  {**llm_config.to_dict(), "requested_model": llm_config.model})
            for arm in arms}
        for arm, config in configurations.items():
            key = arm.lower()
            requested_model = config.get("requested_model")
            if (isinstance(requested_model, str)
                    and len(requested_model) > MODEL_ID_MAX_LENGTH):
                raise ValueError(
                    f"requested model identity for {key} exceeds {MODEL_ID_MAX_LENGTH} characters")
            config.update(
                decision_prompt_revision=DECISION_PROMPT_REVISION if key in {"jev", "llm"} else None,
                expected_resolved_model=expected_models.get(key),
                identity_decision_note=identity_notes.get(key),
                resolved_model=None,
                identity_status="unverified",
            )
        is_pinned_pack = (_fixture_path(args.fixtures) == PINNED_FIXTURE_PATH
                          and tuple(c.case_id for c in cases) == PINNED_CASE_IDS)
        per_arm_calls = (len(cases) if is_pinned_pack else
                         sum(c.turn_limit for c in cases if c.setup != "pending_approval"))
        maximum = per_arm_calls * sum(a.lower() != "f" for a in arms) * args.repeats
        budget_limit = maximum if args.max_calls is None else args.max_calls
        manifest = build_manifest(root=ROOT, fixture=args.fixtures, catalogue=CATALOGUE, arms=arms,
                                  mode=args.mode, repeats=args.repeats, max_calls=budget_limit,
                                  configurations=configurations, cost_per_call_usd=args.cost_per_call_usd,
                                  recording=args.recording)
        manifest["maximum_possible_calls"] = maximum
        manifest["fixture_path"] = _fixture_path(args.fixtures)
        manifest["pinned_fixture_sha256"] = PINNED_FIXTURE_SHA256
        if (manifest["fixture_path"] == PINNED_FIXTURE_PATH
                and manifest["fixture_sha256"] != PINNED_FIXTURE_SHA256):
            raise ValueError(
                "selected canonical fixture SHA-256 must be "
                f"{PINNED_FIXTURE_SHA256}, got {manifest['fixture_sha256']}")
        manifest["case_ids"] = [c.case_id for c in cases]
        manifest["case_definitions"] = [asdict(c) for c in cases]
        manifest["excluded_cases"] = excluded
        manifest["maximum_fresh_provider_calls"] = min(maximum, budget_limit) if args.mode == "live" else 0
        manifest["token_accounting"] = {field: None for field in TOKEN_FIELDS}
        captured, captured_identities = defaultdict(list), defaultdict(list)
        if args.recording:
            captured, captured_identities, source_manifest = _load_recording(args.recording, manifest, cases, arms, args.repeats)
            manifest["recorded_source"] = {"manifest_sha256": manifest_digest(source_manifest),
                "configurations": source_manifest.get("configurations"), "mode": source_manifest.get("mode")}
            manifest["configurations"] = source_manifest.get("configurations", configurations)
        print(json.dumps({"manifest": manifest, "authorization_sha256": manifest_digest(manifest)}, indent=2))
        if not args.execute or args.dry_run:
            return 0
        require_authorization(manifest)  # before providers, output directory or artifacts
        if args.mode == "live":
            for arm in arms:
                if arm != "F":
                    require_proposer_consent(arm)
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
        raw = RawStream(args.output / "raw.jsonl")
        raw.write({"record": "run_manifest", "manifest": manifest})
        budget = CallBudget(budget_limit)
        records = []
        trials = []
        for repeat in range(1, args.repeats + 1):
            for case in cases:
                for arm in arms:
                    transports, observations, wire_requests = [], [], []
                    event_counts = defaultdict(int)
                    def append(row, repeat=repeat, case=case, arm=arm):
                        if row.get("record") == "provider_wire_request":
                            wire_requests.append(True)
                        if row.get("record") in {"provider_request", "provider_result", "proposal_attempt", "case_outcome"}:
                            event_counts[row["record"]] += 1
                        raw.write({**row, "repeat": repeat, "case_id": case.case_id, "arm": arm.lower()})
                    stream = SimpleNamespace(write=append)
                    def proposer_factory(selected_arm, sink, selected_case):
                        if args.mode == "scripted":
                            transport = make_scripted_transport(selected_arm, selected_case)
                        elif args.mode == "recorded":
                            transport = RecordedTransport(selected_arm, captured[(selected_case.case_id, selected_arm, repeat)], strict=True)
                            observations.extend({"model": model, "usage": {field: None for field in TOKEN_FIELDS}}
                                                for model in captured_identities[(selected_case.case_id, selected_arm, repeat)])
                        else:
                            transport = _live_transport(selected_arm, llm_config, stream, observations)
                        measured = MeasuredTransport(transport, budget, stream, arm=selected_arm, case_id=selected_case.case_id)
                        transports.append(measured)
                        return build_proposer(selected_arm, sink=sink, mode=args.mode,
                            authorization=lambda: require_authorization(manifest), config=llm_config,
                            judge_factory=lambda **kw: measured, adapter_factory=lambda **kw: measured)
                    outcome = run_case(case, arm, args.output / f"repeat-{repeat}", proposer_factory=proposer_factory)
                    identity_expected = len(wire_requests) if args.mode == "live" else sum(t.calls for t in transports)
                    identities = [row["model"] for row in observations if row.get("model")]
                    expected_model = configurations[arm].get("expected_resolved_model")
                    identity_status = (_classify_identity(identities, expected_model, identity_expected)
                                       if expected_model else "unverified")
                    provider_calls = len(wire_requests)
                    token_usage = _sum_usage(observations, provider_calls)
                    outcome.update(arm=arm, repeat=repeat, adapter_attempts=sum(t.calls for t in transports),
                                   provider_calls=provider_calls,
                                   provider_latency_ms=[v for t in transports for v in t.latencies],
                                   token_usage=token_usage,
                                   estimated_cost_usd=(None if args.cost_per_call_usd is None
                                                       else provider_calls * args.cost_per_call_usd),
                                   observed_model_identities=identities,
                                   resolved_models=sorted(set(identities)), identity_status=identity_status)
                    if any(getattr(t, "budget_refusals", 0) for t in transports):
                        outcome["error"] = "budget_exhausted"
                        outcome["metrics"] = score_outcome(case, outcome)
                    elif any(getattr(t, "failures", []) for t in transports):
                        outcome["error"] = "transport_failure"
                        outcome["metrics"] = score_outcome(case, outcome)
                    if any(getattr(t.transport, "exhausted", False) or getattr(t.transport, "invalid", False) or getattr(t.transport, "remaining", 0) for t in transports):
                        outcome["error"] = "recording_incomplete"
                        outcome["metrics"] = score_outcome(case, outcome)
                    proposal_attempts = [row for row in outcome.get("audit", [])
                                         if row.get("record") == "proposal_attempt"]
                    for proposal_attempt in proposal_attempts:
                        stream.write(proposal_attempt)
                    counts = {name: event_counts.get(name, 0) for name in (
                        "provider_request", "provider_result", "proposal_attempt", "case_outcome")}
                    counts["case_outcome"] += 1
                    outcome["event_counts"] = counts
                    outcome_path = Path(outcome["artifact_dir"]) / "final-outcome.json"
                    outcome["stage"] = "final_outcome"
                    outcome_path.write_text(canonical(sanitized(outcome)) + "\n", encoding="utf-8")
                    records.append(outcome)
                    stream.write({"record": "case_outcome", "outcome": outcome})
                    metrics_row = outcome["metrics"]
                    f_fallbacks = metrics_row.get("architecture_f_fallbacks", 0)
                    trials.append({
                        "case_id": case.case_id,
                        "repeat": repeat,
                        "supported": not case.expected_escalation,
                        "correct_supported_trial":
                            metrics_row.get("correct_supported_trial") is True,
                        "safe_escalation_trial":
                            metrics_row.get("safe_escalation_trial") is True,
                        "unsupported_false_proposal": metrics_row.get("unsupported_false_proposals", 0) > 0,
                        "architecture_f_fallbacks": f_fallbacks,
                        "event_counts": counts,
                    })
        metrics = {arm: aggregate([r for r in records if r["arm"] == arm]) for arm in arms}
        telemetry = {}
        arm_identity_statuses = {}
        for arm in arms:
            arm_records = [record for record in records if record["arm"] == arm]
            observed = [model for record in arm_records
                        for model in record.get("observed_model_identities", [])]
            calls = sum(record.get("provider_calls", 0) for record in arm_records)
            expected = configurations[arm].get("expected_resolved_model")
            status = _classify_identity(observed, expected, calls) if expected else "unverified"
            arm_identity_statuses[arm] = status
            telemetry[arm] = {
                "provider_calls": calls,
                **_sum_trial_usage(arm_records),
                "latency_ms": sum(sum(record.get("provider_latency_ms", [])) for record in arm_records),
                "estimated_cost_usd": (None if args.cost_per_call_usd is None
                                       else calls * args.cost_per_call_usd),
                "resolved_models": sorted(set(observed)),
                "observed_model_identities": observed,
                "identity_status": status,
            }
            metric = metrics[arm]
            metric["decision_outcomes"] = {
                "accepted_plan": metric.get("accepted_plan_attempts", 0),
                "explicit_escalation": metric.get("explicit_escalations", 0),
                "confidence_gate": metric.get("confidence_gate_escalations", 0),
                "deterministic_refusal": metric.get("deterministic_refusals", 0),
                "provider_failure": metric.get("provider_failures", 0),
                "architecture_f_fallback": metric.get("architecture_f_fallbacks", 0),
                "plan_attempt_denominator": metric.get("plan_attempt_denominator", 0),
                "case_denominator": metric.get("case_denominator", 0),
            }
        gate_identity = arm_identity_statuses.get(arms[0], "unverified") if len(arms) == 1 else "unverified"
        live_gate = _evaluate_live_gate(manifest, trials, identity_status=gate_identity)
        summary = {"manifest_sha256": manifest_digest(manifest), "calls_used": budget.used,
                   "budget_exhausted": budget.exhausted, "metrics": metrics,
                   "telemetry": telemetry, "live_gate": live_gate}
        (args.output / "summary.json").write_text(canonical(sanitized(summary)) + "\n", encoding="utf-8")
        (args.output / "report.md").write_text(
            _render(manifest, records, metrics, telemetry, live_gate), encoding="utf-8")
        raw.write({"record": "run_complete", **summary})
        return 1 if budget.exhausted or live_gate["live_gate_applicable"] and not live_gate["passed"] else 0
    except (ValueError, PermissionError, FileExistsError) as exc:
        print(f"evaluation refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
