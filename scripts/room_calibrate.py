"""CLI for room-agent calibration: preview, record, fit, promote.

Pure stdlib plus sentinel.room.calibration. Every command prints only the canonical
bytes or digest it acted on (or a refusal reason to stderr) -- never a credential,
header, file content or environment value.

``record``'s executor is injected through ``main()``'s ``executor_factory`` keyword,
the same dependency-injection convention scripts/cube_trial.py uses for its
subprocess runners. It defaults to a factory that refuses outright: this build never
wires a real Tello/provider client into calibration recording, so a real client can
only be constructed if a caller deliberately overrides the default -- which this
script never does on its own.
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable

from sentinel.room import calibration
from sentinel.room.calibration import CalibrationError


def _refuse_real_executor() -> object:
    raise RuntimeError(
        "record mode requires an injected executor factory; this build never "
        "constructs a real Tello/provider client for calibration recording"
    )


def _cmd_preview(args: argparse.Namespace) -> int:
    try:
        raw = calibration.preview(
            target_mode=args.target_mode, trial_role=args.trial_role, split_id=args.split_id,
        )
    except CalibrationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    # Canonical bytes are always ASCII-safe JSON (see artifacts.canonical_json), so
    # printing the decoded text is exactly the canonical bytes, just through the
    # ordinary text stream rather than the raw binary one.
    print(raw.decode("utf-8"))
    if args.manifest_out:
        calibration.atomic_write_bytes(args.manifest_out, raw)
    return 0


def _cmd_record(args: argparse.Namespace, *, executor_factory: Callable[[], object]) -> int:
    try:
        calibration.record(
            manifest_path=args.manifest, authorization_digest=args.authorization_digest,
            route_path=args.route, executor_factory=executor_factory,
        )
    except CalibrationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_fit(args: argparse.Namespace) -> int:
    try:
        profile = calibration.fit(
            profile_kind=args.profile_kind, evidence_index_path=args.evidence_index,
            output_path=args.output,
        )
    except CalibrationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(calibration.canonical_profile_sha256(profile))
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    try:
        profile = calibration.promote(
            profile_kind=args.profile_kind, candidate_path=args.candidate,
            evidence_index_path=args.evidence_index, output_path=args.output,
        )
    except CalibrationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(calibration.canonical_profile_sha256(profile))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="room_calibrate")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    preview_parser = subparsers.add_parser("preview", help="bind role/split into a canonical record manifest")
    preview_parser.add_argument("--target-mode", required=True)
    preview_parser.add_argument("--trial-role", required=True, choices=("training", "holdout"))
    preview_parser.add_argument("--split-id", required=True)
    preview_parser.add_argument("--manifest-out", default=None)
    preview_parser.set_defaults(func=_cmd_preview)

    record_parser = subparsers.add_parser("record", help="fly one controller-owned fixed calibration route")
    record_parser.add_argument("--execute", action="store_true", required=True)
    record_parser.add_argument("--manifest", required=True)
    record_parser.add_argument("--authorization-digest", required=True)
    record_parser.add_argument("--route", required=True)

    fit_parser = subparsers.add_parser("fit", help="fit a CALIBRATION candidate from predeclared training evidence")
    fit_parser.add_argument("--profile-kind", required=True, choices=calibration.PROFILE_KINDS)
    fit_parser.add_argument("--evidence-index", required=True)
    fit_parser.add_argument("--output", required=True)
    fit_parser.set_defaults(func=_cmd_fit)

    promote_parser = subparsers.add_parser("promote", help="promote a CALIBRATION candidate using holdout evidence")
    promote_parser.add_argument("--profile-kind", required=True, choices=calibration.PROFILE_KINDS)
    promote_parser.add_argument("--evidence-index", required=True)
    promote_parser.add_argument("--output", required=True)
    promote_parser.add_argument("--candidate", required=True)
    promote_parser.set_defaults(func=_cmd_promote)

    return parser


def main(argv: list[str] | None = None, *, executor_factory: Callable[[], object] = _refuse_real_executor) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "record":
        return _cmd_record(args, executor_factory=executor_factory)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
