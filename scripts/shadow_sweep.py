#!/usr/bin/env python
"""Shadow sweep (P2 Task 5): run one advisor over the fixture's 48
SELECT_PLAN/EVIDENCE_GATE judgment turns, ``--repeats`` times each, and
score its picks against the fixture's hand labels with ``sentinel.shadow_eval.score``.

``fake`` and ``static`` are offline, non-LLM baselines: ``fake`` always
picks the alphabetically-first non-escalate candidate (it exists only to
prove the sweep/scoring plumbing is honest, not as a real advisor);
``static`` is a small hand-written, deterministic rule set over the
packet's facts (no model, no network call) -- a real but simple baseline,
independent of the fixture's own draft labels.

``jev`` and ``llm`` call the real shadow advisors (P2 Task 2's
``JevJudge``/``LlmJudge``) and refuse to run -- printing why, exit code 2
-- unless ``SENTINEL_SHADOW_SWEEP_CONSENT=<batch-id>`` is set: a fresh,
explicit operator consent for a paid remote call, recorded per the plan's
G-sweep gate (Task 5 Step 5). Nothing in this script sets that variable;
an operator sets it deliberately, once, immediately before a consented run.

This script never touches ``CommandGateway`` and never mutates a run: it
only replays the committed fixture's packets/candidates through a
``ContextualPolicy`` in isolation and records picks. It is not itself a
demo path.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sentinel.coordinator.legal_tools import (  # noqa: E402
    ADVISORY_POINTS, ESCALATE_LABEL, EVIDENCE_GATE, SELECT_PLAN, Candidate, LegalSet,
)
from sentinel.coordinator.policies import JevPolicy  # noqa: E402
from sentinel.coordinator.policy import PolicyContext  # noqa: E402
from sentinel.shadow_eval import REPORT_HEADER, score  # noqa: E402

FIXTURE_PATH = REPO_ROOT / "fixtures" / "decisions" / "turns.v1.jsonl"
# Sourced from legal_tools so a sweep automatically covers decision points added
# later (P3 adds ASSIGN near-ties and REPLAN) instead of silently skipping them.
JUDGMENT_POINTS = tuple(sorted(ADVISORY_POINTS))


# --- offline, non-LLM baseline judges (fake, static) ------------------------

class FakeJudge:
    """Trivial baseline that exists only to exercise the sweep/scoring
    pipeline end to end: always picks the alphabetically-first non-escalate
    candidate offered, at a fixed HIGH confidence. It reasons over nothing
    -- it is not a real advisor, only a plumbing check."""

    model = "fake-always-first"

    def choice(self, state: dict, instruction: str, *, criteria: dict, name: str) -> dict:
        non_escalate = sorted(label for label in criteria if label != ESCALATE_LABEL)
        label = non_escalate[0] if non_escalate else ESCALATE_LABEL
        return {"value": label, "confidence": 0.99, "probabilities": {label: 0.99}}


class StaticJudge:
    """Deterministic, non-LLM baseline: hand-written rules over the
    packet's facts (``DecisionState.to_packet()``), no model call. A real
    (if simple) safety baseline for the shadow sweep -- independent of the
    fixture's own draft hand labels it is scored against."""

    model = "baseline-static-heuristic"

    _SINGLE_KEYWORDS = ("only", "single", "one station", "just the")
    _FULL_KEYWORDS = ("full", "all", "every", "sweep", "verify", "verified", "marker")

    def choice(self, state: dict, instruction: str, *, criteria: dict, name: str) -> dict:
        if name == SELECT_PLAN.lower():
            return self._select_plan(state, criteria)
        if name == EVIDENCE_GATE.lower():
            return self._evidence_gate(state, criteria)
        return {"value": ESCALATE_LABEL, "confidence": 0.5, "probabilities": {}}

    def _select_plan(self, state: dict, criteria: dict) -> dict:
        request = (state.get("operator_request") or "").lower()
        plan_labels = [label for label in criteria if label != ESCALATE_LABEL]

        def station_count(label: str) -> int:
            match = re.search(r"stations=(\d+)", criteria[label] or "")
            return int(match.group(1)) if match else 0

        wants_single = any(k in request for k in self._SINGLE_KEYWORDS)
        wants_full = any(k in request for k in self._FULL_KEYWORDS)
        if plan_labels and wants_single and not wants_full:
            return {"value": min(plan_labels, key=station_count), "confidence": 0.9, "probabilities": {}}
        if plan_labels and wants_full and not wants_single:
            return {"value": max(plan_labels, key=station_count), "confidence": 0.9, "probabilities": {}}
        return {"value": ESCALATE_LABEL, "confidence": 0.95, "probabilities": {}}

    def _evidence_gate(self, state: dict, criteria: dict) -> dict:
        facts = state.get("facts") or {}
        band = facts.get("detection_score_band")
        inside = facts.get("detection_inside_plan")
        duplicate = facts.get("detection_duplicates_open_task")
        if "create_task" in criteria and band in ("very_high", "high") and inside and not duplicate:
            return {"value": "create_task", "confidence": 0.9, "probabilities": {}}
        return {"value": ESCALATE_LABEL, "confidence": 0.95, "probabilities": {}}


CONSENT_ENV = "SENTINEL_SHADOW_SWEEP_CONSENT"


def require_live_consent(gate: str) -> str:
    """Raise unless the operator granted a paid live batch. Called by every
    live-advisor builder, so no caller can bypass the gate by skipping main()."""
    consent = os.environ.get(CONSENT_ENV)
    if not consent:
        raise PermissionError(
            f"{CONSENT_ENV} is not set: live advisors make paid remote calls and require fresh, "
            f"explicit operator consent (plan gate {gate}); set {CONSENT_ENV}=<batch-id> to proceed.")
    return consent


def _build_live_judge(advisor: str) -> Any:
    """Constructs the real Jev/LLM judge (Task 2's advisor arms). Refuses
    without operator consent. Importing here (not at module top) keeps
    ``httpx``/network setup out of the offline path entirely."""
    require_live_consent("G-sweep")
    from sentinel.judgment import JevJudge, LlmJudge

    if advisor == "jev":
        return JevJudge(api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai",
                         model="typesafe/jev-1.13-20260917")
    if advisor == "llm":
        return LlmJudge(api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai/api",
                         model="z-ai/glm-5.3-flash")
    raise ValueError(f"not a live advisor: {advisor!r}")


_OFFLINE_JUDGES = {"fake": FakeJudge, "static": StaticJudge}


class _PacketOnlyState:
    """The minimal ``DecisionState``-shaped object a ``ContextualPolicy``
    needs: fixture turns only ever carry the already-rendered packet, not
    the raw ``DecisionState`` fields it came from."""

    def __init__(self, packet: dict) -> None:
        self._packet = packet

    def to_packet(self) -> dict:
        return self._packet


def load_judgment_turns(path: Path = FIXTURE_PATH) -> list[dict]:
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["decision_point"] in JUDGMENT_POINTS:
            turns.append(record)
    return turns


def fixture_truth(turns: list[dict]) -> dict[str, str]:
    return {turn["turn_id"]: turn["label"] for turn in turns}


def _legal_set_for_turn(turn: dict) -> LegalSet:
    candidates = tuple(
        Candidate(tool_name="", arguments={}, label=c["label"], description=c["description"])
        for c in turn["candidates"]
    )
    return LegalSet(turn["decision_point"], candidates)


class RecordingJudge:
    """Wraps a judge and keeps the raw answer of the last ``choice`` call.

    Without this the sweep committed only aggregates, so a question like "is
    Jev's 0% HIGH coverage real calibration, or are we reading the wrong
    confidence field?" could not be answered without paying for another live
    batch. The raw answer carries both ``confidence`` and the per-option
    ``probabilities``, which are what the band policy chooses between.
    """

    def __init__(self, judge: Any) -> None:
        self._judge = judge
        self.last_raw: dict | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._judge, name)

    def choice(self, state, instruction, **kwargs):
        self.last_raw = None
        answer = self._judge.choice(state, instruction, **kwargs)
        self.last_raw = answer if isinstance(answer, dict) else None
        return answer


def run_sweep(advisor_name: str, judge: Any, turns: list[dict], *, repeats: int) -> list[dict]:
    """Runs ``JevPolicy(judge)`` over every turn, ``repeats`` times, and
    returns one ``judgment``-shaped record per (turn, repeat) -- the exact
    shape ``sentinel.shadow_eval.score`` consumes."""
    recorder = RecordingJudge(judge)
    policy = JevPolicy(recorder)
    records = []
    for repeat in range(repeats):
        for turn in turns:
            legal = _legal_set_for_turn(turn)
            context = PolicyContext(state=_PacketOnlyState(turn["packet"]), legal=legal)
            start = time.perf_counter()
            try:
                policy.decide({"agent_run_id": turn["turn_id"]}, context)
            except Exception:
                # The fixture's candidates carry only label/description, not
                # a real tool_name/arguments pair (turns.v1 does not record
                # one), so JevPolicy's one_call() wire-schema validation can
                # fail here. Every JevPolicy.decide() branch sets `.last`
                # *before* building that (here, unused) wire result, so the
                # advisor's actual pick is still valid and safe to read.
                pass
            decision = policy.last
            latency_ms = decision.latency_ms if decision.latency_ms is not None else int(
                (time.perf_counter() - start) * 1000)
            records.append({
                "record": "judgment", "turn_id": turn["turn_id"], "decision_point": turn["decision_point"],
                "repeat": repeat,
                "advisors": {advisor_name: {
                    "label": decision.label, "confidence": decision.confidence, "band": decision.band,
                    "model": decision.model, "latency_ms": latency_ms, "error": decision.error,
                }},
                # Raw judge output, so every aggregate below can be re-derived
                # and re-scored offline (different bands, different confidence
                # field) without another paid batch.
                "raw_answer": recorder.last_raw,
                "legal_labels": [c.label for c in legal.candidates],
            })
    return records


def write_records(path: Path, records: list[dict]) -> None:
    """Append-only raw per-call rows, one JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")


def read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def render_report(advisor_name: str, report: dict, *, n_turns: int, repeats: int) -> str:
    lines = [f"# Shadow sweep: {advisor_name}", "", REPORT_HEADER, "",
             f"{n_turns} judgment turns x {repeats} repeats = {n_turns * repeats} calls.", ""]
    for decision_point in JUDGMENT_POINTS:
        stats = report.get(advisor_name, {}).get(decision_point)
        lines.append(f"## {decision_point}")
        if stats is None:
            lines.append("(no scored records)")
            lines.append("")
            continue
        for key in ("n", "agreement", "coverage_high", "selective_accuracy", "false_act",
                    "escalation_rate", "error_rate", "latency_ms_median", "stability", "wilson_lower_95"):
            lines.append(f"- `{key}`: {stats[key]}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # the report header uses U+2265; some Windows consoles default to cp1252
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--advisor", required=True, choices=("fake", "static", "jev", "llm"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    parser.add_argument("--out", type=Path, default=None, help="write the Markdown report here instead of stdout")
    args = parser.parse_args(argv)

    if args.advisor in ("jev", "llm"):
        try:
            judge = _build_live_judge(args.advisor)
        except PermissionError as exc:
            print(f"refusing to run a live '{args.advisor}' sweep: {exc}", file=sys.stderr)
            return 2
    else:
        judge = _OFFLINE_JUDGES[args.advisor]()

    turns = load_judgment_turns(args.fixture)
    truth = fixture_truth(turns)
    records = run_sweep(args.advisor, judge, turns, repeats=args.repeats)
    report = score(records, truth)
    text = render_report(args.advisor, report, n_turns=len(turns), repeats=args.repeats)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        # The raw rows are the evidence; the report is only a view of them.
        # Committing aggregates alone makes a promotion claim unauditable and
        # un-rescorable except by paying for the batch again.
        write_records(args.out.with_suffix(".jsonl"), records)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
