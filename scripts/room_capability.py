"""CLI for the room-agent capability grant registry: init, issue-trial, promote.

Pure stdlib. No network, provider or drone client is constructed anywhere here. Every command
prints only the canonical grant digest it acted on (or a refusal reason to stderr) -- never a
credential, header, file content or environment value.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from sentinel.room.capabilities import (
    GrantRegistryError,
    empty_registry,
    issue_trial,
    load_registry,
    parse_evidence_index,
    promote,
    write_registry,
)
from sentinel.room.models import AcceptanceManifest, canonical_acceptance_manifest_sha256


def _parse_now(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"--now must be timezone-aware, got {raw!r}")
    return parsed


def _acceptance_manifest(session_id: str, capability: str) -> AcceptanceManifest:
    manifest = AcceptanceManifest(session_id=session_id, capability=capability, manifest_sha256="")
    return replace(manifest, manifest_sha256=canonical_acceptance_manifest_sha256(manifest))


def _cmd_init(args: argparse.Namespace) -> int:
    registry_path = Path(args.registry)
    if registry_path.exists():
        print(f"refused: {registry_path} already exists", file=sys.stderr)
        return 2
    registry = empty_registry()
    write_registry(registry_path, registry)
    # Print the canonical digest acted on, like every other command -- never
    # the filesystem path, which is a caller-supplied string with nothing to
    # do with what was actually written.
    digest = hashlib.sha256(registry.canonical_bytes()).hexdigest()
    print(digest)
    return 0


def _cmd_issue_trial(args: argparse.Namespace) -> int:
    registry_path = Path(args.registry)
    try:
        registry = load_registry(registry_path)
        acceptance_manifest = _acceptance_manifest(args.session_id, args.capability)
        new_registry, grant = issue_trial(
            registry,
            capability=args.capability,
            trial_protocol=args.trial_protocol,
            site_sha256=args.site_sha256,
            aircraft_id=args.aircraft_id,
            profile_sha256s=tuple(args.profile or ()),
            evidence_destination=args.evidence_destination,
            expires_at=args.expires_at,
            acceptance_manifest=acceptance_manifest,
        )
    except (GrantRegistryError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    write_registry(registry_path, new_registry)
    print(grant.grant_sha256)
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    registry_path = Path(args.registry)
    try:
        registry = load_registry(registry_path)
        trial_candidates = [
            grant for grant in registry.grants
            if grant.grant_sha256 == args.trial_grant_sha256 and grant.state == "TRIAL"
        ]
        if len(trial_candidates) != 1:
            print(
                f"refused: no unique TRIAL grant with hash {args.trial_grant_sha256}",
                file=sys.stderr,
            )
            return 2
        evidence_index = parse_evidence_index(Path(args.evidence_index).read_bytes())
        new_registry, grant = promote(
            registry,
            trial_grant=trial_candidates[0],
            evidence_index=evidence_index,
            expires_at=args.expires_at,
            now=_parse_now(args.now),
        )
    except (GrantRegistryError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    write_registry(registry_path, new_registry)
    print(grant.grant_sha256)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="room_capability")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create an empty canonical live registry")
    init_parser.add_argument("--registry", required=True)
    init_parser.set_defaults(func=_cmd_init)

    issue_parser = subparsers.add_parser("issue-trial", help="write one narrowly scoped TRIAL grant")
    issue_parser.add_argument("--capability", required=True)
    issue_parser.add_argument("--trial-protocol", required=True)
    issue_parser.add_argument("--site-sha256", required=True)
    issue_parser.add_argument("--aircraft-id", required=True)
    issue_parser.add_argument("--profile", action="append", default=[])
    issue_parser.add_argument("--evidence-destination", required=True)
    issue_parser.add_argument("--expires-at", required=True)
    issue_parser.add_argument("--session-id", required=True)
    issue_parser.add_argument("--registry", required=True)
    issue_parser.set_defaults(func=_cmd_issue_trial)

    promote_parser = subparsers.add_parser("promote", help="promote a passing TRIAL grant")
    promote_parser.add_argument("--registry", required=True)
    promote_parser.add_argument("--trial-grant-sha256", required=True)
    promote_parser.add_argument("--evidence-index", required=True)
    promote_parser.add_argument("--expires-at", required=True)
    promote_parser.add_argument(
        "--now", default=None,
        help="tz-aware ISO-8601 instant to promote as of; defaults to the real current UTC time",
    )
    promote_parser.set_defaults(func=_cmd_promote)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
