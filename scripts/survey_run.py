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

Modes E and J fall back to small offline, no-network stand-ins
(``_DefaultJudge``/``_DefaultAdapter``) when no live judge or adapter is
supplied, so every mode stays replayable without a paid call -- consistent
with this plan's "no paid API call" constraint. A caller that does have a
real judge or adapter (e.g. Task 13's later live comparison, gated
separately) passes it to ``build_policy`` directly; this script's own CLI
surface has no ``--judge``/``--adapter`` flag, matching the plan's Task 10
flag list.

``--detector``/``--fallback`` (plan section C4) select the *perception*
detector, a separate axis from ``--mode``'s proposer selection above:
``replay`` (default, the ``ReplayDetector`` this script always used) or a
live ``moondream``/``gemini`` provider adapter (Task 12 / plan section C).
A live provider detector refuses to start (a plain, non-zero-exit error --
see ``build_provider_detector``) unless an API key is present in the
environment (``src/sentinel/env_file.py`` / ``os.environ``) *and* filming
consent is recorded for ``--location``/``--date`` -- the same consent
rule ``--retain-person-frames`` already enforces above, now widened to
cover every path that can send a frame to a third party.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT / "src", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sentinel.coordinator.shadow import JsonlAuditSink  # noqa: E402 -- reused read-only (Global Constraints)

from sentinel.cube.site import load_site  # noqa: E402
from sentinel.dry_run import DryRunClient  # noqa: E402
from sentinel.pose.persistence import ensure_schema  # noqa: E402
from sentinel.pose.tagmap import load_tag_map  # noqa: E402
from sentinel.survey.candidates import ESCALATE_LABEL  # noqa: E402
from sentinel.survey.consent import consent_policy  # noqa: E402
from sentinel.survey.context import SurveyPolicy  # noqa: E402
from sentinel.survey.cube_bridge import CubeFlight, envelope_from_site  # noqa: E402
from sentinel.survey.detector import FallbackDetector, ReplayDetector  # noqa: E402
from sentinel.survey.envelope import SweepEnvelope  # noqa: E402
from sentinel.survey.eval import load_scene, score_episode  # noqa: E402
from sentinel.survey.flight import ReplayFlight  # noqa: E402
from sentinel.survey.loop import run_survey  # noqa: E402
from sentinel.survey.observability import CAMERAS, SYNTHETIC_LEVEL, TELLO_MEASURED  # noqa: E402
from sentinel.survey.options import generate_options  # noqa: E402
from sentinel.survey.proposer import JevProposer, LlmProposer, RuleProposer  # noqa: E402
from sentinel.survey.providers.gemini import GeminiDetector  # noqa: E402
from sentinel.survey.providers.moondream import MoondreamDetector  # noqa: E402
from sentinel.survey.store import SurveyStore  # noqa: E402

#: name -> (API key env var, model env var, adapter class). Plan section C4.
_PROVIDER_ENV = {
    "moondream": ("MOONDREAM_API_KEY", "MOONDREAM_MODEL", MoondreamDetector),
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", GeminiDetector),
}

#: The same fixed envelope used across the survey test suite (tests/survey/
#: test_loop.py, test_eval.py). ``--site`` (Task 11) is the only place a
#: real site's envelope is derived instead of this fixed replay stand-in.
DEFAULT_ENVELOPE = SweepEnvelope(-150, 150, 0, 100, 80, 180, 300)

#: Distance from the rail to what it looks at (survey.envelope.SweepEnvelope
#: .standoff_cm) -- matches DEFAULT_ENVELOPE's own value; a Site file itself
#: carries no standoff (it is a survey concept, not a WP5 one).
DEFAULT_STANDOFF_CM = 300

_PLURAL = {"computer": "computers", "person": "people", "phone": "phones", "pen": "pens"}


class _DefaultJudge:
    """Offline, no-network stand-in for ``--mode J`` when no live judge is
    wired in (Task 12's provider gate is separate and still closed).
    Deterministic -- always the first candidate offered, the same one
    ``rules`` takes, at high confidence -- so a replay run is repeatable.
    (It used to take the alphabetically first label, which is the two-stop
    close look: a first sweep that cannot confirm anything.)"""

    def choice(self, state, instruction, *, criteria, name):
        label = next(iter(criteria))
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


#: Who picks the sweep. The letters are the project's architecture names (F, E, J in
#: the coordinator's specs and in every audit record) and stay valid as aliases; the
#: words say what each one does.
#:   rules (F) - code picks: the first legal sweep, no model involved.
#:   judge (J) - a model picks ONE of the offered sweeps; code checks its confidence
#:               and margin and writes the justification.
#:   agent (E) - a model writes the tool call itself, with its own reason; code
#:               validates that the call is legal.
MODE_NAMES = {"rules": "F", "judge": "J", "agent": "E"}
MODE_WORDS = {letter: word for word, letter in MODE_NAMES.items()}


def _mode_letter(value: str) -> str:
    return MODE_NAMES.get(value, value)


LIVE_JUDGE_BASE_URL = "https://openrouter.ai/api"
LIVE_JUDGE_KEY_ENV = "OPENROUTER_API_KEY"


def _require_paid_go_ahead(args, flag: str) -> None:
    if not args.i_authorise_paid_calls:
        raise ValueError(f"{flag} makes paid calls (at most 3 per run); "
                         "pass --i-authorise-paid-calls to go ahead")
    from sentinel.env_file import load_env_file
    load_env_file()
    if not os.environ.get(LIVE_JUDGE_KEY_ENV):
        raise ValueError(f"{flag} needs {LIVE_JUDGE_KEY_ENV} in the environment or .env")


def _live_agent(args):
    """None (the offline stand-in) unless the operator asked for the live agent:
    a model writes the tool call and its reason (survey.llm_adapter). Approved by
    the operator 2026-09-22; every run's paid calls still need the flag."""
    if not args.live_agent:
        return None
    if args.mode != "E":
        raise ValueError("--live-agent only applies to --mode agent")
    _require_paid_go_ahead(args, "--live-agent")
    from sentinel.survey.llm_adapter import LlmAdapter
    return LlmAdapter(api_key_env=LIVE_JUDGE_KEY_ENV, base_url=LIVE_JUDGE_BASE_URL,
                      model=args.llm_model)


def _live_judge(args):
    """None (the offline default judge) unless the operator asked for a live one.

    Mode J's proposer takes a typed choice among the offered sweeps and gates it on
    confidence and margin; the justification is written by code. Mode E's live path
    is ``_live_agent``."""
    if args.live_judge == "none":
        return None
    if args.mode != "J":
        raise ValueError("--live-judge only applies to --mode judge")
    _require_paid_go_ahead(args, "--live-judge")
    from sentinel.judgment import JevJudge, LlmJudge
    if args.live_judge == "jev":
        return JevJudge(api_key_env=LIVE_JUDGE_KEY_ENV, base_url=LIVE_JUDGE_BASE_URL,
                        timeout_s=20.0)
    return LlmJudge(api_key_env=LIVE_JUDGE_KEY_ENV, base_url=LIVE_JUDGE_BASE_URL,
                    model=args.llm_model, timeout_s=120.0)


def _start_pad(args, envelope) -> tuple[int | None, int | None]:
    """Where the drone takes off. ``--start X,Y`` is a tape-measured pad. Without it a
    real flight with pose on measures its start from the tags; a real flight with pose
    off is refused, because dead reckoning from a guessed pad misplaces every stop and
    every photograph. Only a dry run may fall back to a nominal pad."""
    if args.start is not None:
        x_text, _, y_text = args.start.partition(",")
        return int(x_text), int(y_text)
    if args.real:
        if args.pose == "off":
            raise ValueError("--real with --pose off needs a tape-measured --start X,Y")
        return None, None
    return (envelope.min_x_cm + envelope.max_x_cm) // 2, envelope.min_y_cm


def build_policy(mode: str, *, judge=None, adapter=None, sink) -> SurveyPolicy:
    if mode == "F":
        return RuleProposer()
    if mode == "J":
        return JevProposer(judge if judge is not None else _DefaultJudge(), sink)
    if mode == "E":
        return LlmProposer(adapter if adapter is not None else _DefaultAdapter(), sink)
    raise ValueError(f"unknown --mode: {mode!r}")


def _default_writer(results: dict) -> str:
    parts = []
    for entry in results["class_counts"]:
        noun = _PLURAL[entry["class"]]
        if entry["method"] == "NOT_OBSERVABLE":
            continue
        if entry["method"] == "SINGLE_VIEW_LOWER_BOUND":
            parts.append(f"at least {entry['uncertain']} {noun}")
        elif entry["uncertain"]:
            parts.append(f"{entry['confirmed']} {noun} confirmed and up to "
                         f"{entry['confirmed'] + entry['uncertain']} {noun} counting the ones "
                         "I could not confirm")
        else:
            parts.append(f"{entry['confirmed']} {noun}")
    if not parts:
        return "No observable classes were confirmed this run."
    return "I found " + "; ".join(parts) + "."


def _real_tello_client(args):
    """Thin constructor for ``--real`` (hard rule for this phase: no drone
    connection, so this path is untested here). Mirrors the public
    ``DjitellopyClient`` shape used elsewhere in this codebase for the
    real backend -- not the adapter auto-selection wired around it
    (``--local-ip`` is passed explicitly instead)."""
    from sentinel.djitellopy_backend import DjitellopyClient
    return DjitellopyClient(local_ip=args.local_ip, tello_ip=args.tello_ip)


class _HttpxPostSession:
    """Adapts ``httpx`` (this repo's HTTP dependency -- see pyproject.toml)
    to the ``post(url, *, headers, json, timeout)`` duck type
    ``providers.post_json`` expects, translating the adapters' ``(connect,
    read, total)`` timeout tuple into httpx's own ``Timeout`` object. The
    real call site: only reached when the operator passes ``--detector`` or
    ``--fallback`` as ``moondream``/``gemini`` -- never during any test,
    which always injects a fake session instead."""

    def post(self, url, *, headers, json, timeout):
        import httpx
        connect_s, read_s, total_s = timeout
        return httpx.post(url, headers=headers, json=json,
                          timeout=httpx.Timeout(total_s, connect=connect_s, read=read_s))


def build_provider_detector(name: str, *, environ: Mapping[str, str] | None = None, session=None):
    """Builds a live ``MoondreamDetector``/``GeminiDetector`` from the
    environment (``src/sentinel/env_file.py`` populates ``os.environ`` from
    the nearest ``.env`` first, same pattern as
    ``sentinel.adapter.llm.config.load_config``). Raises a plain, readable
    ``RuntimeError`` -- not a stack trace from a missing-key ``KeyError`` --
    when the API key or model id is absent, so ``main`` exits non-zero with
    a message that names exactly what to set."""
    key_var, model_var, cls = _PROVIDER_ENV[name]
    if environ is None:
        from sentinel.env_file import load_env_file
        load_env_file()
        source = os.environ
    else:
        source = environ
    api_key = source.get(key_var)
    if not api_key:
        raise RuntimeError(f"--detector/--fallback {name} requires {key_var} to be set "
                           f"(see src/sentinel/env_file.py or export it directly)")
    model = source.get(model_var)
    if not model:
        raise RuntimeError(f"--detector/--fallback {name} requires {model_var} to be set")
    return cls(api_key=api_key, model=model, session=session or _HttpxPostSession())


def build_detector(kind: str, responses_dir, *, environ: Mapping[str, str] | None = None,
                   session=None):
    if kind == "replay":
        return ReplayDetector(responses_dir)
    return build_provider_detector(kind, environ=environ, session=session)


def _make_on_approval(auto_approve: bool):
    if auto_approve:
        return lambda call: True

    def _prompt(call: dict) -> bool:
        # A real flight always prompts -- --auto-approve is for tests and
        # replay only (Task 10's own flag description).
        answer = input(f"Approve: {call.get('description', '')}\n[y/N] ")
        return answer.strip().lower() in ("y", "yes")

    return _prompt


def main(argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None,
        session=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=(*MODE_NAMES, "F", "J", "E"), default="rules",
                        help="who picks the sweep: rules (code; alias F), judge (a model picks "
                             "one of the offered sweeps, code gates it; alias J), agent (a "
                             "model writes the call and its reason; alias E)")
    parser.add_argument("--scene", type=Path, default=None,
                        help="a Scene json (Task 9); when given, the summary also carries "
                             "a score_episode result")
    parser.add_argument("--frames", type=Path, default=None,
                        help="a ReplayFlight frames dir (frames.json manifest); exactly one "
                             "of --frames or --site is required")
    parser.add_argument("--site", type=Path, default=None,
                        help="a cube-site-v1 json (WP5, Task 11); flies a real (or dry-run) "
                             "cube mission instead of replaying --frames")
    parser.add_argument("--pose", choices=("off", "passive", "reanchor"), default="off",
                        help="cube pose mode -- only meaningful with --site")
    parser.add_argument("--sr063-amended", action="store_true",
                        help="assert that SR-063 has been amended to permit re-anchoring "
                             "(required for --pose reanchor)")
    parser.add_argument("--surface-z-cm", type=float, default=None,
                        help="tape-measured surface height, for the per-frame delta_z_cm "
                             "(V12); required with --site")
    parser.add_argument("--live-judge", choices=("none", "jev", "llm"), default="none",
                        help="--mode J only: a live model reads the request and picks the "
                             "sweep. jev = the Jev decision model, llm = a general LLM through "
                             "the same typed-choice interface. PAID: needs OPENROUTER_API_KEY "
                             "and --i-authorise-paid-calls (at most 3 calls per run)")
    parser.add_argument("--live-agent", action="store_true",
                        help="--mode agent only: a live model writes the tool call and the "
                             "reason the operator reads at approval. PAID: needs "
                             "OPENROUTER_API_KEY and --i-authorise-paid-calls (at most 3 calls)")
    parser.add_argument("--llm-model", default="z-ai/glm-5.3",
                        help="model id for --live-judge llm and --live-agent")
    parser.add_argument("--i-authorise-paid-calls", action="store_true",
                        help="the operator's explicit go-ahead for --live-judge")
    parser.add_argument("--start", default=None, metavar="X,Y",
                        help="tape-measured takeoff pad in room cm. Omit it on a real flight with "
                             "pose on and the start is measured from the tags after takeoff")
    parser.add_argument("--real", action="store_true",
                        help="fly a real Tello instead of DryRunClient; --site only")
    parser.add_argument("--confirm", action="store_true",
                        help="passed through to CubeMissionRunner.run(); --site only")
    parser.add_argument("--tag-map", type=Path, default=Path("calibration/tag-map-calibrated.json"),
                        help="read only when --pose != off and --real")
    parser.add_argument("--pose-db", type=Path, default=Path("artifacts/pose.db"),
                        help="separate from --db, which is the survey's own store")
    parser.add_argument("--mission-log", type=Path, default=None,
                        help="default: <db>.cube-mission.jsonl")
    parser.add_argument("--capture-dir", type=Path, default=None,
                        help="default: <db's parent>/cube-frames")
    parser.add_argument("--local-ip", default=None, help="--real only")
    parser.add_argument("--tello-ip", default="192.168.10.1", help="--real only")
    parser.add_argument("--responses", type=Path, required=True,
                        help="a ReplayDetector responses dir (one json per frame); ignored "
                             "unless --detector replay")
    parser.add_argument("--detector", choices=("replay", "moondream", "gemini"), default="replay",
                        help="replay (default) never leaves the machine; moondream/gemini are "
                             "live providers gated on an API key and recorded filming consent")
    parser.add_argument("--fallback", choices=("none", "moondream", "gemini"), default="none",
                        help="tried only when --detector cannot answer (plan section C3); "
                             "gated the same way --detector is")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--request", default="count the computers")
    parser.add_argument("--location", default=None)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--retain-person-frames", action="store_true",
                        help="requires --location and --date, and refuses to start "
                             "(ConsentMissing) unless filming consent is already recorded")
    parser.add_argument("--camera", choices=sorted(CAMERAS), default=SYNTHETIC_LEVEL.name,
                        help="the model the frames were produced with: synthetic-level for "
                             "rendered scenes, tello-measured for anything the drone filmed "
                             "(coherence spec 10.5). The wrong one misplaces every object. "
                             "Ignored with --site, which always uses tello-measured (V12).")
    parser.add_argument("--auto-approve", action="store_true",
                        help="tests and replay only -- a real flight always prompts")
    args = parser.parse_args(argv)
    args.mode = _mode_letter(args.mode)

    if bool(args.site) == bool(args.frames):
        parser.error("exactly one of --frames or --site is required")
    if args.site and args.surface_z_cm is None:
        parser.error("--site requires --surface-z-cm")
    if args.pose == "reanchor" and not args.sr063_amended:
        parser.error("--pose reanchor requires --sr063-amended")

    store = SurveyStore.open(args.db)

    # Resolves consent BEFORE anything else is built -- the envelope, the
    # policy, the flight, the detector -- so a refusal happens before
    # anything moves (rev 3 R5). Widened by plan section C4: a live
    # provider detector sends the frame to a third party exactly as
    # --retain-person-frames does, so it is gated the same way -- a
    # strict superset of the original check, so every existing
    # --retain-person-frames-only test is unaffected.
    uses_live_provider = args.detector != "replay" or args.fallback != "none"
    if args.retain_person_frames or uses_live_provider:
        if not args.location or not args.date:
            raise ValueError("--location and --date are required when retaining person frames "
                             "or using a live provider detector")
        consent_policy(store, args.location, args.date)

    run_id = str(uuid.uuid4())

    if args.site:
        # V12: a CubeFlight always uses the measured camera -- there is no
        # flag to pass a different one; the adapter, not the caller, fixes
        # the model (CubeFlight itself also refuses anything else).
        site = load_site(args.site)
        envelope = envelope_from_site(site, standoff_cm=DEFAULT_STANDOFF_CM)
        camera = TELLO_MEASURED
        client = _real_tello_client(args) if args.real else DryRunClient()
        tag_map = load_tag_map(str(args.tag_map)) if args.pose != "off" and args.real else None
        conn = None
        if tag_map is not None:
            args.pose_db.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(args.pose_db)
            ensure_schema(conn)
        mission_log = args.mission_log or args.db.with_suffix(".cube-mission.jsonl")
        start = _start_pad(args, envelope)
        capture_dir = args.capture_dir or (args.db.resolve().parent / "cube-frames")
        flight = CubeFlight(
            client=client, site=site, mode="real" if args.real else "dry-run",
            pose_mode=args.pose, run_id=run_id, mission_log=mission_log,
            capture_dir=capture_dir,
            start_x_cm=start[0], start_y_cm=start[1],
            surface_z_cm=args.surface_z_cm, sr063_amended=args.sr063_amended,
            tag_map=tag_map, conn=conn,
            # A real flight always requires the explicit --confirm flag
            # (CubeMissionRunner.run() refuses without it regardless of
            # mode); a dry run needs no operator confirmation.
            confirm=args.confirm if args.real else True)
    else:
        camera = CAMERAS[args.camera]
        envelope = DEFAULT_ENVELOPE
        flight = ReplayFlight(args.frames)

    options = generate_options(envelope, camera)
    sink = JsonlAuditSink(args.db.with_suffix(".jsonl"))
    policy = build_policy(args.mode, sink=sink, judge=_live_judge(args), adapter=_live_agent(args))
    detector = build_detector(args.detector, args.responses, environ=environ, session=session)
    if args.fallback != "none":
        fallback_detector = build_provider_detector(args.fallback, environ=environ,
                                                     session=session)
        detector = FallbackDetector(detector, fallback_detector)

    outcome = run_survey(store=store, run_id=run_id, request_text=args.request,
                         options=options, policy=policy, flight=flight, detector=detector,
                         on_approval=_make_on_approval(args.auto_approve),
                         writer=_default_writer,
                         location=args.location, consent_date=args.date, camera=camera)

    frame_rows = store.frames(run_id)
    frame_counts = {
        "measured": sum(1 for r in frame_rows if r["position_kind"] == "MEASURED"),
        "commanded": sum(1 for r in frame_rows if r["position_kind"] == "COMMANDED"),
        "outer_tolerance": sum(1 for r in frame_rows if r["tolerance"] == "OUTER"),
    }

    summary: dict[str, Any] = {"run_id": run_id, "mode": args.mode,
                               "mode_name": MODE_WORDS[args.mode], "camera": camera.name,
                               "frame_counts": frame_counts, "outcome": outcome}
    if args.scene is not None:
        scene = load_scene(args.scene)
        summary["score"] = score_episode(scene, outcome, store, run_id)

    print(json.dumps(summary, indent=2, default=str))
    return 0 if outcome["status"] == "REPORTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
