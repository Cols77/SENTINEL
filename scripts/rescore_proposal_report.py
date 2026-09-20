#!/usr/bin/env python
"""Rescore retained final outcomes without rerunning policies or providers."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sentinel.evaluation_run import RawStream, canonical, manifest_digest
from sentinel.proposal_eval import Case, aggregate, load_case_bundle, score_outcome


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    original = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if sha(ROOT / "docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json") != original["catalogue_sha256"]:
        raise ValueError("catalogue changed; retained outcomes require their execution catalogue")
    cases = {case["case_id"]: Case.from_dict(case) for case in original["case_definitions"]}
    expected = {(case, arm.lower(), repeat) for case in cases for arm in original["arms"]
                for repeat in range(1, original["repeats"] + 1)}
    records, seen, inputs = [], set(), {}
    for path in sorted(source.rglob("final-outcome.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        key = row["case_id"], row["arm"].lower(), row["repeat"]
        if key in seen or key not in expected or Case.from_dict(row["case"]) != cases[key[0]]:
            raise ValueError("duplicate, unexpected or altered case definition")
        if row.get("stage") != "final_outcome":
            raise ValueError("authoritative final outcome required")
        seen.add(key)
        inputs[str(path.relative_to(source))] = sha(path)
        row["metrics"] = score_outcome(cases[key[0]], row)
        records.append(row)
    if seen != expected:
        raise ValueError("incomplete source case/arm/repeat matrix")
    for filename in ("manifest.json", "raw.jsonl", "summary.json", "report.md"):
        inputs[filename] = sha(source / filename)
    census = {}
    for filename in ("fixtures/decisions/turns.v1.jsonl", "fixtures/decisions_v2/turns.v2.jsonl"):
        included, excluded = load_case_bundle(ROOT / filename)
        census[filename] = {"sha256": sha(ROOT / filename), "included": len(included),
                            "excluded": len(excluded), "exclusion_reasons": dict(Counter(row["reason"] for row in excluded))}
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    manifest = {"version": "proposal-eval-rescore.v1", "operation": "saved_outcome_rescore",
                "source_directory": str(source), "execution_manifest_sha256": manifest_digest(original),
                "execution_revision": original["revision"], "execution_dirty": original["dirty"],
                "execution_source_sha256": original["source_sha256"], "execution_mode": original["mode"],
                "scoring_revision": revision, "scoring_dirty": bool(dirty),
                "scoring_dirty_status_sha256": hashlib.sha256(dirty.encode()).hexdigest(),
                "scorer_sha256": sha(ROOT / "src/sentinel/proposal_eval.py"),
                "rescore_script_sha256": sha(Path(__file__)), "input_sha256": inputs,
                "new_policy_calls": 0, "new_provider_calls": 0, "catalogue_sha256": original["catalogue_sha256"],
                "fixture_sha256": original["fixture_sha256"], "repeats": original["repeats"],
                "quality_claim": "synthetic scripted wiring evidence; no model-quality measurement"}
    metrics = {arm: aggregate([row for row in records if row["arm"].lower() == arm.lower()]) for arm in original["arms"]}
    details = {}
    for arm in metrics:
        rows = [row for row in records if row["arm"].lower() == arm.lower()]
        scores = [row["metrics"] for row in rows]
        details[arm] = {key: sum(score[key] for score in scores) for key in
                       ("turns", "retries", "reproposals", "proposal_fallbacks", "rationale_complete", "rationale_denominator", "proposal_latency_ms")}
        details[arm].update(provider_calls=sum(row["provider_calls"] for row in rows),
                            adapter_attempts=sum(row["adapter_attempts"] for row in rows),
                            accepted_commands=sum(attempt.get("accepted", False) for row in rows for attempt in row["tool_attempts"]),
                            command_attempts=sum(len(row["tool_attempts"]) for row in rows),
                            escalation_cases=sum(row["case"]["expected_escalation"] for row in rows),
                            successful_escalation_cases=sum(row["case"]["expected_escalation"] and row["metrics"]["outcome_success"] for row in rows),
                            completed_terminal_states=sum(row["final_state"].get("run_status") == "COMPLETED" for row in rows),
                            unsupported_cases=sum(row["case_id"] == "unsupported_computer_count" for row in rows))
        boundaries = []
        for row in rows:
            approved_plans, proposed_tasks = set(), set()
            evidence = {item["result_id"]: json.loads(item["result_json"]).get("station_capture", {}).get("plan_id")
                        for item in row["snapshot"].get("perception_references", [])}
            for event in row["events"]:
                payload = event.get("payload", {})
                if event["event_type"] == "PLAN_APPROVED":
                    approved_plans.add(payload.get("target", {}).get("plan_reference", {}).get("plan_id"))
                elif event["event_type"] == "TASK_CREATED":
                    boundaries.append(evidence.get(payload.get("perception_result_id")) not in approved_plans)
                elif event["event_type"] == "PATROLLER_ASSIGNMENT_PROPOSED":
                    proposed_tasks.add(payload.get("task_id"))
                elif event["event_type"] == "PATROLLER_ASSIGNMENT_CONFIRMED":
                    target = payload.get("target", {})
                    boundaries.append(payload.get("decision") != "CONFIRM" or target.get("kind") != "TASK" or target.get("task_id") not in proposed_tasks)
        details[arm].update(approval_boundary_effects=len(boundaries), approval_boundary_violations=sum(boundaries))
    summary = {"manifest_sha256": manifest_digest(manifest), "metrics": metrics, "details": details, "labelled_fixture_census": census}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
    raw = RawStream(args.output / "raw.jsonl")
    raw.write({"record": "rescore_manifest", "manifest": manifest})
    lines = ["# Inspection retained-outcome rescore", "", "No policies, providers or hardware were called.", "",
             "| Case | Arm | Success | Failure reasons |", "|---|---|---|---|"]
    for row in records:
        raw.write({"record": "rescored_outcome", "case_id": row["case_id"], "arm": row["arm"], "repeat": row["repeat"], "metrics": row["metrics"]})
        lines.append(f"| {row['case_id']} | {row['arm']} | {row['metrics']['outcome_success']} | {', '.join(row['metrics']['failure_reasons'])} |")
    raw.write({"record": "rescore_complete", "summary": summary})
    (args.output / "summary.json").write_text(canonical(summary) + "\n", encoding="utf-8")
    (args.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
