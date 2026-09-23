"""CLI to independently verify one room-agent run artifact directory.

Pure stdlib plus sentinel.room.*. Reads only local files; constructs no
provider, drone client or network object. Every refusal prints a reason to
stderr and exits nonzero; a clean run prints only the run_id it verified.

Verifies, against the run directory a manifest.json's own run_id names
(``manifest_path.parent`` -- ``RunArtifacts.open``'s own layout convention,
so the run_id this script resolves is deterministic: it is read straight
from the manifest, never guessed or derived separately from it):

- manifest hash: ``artifacts.parse_manifest`` fails closed on any digest,
  schema or field mismatch before this script ever inspects the document.
- terminal hash/state: ``terminal.json`` exists, names this same run_id, and
  its recorded ``state`` matches ``--expect-terminal`` exactly. For a
  safe-termination state (COMPLETED, INCOMPLETE_RETURNED, SAFE_LANDED,
  LANDED_AT_CERTIFIED_ZONE -- the states controller.py's own
  ``_finalize_terminal`` only ever reaches after a confirmed touchdown),
  also requires ``completed_at`` evidence; its absence refuses as an
  absent-safe-termination-evidence failure, even if the state string alone
  matches.
- event hashes: every durable event this run's own ``session.sqlite3``
  holds is independently re-hashed (``store._event_sha256``, the store's
  own trigger-protected content hash) and must match what the store itself
  recorded -- catching a SQLite file edited outside the store's write path,
  not just a plausible-looking terminal record.
- profile hashes (optional, ``--profile-file``): each supplied calibration
  profile file's own canonical hash must be among the manifest's declared
  ``profile_sha256s``.
- identity: the resolved-identity map independently rebuilt from the
  durable ``IDENTITY_RESOLVED`` events must equal what ``terminal.json``
  recorded at finalize time -- a mismatch here is exactly a tampered or
  drifted identity record.
- ``--require-event`` (repeatable): each named event type must appear at
  least once in the durable event log.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from sentinel.room import artifacts, calibration
from sentinel.room.artifacts import ManifestError
from sentinel.room.models import TerminalState
from sentinel.room.store import RoomStore
from sentinel.room.store import _event_sha256 as store_event_sha256

_SAFE_TERMINATION_STATES = frozenset({
    TerminalState.COMPLETED.value, TerminalState.INCOMPLETE_RETURNED.value,
    TerminalState.SAFE_LANDED.value, TerminalState.LANDED_AT_CERTIFIED_ZONE.value,
})

#: Fix round 1 (Q8): terminal.json's own ``completed_at`` key is a
#: caller-supplied mapping -- nothing in this build's production code writes
#: it yet, so a truthy value there alone is not evidence of anything. Reuse
#: the same durable-event predicate ``sentinel.room.queries.ReadQueries.project``
#: (and ``sentinel.room.supervisor.SafetySupervisor.__init__``'s own
#: ``_terminal`` latch) already treat as a confirmed safe landing: a
#: ``DRONE_STATE_RECORDED`` event whose measured state reports
#: ``terminal_state == "LANDED"`` with ``completion_known`` true -- an
#: SDK-uncertain completion is never durable landing evidence.
_LANDED_TERMINAL_STATE = "LANDED"


class VerificationError(RuntimeError):
    """A run artifact failed one of this script's independent checks."""


def _has_durable_landing_evidence(events) -> bool:
    for event in events:
        if event.event_type != "DRONE_STATE_RECORDED":
            continue
        state = event.payload.get("state")
        if not isinstance(state, dict):
            continue
        if state.get("terminal_state") == _LANDED_TERMINAL_STATE and state.get("completion_known"):
            return True
    return False


def _resolved_identities_from_events(events) -> dict:
    resolved: dict = {}
    for event in events:
        if event.event_type != "IDENTITY_RESOLVED":
            continue
        role, value = event.payload.get("role"), event.payload.get("resolved")
        previous = resolved.get(role)
        if previous is not None and previous != value:
            raise VerificationError(f"durable event log has conflicting resolved identities for role {role!r}")
        resolved[role] = value
    return resolved


def verify(*, manifest_path: Path, expect_terminal: str, expect_capability: str,
          require_event: tuple[str, ...] = (), profile_files: tuple[str, ...] = ()) -> str:
    """Run every check; returns the verified run_id, or raises VerificationError."""
    manifest = artifacts.parse_manifest(manifest_path.read_bytes())
    run_id = manifest.run_id
    run_dir = manifest_path.parent
    if run_dir.name != run_id:
        raise VerificationError(
            f"manifest is not stored under its own run_id directory "
            f"(expected {run_id!r}, found {run_dir.name!r})"
        )
    if manifest.requested_capability != expect_capability:
        raise VerificationError(
            f"manifest requested_capability {manifest.requested_capability!r} "
            f"does not match --expect-capability {expect_capability!r}"
        )
    terminal_path = run_dir / "terminal.json"
    if not terminal_path.exists():
        raise VerificationError("run directory has no terminal.json")
    try:
        terminal_document = json.loads(terminal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerificationError(f"terminal.json is not valid JSON: {exc}") from exc
    if not isinstance(terminal_document, dict):
        raise VerificationError("terminal.json must be a JSON object")
    if terminal_document.get("run_id") != run_id:
        raise VerificationError("terminal.json run_id does not match the manifest's run_id")
    state = terminal_document.get("state")
    if state != expect_terminal:
        raise VerificationError(f"terminal state {state!r} does not match --expect-terminal {expect_terminal!r}")
    if state in _SAFE_TERMINATION_STATES and not terminal_document.get("completed_at"):
        raise VerificationError(
            f"terminal state {state!r} is a safe-termination state but terminal.json "
            "records no completed_at evidence of an actual confirmed landing"
        )

    sqlite_path = run_dir / "session.sqlite3"
    if not sqlite_path.exists():
        raise VerificationError("run directory has no session.sqlite3")
    store = RoomStore.open(sqlite_path)
    try:
        events = store.events(run_id)
    finally:
        store.close()
    if not events:
        raise VerificationError("run has zero durable events")
    # Fix round 1 (Q8): a gap in sequence_no (an event deleted straight from
    # the SQLite file, bypassing the store's own append-only write path) is
    # otherwise invisible to every check below -- each remaining event's own
    # hash still recomputes fine, and the resolved-identity/require-event
    # checks only ever look for presence, never completeness.
    sequence_numbers = sorted(event.sequence_no for event in events)
    if sequence_numbers != list(range(1, len(events) + 1)):
        raise VerificationError(
            "durable event sequence_no is not contiguous from 1 (a gap indicates deleted or reordered events)"
        )
    for event in events:
        recomputed = store_event_sha256(
            session_id=event.session_id, event_type=event.event_type,
            payload_json=json.dumps(event.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
            correlation_id=event.correlation_id, causation_id=event.causation_id,
        )
        if recomputed != event.event_sha256:
            raise VerificationError(f"event {event.event_id} hash does not match its durable content")
    if state in _SAFE_TERMINATION_STATES and not _has_durable_landing_evidence(events):
        raise VerificationError(
            f"terminal state {state!r} is a safe-termination state but the durable event log has no "
            "DRONE_STATE_RECORDED evidence of a confirmed landing (terminal_state=LANDED, completion_known)"
        )

    resolved = _resolved_identities_from_events(events)
    if resolved != terminal_document.get("resolved_identities", {}):
        raise VerificationError(
            "terminal.json resolved_identities do not match the durable event log (mismatched identity)"
        )

    declared_profiles = set(manifest.profile_sha256s)
    for path in profile_files:
        try:
            profile = calibration.load_profile(path)
        except (calibration.CalibrationError, OSError) as exc:
            raise VerificationError(f"profile file {path!r} could not be loaded: {exc}") from exc
        digest = calibration.canonical_profile_sha256(profile)
        if digest not in declared_profiles:
            raise VerificationError(
                f"profile file {path!r} (sha256 {digest}) is not among the manifest's declared profile_sha256s"
            )

    for event_type in require_event:
        if not any(event.event_type == event_type for event in events):
            raise VerificationError(f"required event type {event_type!r} is missing from the run")

    # Fix round 1 (Q8): cross-check summary.json (RunArtifacts.finalize's own
    # second, independently-written record of this run) against what was just
    # independently verified above, rather than trusting terminal.json alone.
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise VerificationError("run directory has no summary.json")
    try:
        summary_document = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerificationError(f"summary.json is not valid JSON: {exc}") from exc
    if not isinstance(summary_document, dict):
        raise VerificationError("summary.json must be a JSON object")
    if summary_document.get("run_id") != run_id:
        raise VerificationError("summary.json run_id does not match the manifest's run_id")
    if summary_document.get("terminal_state") != state:
        raise VerificationError(
            f"summary.json terminal_state {summary_document.get('terminal_state')!r} "
            f"does not match terminal.json's state {state!r}"
        )
    if summary_document.get("event_count") != len(events):
        raise VerificationError(
            f"summary.json event_count ({summary_document.get('event_count')!r}) does not match "
            f"the durable event log ({len(events)} events)"
        )

    return run_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="room_verify_artifact")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expect-terminal", required=True, choices=tuple(state.value for state in TerminalState))
    parser.add_argument("--expect-capability", required=True)
    parser.add_argument("--require-event", action="append", default=[])
    parser.add_argument("--profile-file", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_id = verify(
            manifest_path=Path(args.manifest), expect_terminal=args.expect_terminal,
            expect_capability=args.expect_capability, require_event=tuple(args.require_event),
            profile_files=tuple(args.profile_file),
        )
    except (VerificationError, ManifestError, OSError, sqlite3.Error) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
