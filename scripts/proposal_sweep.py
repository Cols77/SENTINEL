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
from sentinel.coordinator.proposer_factory import build_proposer, require_proposer_consent, JEV_PROPOSER_MODEL
from sentinel.evaluation_run import (CallBudget, RawStream, RecordedTransport, MeasuredTransport,
    build_manifest, canonical, manifest_digest, require_authorization, sanitized)
from sentinel.proposal_eval import DEFAULT_CASES, load_case_bundle, run_case, score_outcome, aggregate, make_scripted_transport

CATALOGUE = ROOT / "docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json"


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


def _live_transport(arm, config, stream, identities):
    """One HTTP request per measured call; SDK retries are explicitly disabled."""
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
            if isinstance(raw, dict) and isinstance(raw.get("model"), str):
                identities.append(raw["model"])
            return raw
        return JevJudge(api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai",
                        model=JEV_PROPOSER_MODEL, transport=post)
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
            if isinstance(raw.get("model"), str):
                identities.append(raw["model"])
            stream.write({"record": "provider_wire_result", "response": raw})
            return response
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return OpenAICompatibleAgentModel(config, client_factory=client_factory)


def _render(manifest, records, metrics):
    lines = ["# Inspection capability baseline", "",
             f"Mode: **{manifest['mode']}**. Revision: `{manifest['revision']}`; dirty: {manifest['dirty']}.",
             f"Manifest: `{manifest_digest(manifest)}`.", "",
             "This is an outcome benchmark through the real policies, AgentLoop, repository and gateway.",
             "Approvals and judgment choices use a scripted operator policy; these are simulated approval tests, not measured human effort.",
             "Synthetic cases establish harness behavior only. Recorded responses are not fresh model measurements.",
             "No numerical model-release thresholds or autonomous-action promotion are established.", "",
             "| Case | Arm | Repeat | Success | Fresh provider calls | Failure reasons |", "|---|---|---:|---|---:|---|"]
    for row in records:
        score = row["metrics"]
        lines.append(f"| {row['case']['case_id']} | {row['arm']} | {row['repeat']} | {score['outcome_success']} | {row['provider_calls']} | {str(score['failure_reasons']).replace('|', '/')} |")
    lines += ["", "Aggregate metrics (explicit counts and denominators):", "", "```json",
              json.dumps(metrics, indent=2), "```", "",
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
                identities[key].append(model)
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
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--cost-per-call-usd", type=float, help="operator-supplied estimate, not a provider price guarantee")
    parser.add_argument("--recording", type=Path, help="previous raw.jsonl containing provider_result rows")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/proposal-eval/baseline")
    parser.add_argument("--execute", action="store_true", help="execute after validation; default only prints dry-run manifest")
    parser.add_argument("--dry-run", action="store_true", help="explicitly validate and print the manifest without execution")
    args = parser.parse_args(argv)
    try:
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
        llm_config = load_config() if args.mode == "live" and "llm" in arms else LLMConfig(
            "llm-adapter-config.v1", "openrouter", "offline", 1000, "v1", "v1")
        configurations = {
            arm: ({"provider": "scripted_operator", "requested_model": None} if arm == "F" else
                  {"provider": "openrouter", "requested_model": JEV_PROPOSER_MODEL, "prompt_revision": "select_plan.v1"} if arm == "jev" else
                  {**llm_config.to_dict(), "requested_model": llm_config.model})
            for arm in arms}
        for config in configurations.values():
            config.update(resolved_model=None, identity_status="unverified")
        maximum = sum(c.turn_limit for c in cases if c.setup != "pending_approval") * sum(a != "F" for a in arms) * args.repeats
        budget_limit = maximum if args.max_calls is None else args.max_calls
        manifest = build_manifest(root=ROOT, fixture=args.fixtures, catalogue=CATALOGUE, arms=arms,
                                  mode=args.mode, repeats=args.repeats, max_calls=budget_limit,
                                  configurations=configurations, cost_per_call_usd=args.cost_per_call_usd,
                                  recording=args.recording)
        manifest["maximum_possible_calls"] = maximum
        manifest["case_ids"] = [c.case_id for c in cases]
        manifest["case_definitions"] = [asdict(c) for c in cases]
        manifest["excluded_cases"] = excluded
        manifest["maximum_fresh_provider_calls"] = min(maximum, budget_limit) if args.mode == "live" else 0
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
        for repeat in range(1, args.repeats + 1):
            for case in cases:
                for arm in arms:
                    transports, identities, wire_requests = [], [], []
                    def append(row, repeat=repeat, case=case, arm=arm):
                        if row.get("record") == "provider_wire_request":
                            wire_requests.append(True)
                        raw.write({**row, "repeat": repeat, "case_id": case.case_id, "arm": arm.lower()})
                    stream = SimpleNamespace(write=append)
                    def proposer_factory(selected_arm, sink, selected_case):
                        if args.mode == "scripted":
                            transport = make_scripted_transport(selected_arm, selected_case)
                        elif args.mode == "recorded":
                            transport = RecordedTransport(selected_arm, captured[(selected_case.case_id, selected_arm, repeat)], strict=True)
                            identities.extend(captured_identities[(selected_case.case_id, selected_arm, repeat)])
                        else:
                            transport = _live_transport(selected_arm, llm_config, stream, identities)
                        measured = MeasuredTransport(transport, budget, stream, arm=selected_arm, case_id=selected_case.case_id)
                        transports.append(measured)
                        return build_proposer(selected_arm, sink=sink, mode=args.mode,
                            authorization=lambda: require_authorization(manifest), config=llm_config,
                            judge_factory=lambda **kw: measured, adapter_factory=lambda **kw: measured)
                    outcome = run_case(case, arm, args.output / f"repeat-{repeat}", proposer_factory=proposer_factory)
                    identity_expected = len(wire_requests) if args.mode == "live" else sum(t.calls for t in transports)
                    identity_status = "verified" if identities and len(identities) == identity_expected else "partially_verified" if identities else "unverified"
                    outcome.update(arm=arm, repeat=repeat, adapter_attempts=sum(t.calls for t in transports),
                                   provider_calls=len(wire_requests),
                                   provider_latency_ms=[v for t in transports for v in t.latencies],
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
                    outcome_path = Path(outcome["artifact_dir"]) / "final-outcome.json"
                    outcome["stage"] = "final_outcome"
                    outcome_path.write_text(canonical(sanitized(outcome)) + "\n", encoding="utf-8")
                    records.append(outcome)
                    stream.write({"record": "case_outcome", "outcome": outcome})
        metrics = {arm: aggregate([r for r in records if r["arm"] == arm]) for arm in arms}
        summary = {"manifest_sha256": manifest_digest(manifest), "calls_used": budget.used,
                   "budget_exhausted": budget.exhausted, "metrics": metrics}
        (args.output / "summary.json").write_text(canonical(sanitized(summary)) + "\n", encoding="utf-8")
        (args.output / "report.md").write_text(_render(manifest, records, metrics), encoding="utf-8")
        raw.write({"record": "run_complete", **summary})
        return 1 if budget.exhausted else 0
    except (ValueError, PermissionError, FileExistsError) as exc:
        print(f"evaluation refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
