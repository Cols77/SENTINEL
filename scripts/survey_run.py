#!/usr/bin/env python
"""Survey CLI (Task 10): one replay-driven survey run in mode F, E or J.

Follows ``scripts/rehearsal_run.py``'s structure (argparse, a ``REPO_ROOT``
``sys.path`` bootstrap, ``main(argv) -> int`` printing a JSON summary) but
is otherwise self-contained: this script's only survey-package dependency
is the public surface Tasks 1-9 already produced, and its only reused
alley-side import is ``coordinator.shadow.JsonlAuditSink`` (Global
Constraints: "Reused read-only from the alley code" -- an append-only sink,
never a decision).

Consent (rev 3 R5, Task 8): ``--retain-person-frames`` resolves filming
consent *before anything else is built* -- before the envelope, the
policy, the flight or the detector -- so a refusal (``ConsentMissing``)
happens before anything moves. This is a stricter, script-level check than
``run_survey``'s own ``location``/``consent_date`` gate (which returns a
soft ``CONSENT_REQUIRED`` outcome instead of raising): a hermetic replay
over frames already consented for (design spec 13.4) passes no
``--retain-person-frames`` flag at all and is unaffected by this check,
exactly as ``run_survey`` itself treats a ``None`` location.

Modes E and J (Task 12's provider adapters are a separate, still-closed
gate) fall back to small offline, no-network stand-ins
(``_DefaultJudge``/``_DefaultAdapter``) when no live judge or adapter is
supplied, so every mode stays replayable without a paid call -- consistent
with this plan's "no paid API call" constraint. A caller that does have a
real judge or adapter (e.g. Task 13's later live comparison, gated
separately) passes it to ``build_policy`` directly; this script's own CLI
surface has no ``--judge``/``--adapter`` flag, matching the plan's Task 10
flag list.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT / "src", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sentinel.coordinator.shadow import JsonlAuditSink  # noqa: E402 -- reused read-only (Global Constraints)

from sentinel.survey.candidates import ESCALATE_LABEL  # noqa: E402
from sentinel.survey.consent import consent_policy  # noqa: E402
from sentinel.survey.context import SurveyPolicy  # noqa: E402
from sentinel.survey.detector import ReplayDetector  # noqa: E402
from sentinel.survey.envelope import SweepEnvelope  # noqa: E402
from sentinel.survey.eval import load_scene, score_episode  # noqa: E402
from sentinel.survey.flight import ReplayFlight  # noqa: E402
from sentinel.survey.loop import run_survey  # noqa: E402
from sentinel.survey.options import generate_options  # noqa: E402
from sentinel.survey.proposer import JevProposer, LlmProposer, RuleProposer  # noqa: E402
from sentinel.survey.store import SurveyStore  # noqa: E402

#: The same fixed envelope used across the survey test suite (tests/survey/
#: test_loop.py, test_eval.py). ``survey.cube_bridge`` (Task 11, blocked on
#: the WP5 merge) is the only place a real site's envelope is derived
#: instead of this fixed replay stand-in.
DEFAULT_ENVELOPE = SweepEnvelope(-150, 150, 0, 100, 80, 180, 300)

_PLURAL = {"computer": "computers", "person": "people", "phone": "phones", "pen": "pens"}


class _DefaultJudge:
    """Offline, no-network stand-in for ``--mode J`` when no live judge is
    wired in (Task 12's provider gate is separate and still closed).
    Deterministic -- always the alphabetically-first candidate, at high
    confidence -- so a replay run is repeatable."""

    def choice(self, state, instruction, *, criteria, name):
        label = sorted(criteria)[0]
        return {"value": label, "confidence": 0.9,
                "probabilities": {label: 0.9,
                                  **{k: 0.02 for k in criteria if k != label}}}


class _DefaultAdapter:
    """Offline, no-network stand-in for ``--mode E``, same rationale as
    ``_DefaultJudge``: always the first legal candidate."""

    def propose(self, request, context):
        actionable = [c for c in context.legal.candidates if c.label != ESCALATE_LABEL]
        if not actionable:
            return {"status": "TOOL_CALLS", "tool_calls": [
                {"tool_name": "survey.escalate",
                 "arguments": {"reason": "no actionable candidate"}}]}
        candidate = actionable[0]
        if candidate.tool_name == "survey.submit_report":
            return {"status": "TOOL_CALLS",
                    "tool_calls": [{"tool_name": "survey.submit_report", "arguments": {}}]}
        return {"status": "TOOL_CALLS", "tool_calls": [
            {"tool_name": "survey.propose_sweep",
             "arguments": {"option_id": candidate.arguments["option_id"],
                           "reason": "default offline stand-in: first legal sweep"}}]}


def build_policy(mode: str, *, judge=None, adapter=None, sink) -> SurveyPolicy:
    if mode == "F":
        return RuleProposer()
    if mode == "J":
        return JevProposer(judge if judge is not None else _DefaultJudge(), sink)
    if mode == "E":
        return LlmProposer(adapter if adapter is not None else _DefaultAdapter(), sink)
    raise ValueError(f"unknown --mode: {mode!r}")


def _default_writer(results: dict) -> str:
    parts = [f"{entry['confirmed']} {_PLURAL[entry['class']]}" for entry in results["class_counts"]
             if entry["method"] != "NOT_OBSERVABLE"]
    if not parts:
        return "No observable classes were confirmed this run."
    return "I found " + ", ".join(parts) + "."


def _make_on_approval(auto_approve: bool):
    if auto_approve:
        return lambda call: True

    def _prompt(call: dict) -> bool:
        # A real flight always prompts -- --auto-approve is for tests and
        # replay only (Task 10's own flag description).
        answer = input(f"Approve: {call.get('description', '')}\n[y/N] ")
        return answer.strip().lower() in ("y", "yes")

    return _prompt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("F", "E", "J"), default="F")
    parser.add_argument("--scene", type=Path, default=None,
                        help="a Scene json (Task 9); when given, the summary also carries "
                             "a score_episode result")
    parser.add_argument("--frames", type=Path, required=True,
                        help="a ReplayFlight frames dir (frames.json manifest)")
    parser.add_argument("--responses", type=Path, required=True,
                        help="a ReplayDetector responses dir (one json per frame)")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--request", default="count the computers")
    parser.add_argument("--location", default=None)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--retain-person-frames", action="store_true",
                        help="requires --location and --date, and refuses to start "
                             "(ConsentMissing) unless filming consent is already recorded")
    parser.add_argument("--auto-approve", action="store_true",
                        help="tests and replay only -- a real flight always prompts")
    args = parser.parse_args(argv)

    store = SurveyStore.open(args.db)

    # Resolves consent BEFORE anything else is built -- the envelope, the
    # policy, the flight, the detector -- so a refusal happens before
    # anything moves (rev 3 R5).
    if args.retain_person_frames:
        if not args.location or not args.date:
            raise ValueError("--retain-person-frames requires --location and --date")
        consent_policy(store, args.location, args.date)

    options = generate_options(DEFAULT_ENVELOPE)
    sink = JsonlAuditSink(args.db.with_suffix(".jsonl"))
    policy = build_policy(args.mode, sink=sink)
    flight = ReplayFlight(args.frames)
    detector = ReplayDetector(args.responses)

    run_id = str(uuid.uuid4())
    outcome = run_survey(store=store, run_id=run_id, request_text=args.request,
                         options=options, policy=policy, flight=flight, detector=detector,
                         on_approval=_make_on_approval(args.auto_approve),
                         writer=_default_writer,
                         location=args.location, consent_date=args.date)

    summary: dict[str, Any] = {"run_id": run_id, "mode": args.mode, "outcome": outcome}
    if args.scene is not None:
        scene = load_scene(args.scene)
        summary["score"] = score_episode(scene, outcome, store, run_id)

    print(json.dumps(summary, indent=2, default=str))
    return 0 if outcome["status"] == "REPORTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
