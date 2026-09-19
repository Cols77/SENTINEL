#!/usr/bin/env python
"""Consented live v2 Jev + LLM shadow replay.

Credentials are supplied by the calling environment. This script never opens
or prints a .env file; provider adapters read only named environment variables.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sentinel.evaluation import (  # noqa: E402
    AlwaysEscalateJudge,
    QUESTION_SET,
    decision_rule,
    run_cases,
)
from sentinel.judgment import JevJudge, LlmJudge  # noqa: E402

ART = REPO_ROOT / "artifacts" / "jev"
FIXTURE = REPO_ROOT / "fixtures" / "judgment" / "cases.v2.jsonl"
REPEATS = 3


def load_cases() -> list[dict]:
    cases = []
    with FIXTURE.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                source = json.loads(line)
                cases.append({
                    "case_id": source["case_id"],
                    "state": source["state"],
                    "reference": {
                        "hazard_relevant": source["hazard"],
                        "human_review_needed": source["needs_review"],
                    },
                })
    if len(cases) != 21:
        raise ValueError(f"expected 21 v2 cases, got {len(cases)}")
    return cases


def summarize_log(path: Path) -> dict:
    final = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("repeat") == REPEATS - 1:
                final[record["case_id"]] = record
    pos_fs = pos_scored = review_hits = review_needed = review_false_escalation = 0
    bands = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for case_id, record in final.items():
        reference = record.get("reference", {})
        answers = record.get("answers", {})
        hv = answers.get("hazard_relevant", {}).get("value")
        hl = reference.get("hazard_relevant")
        if case_id.startswith("pos-") and isinstance(hl, bool) and isinstance(hv, bool):
            pos_scored += 1
            pos_fs += int(hl and not hv)
        rv = answers.get("human_review_needed", {}).get("value")
        rl = reference.get("human_review_needed")
        if isinstance(rl, bool) and isinstance(rv, bool):
            if rl:
                review_needed += 1
                review_hits += int(rv)
            else:
                review_false_escalation += int(rv)
        for name in ("hazard_relevant", "human_review_needed"):
            confidence = answers.get(name, {}).get("confidence")
            if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                bands["unknown"] += 1
            elif confidence >= 0.8:
                bands["high"] += 1
            elif confidence >= 0.5:
                bands["medium"] += 1
            else:
                bands["low"] += 1
    return {
        "pos_clean_false_safe": {"false_safe": pos_fs, "scored": pos_scored},
        "needs_review_recall": {
            "hits": review_hits,
            "needed": review_needed,
            "recall": round(review_hits / review_needed, 3) if review_needed else None,
            "false_escalation_on_negatives": review_false_escalation,
        },
        "self_confidence_bands_noul": bands,
    }


def main() -> int:
    cases = load_cases()
    ART.mkdir(parents=True, exist_ok=True)
    specs = {
        "jev": JevJudge(
            api_key_env="OPENROUTER_API_KEY",
            base_url="https://openrouter.ai",
            timeout_s=120,
        ),
        "llm": LlmJudge(
            api_key_env="OPENROUTER_API_KEY",
            base_url="https://openrouter.ai/api",
            model="z-ai/glm-5.3-flash",
            timeout_s=120,
        ),
    }
    arms = {}
    for name, judge in specs.items():
        # Provenance fix (J5): run_cases() logs `getattr(judge, "model", "static")`,
        # but JevJudge/LlmJudge only carry the resolved model id on the private
        # `_model` attribute, so every logged record's `judge.model` read the
        # "static" default. Expose it publicly so future runs record the real,
        # resolved model id (never "static", never an unpinned "latest" alias).
        judge.model = judge._model
        path = ART / f"v2-live-{name}.jsonl"
        if path.exists():
            path.unlink()
        metrics, _ = run_cases(cases, judge, log_path=path, repeats=REPEATS)
        metrics["pos_and_review"] = summarize_log(path)
        arms[name] = {"metrics": metrics, "repeats": REPEATS}

    baseline_path = ART / ".v2-live-baseline.jsonl"
    baseline_metrics, _ = run_cases(
        cases, AlwaysEscalateJudge(), log_path=baseline_path, repeats=1
    )
    baseline_path.unlink(missing_ok=True)
    for arm in arms.values():
        arm["metrics"]["decision_rule_vs_always_escalate"] = decision_rule(
            arm["metrics"], {"always_escalate": baseline_metrics}
        )

    summary = {
        "title": "Jev v2 live shadow replay (J5)",
        "fixture": "fixtures/judgment/cases.v2.jsonl",
        "label_source": "approved v2 case-level hazard/needs_review labels projected into run_cases reference shape",
        "repeats": REPEATS,
        "network": "live OpenRouter calls; Jev /api/alpha/decisions and LLM /api/v1/chat/completions",
        "jev_model": specs["jev"]._model,
        "llm_model": specs["llm"]._model,
        "arms": arms,
        "always_escalate_baseline": baseline_metrics,
        "policy": {
            "jev_mode": "SHADOW_EVALUATION_ONLY",
            "worse_only_if_loses_to_always_escalate_on_false_safe_and_needs_review_recall": True,
        },
    }
    json_path = ART / "v2-live-summary.json"
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# J5 live v2 shadow replay summary", "",
        f"Fixture: {summary['fixture']}; repeats={REPEATS}; cases=21.",
        "Arms: Jev via /api/alpha/decisions; LLM baseline z-ai/glm-5.3-flash via JSON mode.",
        "Jev remains SHADOW_EVALUATION_ONLY regardless of outcome.", "",
        "| arm | hazard FS/FE/scored | review FS/FE/scored | clean-pos FS | review recall | stability | ranks vs always-escalate |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, arm in arms.items():
        m = arm["metrics"]
        h, r, p = m["hazard"], m["review"], m["pos_and_review"]
        stability = m["stability"]
        rule = m["decision_rule_vs_always_escalate"]
        lines.append(
            f"| {name} | {h['false_safe']}/{h['false_escalation']}/{h['scored']} "
            f"| {r['false_safe']}/{r['false_escalation']}/{r['scored']} "
            f"| {p['pos_clean_false_safe']['false_safe']}/{p['pos_clean_false_safe']['scored']} "
            f"| {p['needs_review_recall']['recall']} "
            f"| {stability['flipped']}/{stability['cases']} (count {stability['flip_count']}) "
            f"| {'YES' if rule['ranks_fixture'] else 'no'} |"
        )
    lines += [
        "",
        "Decision rule: an arm ranks only if it has equal-or-better false-safe outcomes and beats always-escalate on false escalation; the task's worse criterion additionally requires losing on clean-positive false-safe and needs_review recall.",
    ]
    (ART / "v2-live-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
