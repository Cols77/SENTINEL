"""CLI for one room-agent session: preview, replay, simulate, live.

Pure stdlib plus sentinel.room.*. Every command prints only the canonical
manifest bytes, a digest, or a typed refusal reason (to stderr) -- never a
credential, header, file content or environment value.

Capability-flight-capable factories (``client_factory``/``jev_factory``,
plus the two connectivity probes) are injected through ``main()``'s
keyword-only parameters, the same dependency-injection convention
``scripts/room_calibrate.py`` uses for its executor. Every one defaults to a
factory that refuses outright: this build never wires a real Tello/provider
client or judgment model into a live session, so a real one can only be
constructed if a caller deliberately overrides the default -- which this
script never does on its own, and this file's own tests never do either.
Every gate below (manifest authorization, capability admission, matching
profiles, consent, site assurance, connectivity) is checked and enforced
*before* any of those factories is ever called.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sentinel.room import artifacts, calibration, capabilities, connectivity, replay
from sentinel.room.artifacts import ArtifactError, ManifestError, SessionManifest
from sentinel.room.site import ContingencyTrigger, RoomSite, parse_room_site
from sentinel.survey.live_detect import LiveDetectRefused, check_gates as check_consent_gates

#: The count-and-return operator's default request text (Plan 06 ruling): the
#: runbook and every example in this packet use this exact string.
DEFAULT_REQUEST_TEXT = (
    "count the computers and phones in the surveyed room, report uncertain "
    "detections as uncertain, then return home"
)

#: The capabilities this build actually offers. closer_inspection (Plan 03
#: Task 3), certified_landing and following (Plan 05) are deferred by
#: operator ruling -- named here, not silently rejected as unknown, so an
#: operator asking for one gets an explicit "unavailable", not a generic
#: argparse error indistinguishable from a typo.
AVAILABLE_CAPABILITIES = ("connectivity", "adaptive_movement", "count_return")
UNAVAILABLE_CAPABILITIES = ("closer_inspection", "certified_landing", "following")

SIMULATION_DEFERRED_MESSAGE = "simulation deferred (Plan 06 Task 2)"


class GateError(RuntimeError):
    """A live-mode authorization gate (capability, profile, consent, site) refused."""


def _refuse_drone_probe() -> "connectivity.ProbeResult":
    raise RuntimeError(
        "connectivity check requires an injected drone_probe; this build never constructs "
        "a real Tello telemetry probe on its own"
    )


def _refuse_provider_probe() -> "connectivity.ProbeResult":
    raise RuntimeError(
        "connectivity check requires an injected provider_probe; this build never constructs "
        "a real provider reachability probe on its own"
    )


def _refuse_client() -> object:
    raise RuntimeError(
        "live flight requires an injected client_factory; this build never constructs a "
        "real Tello/provider client on its own"
    )


def _refuse_jev() -> object:
    raise RuntimeError(
        "live flight requires an injected jev_factory; this build never constructs a "
        "real judgment model on its own"
    )


def _parse_now(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"--now must be timezone-aware, got {raw!r}")
    return parsed


def _check_capability_available(name: str) -> None:
    if name in UNAVAILABLE_CAPABILITIES:
        raise GateError(f"capability {name!r} is not available in this build (Plan 06 count-and-return scope)")
    if name not in AVAILABLE_CAPABILITIES:
        raise GateError(f"capability {name!r} is unknown")


def _resolve_mode(*, target_mode: str, trial_protocol: str | None) -> str:
    if target_mode == "simulate":
        return "simulate"
    return "acceptance" if trial_protocol is not None else "live"


def _missing_gates(manifest: SessionManifest, *, registry_path: str, now: datetime) -> tuple[str, ...]:
    """A read-only, best-effort report for the operator -- never itself a refusal.

    Reuses capabilities.admit_live (the one fail-closed admission point Task 1
    built) exactly as live mode will, so preview's report can never drift from
    what live mode actually checks.
    """
    gates = []
    try:
        registry = capabilities.load_registry(registry_path)
        capabilities.admit_live(registry, manifest=manifest, now=now)
    except (capabilities.GrantRegistryError, OSError, ValueError) as exc:
        gates.append(f"capability admission: {exc}")
    if not manifest.consent_references:
        gates.append("consent: manifest declares no consent_references")
    return tuple(gates)


def _cmd_preview(args: argparse.Namespace) -> int:
    try:
        _check_capability_available(args.capability)
    except GateError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if (args.trial_protocol is None) != (args.evidence_destination is None):
        print("refused: --trial-protocol and --evidence-destination must be given together", file=sys.stderr)
        return 2
    try:
        now = _parse_now(args.now)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    try:
        provider_deadlines = json.loads(args.provider_deadlines_json)
        cost_assumptions = json.loads(args.cost_assumptions_json)
        home_policy = json.loads(args.home_policy_json)
        termination_policy = json.loads(args.termination_policy_json)
    except json.JSONDecodeError as exc:
        print(f"refused: invalid JSON option: {exc}", file=sys.stderr)
        return 2
    mode = _resolve_mode(target_mode=args.target_mode, trial_protocol=args.trial_protocol)
    try:
        manifest = SessionManifest(
            schema_version=artifacts.SCHEMA_VERSION, run_id=args.run_id, created_at=now.isoformat(),
            mode=mode, code_revision=args.code_revision, code_dirty=args.code_dirty,
            integration_revision=args.integration_revision, requested_capability=args.capability,
            capability_grant_sha256=args.capability_grant_sha256, capability_grant_state=args.capability_grant_state,
            request_revision=args.request_revision, goal_parser_revision=args.goal_parser_revision,
            site_sha256=args.site_sha256, tag_map_sha256=args.tag_map_sha256, camera_sha256=args.camera_sha256,
            profile_sha256s=tuple(sorted(args.profile)), aircraft_id=args.aircraft_id,
            firmware_version=args.firmware_version, requested_jev_identity=args.requested_jev_identity,
            resolved_jev_identity="UNVERIFIED", requested_detector_identity=args.requested_detector_identity,
            resolved_detector_identity="UNVERIFIED", max_duration_s=args.max_duration_s,
            max_distance_cm=args.max_distance_cm, max_recovery_attempts=args.max_recovery_attempts,
            provider_deadlines=provider_deadlines, cost_assumptions=cost_assumptions, home_policy=home_policy,
            termination_policy=termination_policy, consent_references=tuple(args.consent_reference),
            trial_protocol=args.trial_protocol, evidence_destination=args.evidence_destination,
        )
        raw = manifest.canonical_bytes()
    except (ManifestError, ArtifactError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    # Canonical bytes are always ASCII-safe JSON (see artifacts.canonical_json), so
    # printing the decoded text is exactly the canonical bytes on stdout -- nothing
    # else this command prints (missing-gate notes, the next command) goes there.
    print(raw.decode("utf-8"))
    if args.manifest_out:
        artifacts.atomic_write_bytes(args.manifest_out, raw)
    digest = manifest.to_document()[artifacts.DIGEST_FIELD]
    gates = _missing_gates(manifest, registry_path=args.grant_registry, now=now)
    for gate in gates:
        print(f"missing gate: {gate}", file=sys.stderr)
    if not gates and args.manifest_out:
        print(
            "next: python scripts/room_session.py --mode live --execute "
            f"--manifest {args.manifest_out} --authorization-digest {digest} "
            f"--capability {args.capability} --grant-registry {args.grant_registry}",
            file=sys.stderr,
        )
    return 0


def _check_profiles(manifest: SessionManifest, profile_files: list[str], *, grant_state: str) -> None:
    declared = set(manifest.profile_sha256s)
    if not declared:
        return
    seen = set()
    for path in profile_files:
        try:
            profile = calibration.load_profile(path)
        except (calibration.CalibrationError, OSError) as exc:
            raise GateError(f"profile file {path!r} could not be loaded: {exc}") from exc
        digest = calibration.canonical_profile_sha256(profile)
        if digest not in declared:
            raise GateError(f"profile file {path!r} (sha256 {digest}) is not among the manifest's profile_sha256s")
        if not calibration.admissible_for(profile, grant_state=grant_state):
            raise GateError(f"profile {digest} (state {profile.state}) is not admissible for grant state {grant_state!r}")
        seen.add(digest)
    missing = declared - seen
    if missing:
        raise GateError(f"no --profile-file supplied for declared profile_sha256(s): {sorted(missing)}")


def _load_assurance(path: str):
    from sentinel.room.site import ContingencyAssurance

    document = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return ContingencyAssurance(
            assurance_id=document["assurance_id"], contingency_id=document["contingency_id"],
            session_id=document["session_id"], clock_domain_id=document["clock_domain_id"],
            state=document["state"], assured_at=document["assured_at"],
            assured_monotonic_ms=document["assured_monotonic_ms"],
            expires_monotonic_ms=document["expires_monotonic_ms"],
            evidence_refs=tuple(document["evidence_refs"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GateError(f"assurance file {path!r} is malformed: {exc}") from exc


def _check_site_assurance(site_path: str | None, assurance_paths: list[str], *,
                          clock_domain_id: str | None, now_monotonic_ms: int) -> None:
    if site_path is None:
        raise GateError("--site is required to verify site assurance before a live flight")
    if not clock_domain_id:
        raise GateError("--clock-domain-id is required to verify site assurance before a live flight")
    try:
        site: RoomSite = parse_room_site(json.loads(Path(site_path).read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise GateError(f"--site could not be loaded: {exc}") from exc
    assurances = tuple(_load_assurance(path) for path in assurance_paths)
    for trigger in ContingencyTrigger:
        entries = [entry for entry in site.terminal_contingencies if entry.trigger == trigger]
        entries_by_id = {entry.contingency_id: entry for entry in entries}
        matching = [a for a in assurances
                   if a.clock_domain_id == clock_domain_id and a.contingency_id in entries_by_id]
        revoked_ids = {a.assurance_id for a in matching if a.state == "REVOKED"}
        current = []
        for assurance in matching:
            if assurance.state != "CURRENT" or assurance.assurance_id in revoked_ids:
                continue
            entry = entries_by_id[assurance.contingency_id]
            deadline = min(assurance.expires_monotonic_ms, assurance.assured_monotonic_ms + entry.max_assurance_age_ms)
            if assurance.assured_monotonic_ms <= now_monotonic_ms < deadline:
                current.append(assurance)
        if len(current) != 1:
            raise GateError(f"site assurance for trigger {trigger.value} is missing, expired or not uniquely current")


def _cmd_live(
    args: argparse.Namespace, *,
    drone_probe: Callable[[], connectivity.ProbeResult],
    provider_probe: Callable[[], connectivity.ProbeResult],
    client_factory: Callable[[], object],
    jev_factory: Callable[[], object],
    clock: Callable[[], float],
) -> int:
    try:
        _check_capability_available(args.capability)
    except GateError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    manifest_path = Path(args.manifest)
    try:
        manifest = artifacts.parse_manifest(manifest_path.read_bytes())
    except (ManifestError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    bound_digest = manifest.to_document()[artifacts.DIGEST_FIELD]
    if bound_digest != args.authorization_digest:
        print("refused: authorization_digest does not match the manifest bound at preview time", file=sys.stderr)
        return 2
    if manifest.mode not in ("live", "acceptance"):
        print(f"refused: manifest mode {manifest.mode!r} is not a flight-capable live mode", file=sys.stderr)
        return 2
    if manifest.requested_capability != args.capability:
        print("refused: --capability does not match the manifest's requested_capability", file=sys.stderr)
        return 2
    try:
        now = _parse_now(args.now)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    # Provider/filming consent: required for every live invocation, including
    # the motors-off connectivity-only diagnostic (the sole capability-grant
    # exemption -- consent is never exempted).
    try:
        check_consent_gates(
            consent_db=Path(args.consent_db) if args.consent_db else None,
            location=args.location, date=args.date, authorised=args.i_authorise_paid_calls,
        )
    except LiveDetectRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    if not args.check_connectivity_only:
        try:
            registry = capabilities.load_registry(args.grant_registry)
            winning_grant = capabilities.admit_live(registry, manifest=manifest, now=now)
        except (capabilities.GrantRegistryError, OSError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        try:
            _check_profiles(manifest, args.profile_file, grant_state=winning_grant.state)
            _check_site_assurance(args.site, args.assurance, clock_domain_id=args.clock_domain_id,
                                  now_monotonic_ms=int(now.timestamp() * 1000))
        except GateError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2

    # Past every capability/consent/(site) gate. A motors-off connectivity
    # preflight always runs before takeoff -- proving fresh drone telemetry
    # and provider reachability concurrently -- whether this is the explicit
    # diagnostic or the last check before a real flight.
    result = connectivity.check(
        drone_probe=drone_probe, provider_probe=provider_probe,
        max_age_s=args.connectivity_max_age_s, timeout_s=args.connectivity_timeout_s, clock=clock,
    )
    if not result.ready:
        print(
            "refused: connectivity preflight not ready "
            f"(drone_ready={result.drone_ready}, provider_ready={result.provider_ready}, "
            f"drone_error={result.drone_error}, provider_error={result.provider_error})",
            file=sys.stderr,
        )
        return 2
    print(
        f"drone_identity={result.drone_identity} drone_checked_at={result.drone_checked_at} "
        f"provider_identity={result.provider_identity} provider_checked_at={result.provider_checked_at}",
        file=sys.stderr,
    )
    if args.check_connectivity_only:
        print("CONNECTIVITY_READY")
        return 0

    # Full live flight: this CLI's own scope stops here. SafetySupervisor/
    # tracker/reserve/recovery bootstrap and the actual controller run loop
    # are an operator-supplied controller_factory this build does not wire
    # (see docs/room-agent-live-runbook.md) -- client_factory/jev_factory
    # default to refuse, so no real client or judgment model is ever
    # constructed without a caller deliberately overriding both.
    try:
        client_factory()
        jev_factory()
    except Exception as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(
        "refused: full live flight execution is not wired in this build; connectivity passed "
        "but no controller_factory is available (see docs/room-agent-live-runbook.md)",
        file=sys.stderr,
    )
    return 2


def _cmd_replay(args: argparse.Namespace) -> int:
    try:
        result = replay.replay_fixture(Path(args.fixture))
    except replay.ReplayIntegrityError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "final_snapshot_sha256": result.final_snapshot_sha256,
        "decision_stream_sha256": result.decision_stream_sha256,
        "report_sha256": result.report_sha256,
        "terminal_state": result.terminal_state.value,
    }))
    return 0


def _cmd_simulate(args: argparse.Namespace) -> int:
    print(f"refused: {SIMULATION_DEFERRED_MESSAGE}", file=sys.stderr)
    return 2


def _add_capability_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--capability", required=True, choices=(*AVAILABLE_CAPABILITIES, *UNAVAILABLE_CAPABILITIES))
    parser.add_argument("--grant-registry", required=True)
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--trial-protocol", default=None)
    parser.add_argument("--evidence-destination", default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="room_session")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    preview_parser = subparsers.add_parser("preview", help="preview and print a canonical session manifest")
    preview_parser.add_argument("--target-mode", required=True, choices=("simulate", "live"))
    preview_parser.add_argument("--run-id", required=True)
    _add_capability_args(preview_parser)
    preview_parser.add_argument("--capability-grant-sha256", required=True)
    preview_parser.add_argument("--capability-grant-state", required=True,
                                choices=("SIMULATION_ONLY", "DISABLED", "TRIAL", "PROMOTED"))
    preview_parser.add_argument("--code-revision", required=True)
    preview_parser.add_argument("--code-dirty", action="store_true")
    preview_parser.add_argument("--integration-revision", required=True)
    preview_parser.add_argument("--request-revision", required=True)
    preview_parser.add_argument("--goal-parser-revision", required=True)
    preview_parser.add_argument("--site-sha256", required=True)
    preview_parser.add_argument("--tag-map-sha256", required=True)
    preview_parser.add_argument("--camera-sha256", required=True)
    preview_parser.add_argument("--aircraft-id", required=True)
    preview_parser.add_argument("--firmware-version", required=True)
    preview_parser.add_argument("--requested-jev-identity", required=True)
    preview_parser.add_argument("--requested-detector-identity", required=True)
    preview_parser.add_argument("--max-duration-s", required=True, type=float)
    preview_parser.add_argument("--max-distance-cm", required=True, type=float)
    preview_parser.add_argument("--max-recovery-attempts", required=True, type=int)
    preview_parser.add_argument("--provider-deadlines-json", default="{}")
    preview_parser.add_argument("--cost-assumptions-json", default="{}")
    preview_parser.add_argument("--home-policy-json", default="{}")
    preview_parser.add_argument("--termination-policy-json", default="{}")
    preview_parser.add_argument("--consent-reference", action="append", default=[])
    preview_parser.add_argument("--manifest-out", default=None)
    preview_parser.add_argument("--now", default=None)
    preview_parser.set_defaults(func=_cmd_preview)

    live_parser = subparsers.add_parser("live", help="run (or connectivity-check) one authorized live session")
    live_parser.add_argument("--execute", action="store_true", required=True)
    live_parser.add_argument("--manifest", required=True)
    live_parser.add_argument("--authorization-digest", required=True)
    _add_capability_args(live_parser)
    live_parser.add_argument("--profile-file", action="append", default=[])
    live_parser.add_argument("--consent-db", default=None)
    live_parser.add_argument("--location", default=None)
    live_parser.add_argument("--date", default=None)
    live_parser.add_argument("--i-authorise-paid-calls", action="store_true")
    live_parser.add_argument("--site", default=None)
    live_parser.add_argument("--assurance", action="append", default=[])
    live_parser.add_argument("--clock-domain-id", default=None)
    live_parser.add_argument("--check-connectivity-only", action="store_true")
    live_parser.add_argument("--connectivity-max-age-s", type=float, default=5.0)
    live_parser.add_argument("--connectivity-timeout-s", type=float, default=5.0)
    live_parser.add_argument("--now", default=None)
    live_parser.set_defaults(func=None)

    replay_parser = subparsers.add_parser("replay", help="replay a frozen fixture")
    replay_parser.add_argument("--fixture", required=True)
    replay_parser.set_defaults(func=_cmd_replay)

    simulate_parser = subparsers.add_parser("simulate", help="simulation mode (deferred)")
    simulate_parser.add_argument("--fixture", required=True)
    simulate_parser.set_defaults(func=_cmd_simulate)

    return parser


def main(
    argv: list[str] | None = None, *,
    drone_probe: Callable[[], connectivity.ProbeResult] = _refuse_drone_probe,
    provider_probe: Callable[[], connectivity.ProbeResult] = _refuse_provider_probe,
    client_factory: Callable[[], object] = _refuse_client,
    jev_factory: Callable[[], object] = _refuse_jev,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "live":
        return _cmd_live(args, drone_probe=drone_probe, provider_probe=provider_probe,
                         client_factory=client_factory, jev_factory=jev_factory, clock=clock)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
