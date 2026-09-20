#!/usr/bin/env python
"""J4 offline dry run: v2 fixture through the fixed harness, no network.

Arms:
- static_v2: the demo StaticJudge (constant hazard=True@0.9) -- included to
  document that its 0 false-safe is by construction, not evidence.
- baseline-always-safe / baseline-always-escalate / baseline-static-rules:
  the trivial baselines from evaluation.py (deterministic, repeats=1).
- jev-fake-transport: JevJudge with an INJECTED scripted transport (offline,
  no socket). Proves the adapter + scoring path end-to-end without a live
  provider call.

Label source: the APPROVED v2 labels are the case-level ``hazard`` /
``needs_review`` fields (null hazard = ambiguous, unscored for hazard). The
embedded ``reference`` dict is v1 kept only for traceability, so the dry run
projects the v2 labels into the reference shape run_cases scores against.

Writes per-arm JSONL logs + artifacts/jev/v2-dryrun-summary.{json,md}.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sentinel.evaluation import (  # noqa: E402
    AlwaysEscalateJudge,
    AlwaysSafeJudge,
    QUESTION_SET,
    StaticRulesJudge,
    decision_rule,
    run_cases,
)
from sentinel.judgment import JevJudge, StaticJudge  # noqa: E402

REPEATS = 3
ART = REPO_ROOT / "artifacts" / "jev"
FIXTURE = REPO_ROOT / "fixtures" / "judgment" / "cases.v2.jsonl"
CONFIDENT = 0.8
MIN_SIZE_M = 0.3


def load_cases() -> list[dict]:
    """v2 fixture projected into the reference shape run_cases scores."""
    cases = []
    with FIXTURE.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            c = json.loads(line)
            cases.append({
                "case_id": c["case_id"],
                "state": c["state"],
                # approved v2 labels; null hazard stays null (skipped for hazard)
                "reference": {
                    "hazard_relevant": c["hazard"],
                    "human_review_needed": c["needs_review"],
                },
            })
    return cases


# --------------------------------------------------------------------------
# Fake Jev transport: scripted answers in the Jev decisions wire format.
# --------------------------------------------------------------------------

def scripted_jev_answers(state: dict) -> dict:
    obs = state.get("observation") or {}
    conf = obs.get("perception_confidence")
    size = obs.get("size_m")
    pos = obs.get("position_relation")
    duplicate = obs.get("duplicate_of_existing_detection")

    confident = isinstance(conf, (int, float)) and conf >= CONFIDENT
    sizable = isinstance(size, (int, float)) and size >= MIN_SIZE_M
    on_path = pos in {"on_trail", "at_emergency_gate", "trail_edge"}

    if duplicate:
        hazard_p = 0.08
    elif on_path and confident and (sizable or size is None):
        hazard_p = max(conf, 0.85)
    elif on_path and confident:
        hazard_p = 0.35
    elif on_path:
        hazard_p = 0.45
    else:
        hazard_p = 0.05

    review_p = 0.85 if (not confident or 0.3 <= hazard_p < 0.8 or duplicate) else 0.12
    if hazard_p >= 0.8 and isinstance(conf, (int, float)) and conf >= 0.9:
        review_p = 0.4

    probabilities = {"ignore": 0.6, "low": 0.2, "medium": 0.1, "high": 0.05, "critical": 0.05}
    priority = "ignore"
    if hazard_p >= 0.9:
        priority, probabilities = "high", {"ignore": 0.02, "low": 0.03, "medium": 0.1, "high": 0.7, "critical": 0.15}
    elif hazard_p >= 0.5:
        priority, probabilities = "medium", {"ignore": 0.1, "low": 0.15, "medium": 0.55, "high": 0.15, "critical": 0.05}
    elif hazard_p >= 0.3:
        priority, probabilities = "low", {"ignore": 0.3, "low": 0.5, "medium": 0.15, "high": 0.03, "critical": 0.02}

    score = 0.0 if hazard_p < 0.3 else (1.5 if hazard_p < 0.8 else 3.0)

    return {"answers": {
        "hazard_relevant": {"noul": round(hazard_p, 3)},
        "human_review_needed": {"noul": round(review_p, 3)},
        "more_evidence_needed": {"noul": round(1.0 - min(hazard_p, 0.99), 3)},
        "priority": {"choice": priority, "probabilities": probabilities,
                     "confidence": 0.7 if priority in {"medium", "high", "critical"} else 0.4},
        "priority_scored": {"score": score,
                            "legend": {str(i): d for i, d in enumerate(QUESTION_SET[2]["criteria"])},
                            "probabilities": {"0": 0.2, "1": 0.3, "2": 0.3, "3": 0.2},
                            "confidence": 0.5},
    }}


def fake_jev_judge() -> JevJudge:
    # Offline dry run: JevJudge._post requires the key env var to be SET even
    # with an injected transport, so an obviously fake value is set in-process.
    # No network call is ever made: the injected transport is the whole I/O
    # path and httpx is never imported.
    os.environ["SENTINEL_DRYRUN_JEV_KEY"] = "offline-dry-run-not-a-real-key"

    def transport(payload: dict) -> dict:
        return {"model": payload["model"],
                "answers": scripted_jev_answers(payload["state"])["answers"]}

    return JevJudge(api_key_env="SENTINEL_DRYRUN_JEV_KEY",
                    model="fake-jev-transport", transport=transport)


# --------------------------------------------------------------------------
# Post-processing: pos-* false-safe, needs_review recall, band routing.
# --------------------------------------------------------------------------

def summarize_log(log_path: Path, repeats: int) -> dict:
    """Per-case metrics from the FINAL repeat of a run log."""
    final: dict[str, dict] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("repeat", repeats - 1) == repeats - 1:
            final[rec["case_id"]] = rec

    pos_fs = pos_scored = 0
    review_hits = review_needed = review_false_escalation = 0
    bands = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for case_id, rec in final.items():
        ref = rec.get("reference", {})
        ans = rec.get("answers", {})
        hazard_label = ref.get("hazard_relevant")
        hazard_value = ans.get("hazard_relevant", {}).get("value")
        if case_id.startswith("pos-") and isinstance(hazard_label, bool) \
                and isinstance(hazard_value, bool):
            pos_scored += 1
            if hazard_label and hazard_value is False:
                pos_fs += 1
        review_label = ref.get("human_review_needed")
        review_value = ans.get("human_review_needed", {}).get("value")
        if isinstance(review_label, bool) and isinstance(review_value, bool):
            if review_label:
                review_needed += 1
                if review_value:
                    review_hits += 1
            elif review_value:
                review_false_escalation += 1
        for q in ("hazard_relevant", "human_review_needed"):
            a = ans.get(q)
            if not isinstance(a, dict):
                continue
            c = a.get("confidence")
            if not isinstance(c, (int, float)) or not 0.0 <= c <= 1.0:
                bands["unknown"] += 1
            elif c >= 0.8:
                bands["high"] += 1
            elif c >= 0.5:
                bands["medium"] += 1
            else:
                bands["low"] += 1

    return {
        "pos_clean_false_safe": {"false_safe": pos_fs, "scored": pos_scored},
        "needs_review_recall": {
            "hits": review_hits, "needed": review_needed,
            "recall": round(review_hits / review_needed, 3) if review_needed else None,
            "false_escalation_on_negatives": review_false_escalation,
        },
        "self_confidence_bands_noul": bands,
    }


def main() -> int:
    cases = load_cases()
    ART.mkdir(parents=True, exist_ok=True)

    arms: dict[str, dict] = {}
    baselines: dict[str, dict] = {}

    arm_specs = [
        ("static_v2", StaticJudge(booleans={"hazard_relevant": (True, 0.9)}), REPEATS),
        ("jev_fake_transport", fake_jev_judge(), REPEATS),
        ("baseline-always_safe", AlwaysSafeJudge(), 1),
        ("baseline-always_escalate", AlwaysEscalateJudge(), 1),
        ("baseline-static_rules", StaticRulesJudge(), 1),
    ]
    for name, judge, repeats in arm_specs:
        log_path = ART / f"v2-dryrun-{name}.jsonl"
        if log_path.exists():
            log_path.unlink()
        metrics, _ = run_cases(cases, judge, log_path=log_path, repeats=repeats)
        arms[name] = {"metrics": metrics, "repeats": repeats}
        if name.startswith("baseline-"):
            baselines[name.removeprefix("baseline-")] = metrics

    escalate = baselines["always_escalate"]
    for name, arm in arms.items():
        arm["metrics"]["pos_and_review"] = summarize_log(
            ART / f"v2-dryrun-{name}.jsonl", arm["repeats"])
        if name != "baseline-always_escalate":
            arm["metrics"]["decision_rule_vs_always_escalate"] = \
                decision_rule(arm["metrics"], baselines)
        else:
            arm["metrics"]["decision_rule_vs_always_escalate"] = None

    summary = {
        "title": "Jev v2 offline dry run (J4) -- static/baselines + fake Jev transport",
        "fixture": "fixtures/judgment/cases.v2.jsonl",
        "label_source": "approved v2 case-level hazard/needs_review labels projected "
                        "into run_cases reference shape; embedded v1 reference kept "
                        "for traceability only",
        "repeats": REPEATS,
        "network": "none (fake Jev transport injected into JevJudge; no live provider call)",
        "arms": {name: {"metrics": a["metrics"], "repeats": a["repeats"]}
                 for name, a in arms.items()},
        "known_flake": "tests/test_env_file.py::test_parses_comments_and_quoted_values -- "
                       "pre-existing flake, tracked by WP1 card t_45e8629b; not touched here.",
    }
    (ART / "v2-dryrun-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    lines = ["# v2 dry run summary (offline, no network)", ""]
    lines.append(f"Fixture: {summary['fixture']}; repeats={REPEATS} "
                 "(baselines deterministic, repeats=1).")
    lines.append("Fake Jev transport: scripted answers injected via JevJudge's transport "
                 "hook; no socket, no key, no provider call.")
    lines.append("Label source: approved v2 case-level hazard/needs_review labels; "
                 "hazard-null (amb-*) cases are skipped for hazard and scored for review.")
    lines.append("")
    lines.append("| arm | hazard FS/FE/scored | review FS/FE/scored | pos-* false-safe | "
                 "review recall | stability flips | ranks vs always-escalate |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, a in arms.items():
        m = a["metrics"]
        h, r = m["hazard"], m["review"]
        pr = m["pos_and_review"]
        rr = pr["needs_review_recall"]["recall"]
        dr = m.get("decision_rule_vs_always_escalate")
        ranks = "--" if dr is None else ("YES" if dr["ranks_fixture"] else "no")
        stab = m["stability"]
        lines.append(
            f"| {name} | {h['false_safe']}/{h['false_escalation']}/{h['scored']} "
            f"| {r['false_safe']}/{r['false_escalation']}/{r['scored']} "
            f"| {pr['pos_clean_false_safe']['false_safe']}/{pr['pos_clean_false_safe']['scored']} "
            f"| {rr if rr is not None else 'n/a'} "
            f"| {stab['flipped']}/{stab['cases']} (count {stab['flip_count']}) | {ranks} |")
    lines.append("")
    lines.append("Notes:")
    lines.append("- static_v2 answers True@0.9 to every hazard question: its 0 false-safe "
                 "is by construction (constant), and every reference-negative hazard case "
                 "is a false-escalation. The v1 run's 'static 0 false-safe' claim cannot "
                 "support go/no-go.")
    lines.append("- always_escalate: 0 false-safe by construction, so any judge only ranks "
                 "if it beats it on false-escalation at equal-or-better false-safe "
                 "(decision_rule).")
    lines.append("- Jev noul wire answers carry no self-confidence field, so noul routing "
                 "bands report 'unknown'; the plain 0.5 threshold carries the routing "
                 "signal. choice/score answers carry confidence and band normally.")
    lines.append("- Known flake: tests/test_env_file.py::test_parses_comments_and_quoted_values "
                 "(tracked by WP1 card t_45e8629b, out of scope here).")
    (ART / "v2-dryrun-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
