"""CLI for one room-agent session: preview, replay, simulate, live.

Pure stdlib plus sentinel.room.*. Every command prints only the canonical
manifest bytes, a digest, or a typed refusal reason (to stderr) -- never a
credential, header, file content or environment value.

Capability-flight-capable factories (``client_factory``/``jev_factory``/
``detector_factory``, plus the two connectivity probes) are injected
through ``main()``'s keyword-only parameters, the same dependency-injection
convention ``scripts/room_calibrate.py`` uses for its executor. Task A: the
two probes and ``client_factory`` default to the real-Tello adapters in
``sentinel.room.live_stack`` -- a motors-off telemetry probe and a flight
stack sharing one Tello connection, and an OpenRouter reachability probe.
The full flight's default stack also needs ``--tag-map``, bound to the
manifest's ``tag_map_sha256``. Task B: ``jev_factory``/``detector_factory``
default to the live Jev judge and the gemini-openrouter detector
(``live_stack.default_jev``/``default_detector``), which refuse without
``OPENROUTER_API_KEY``/``GEMINI_OPENROUTER_MODEL``. Every gate below (manifest authorization, capability
admission, matching profiles, consent, site assurance, tag map,
connectivity) is checked and enforced *before* any of those defaults is
built or called.

Fix round 4 (Q2): one ``live`` invocation authorizes and runs the bounded
count-and-return session. After every gate and a ready preflight, Jev and
the detector are constructed and their reported ``.identity`` durably
recorded (``IDENTITY_RESOLVED``; the manifest always declares them
``"UNVERIFIED"``). ``sentinel.room.live_session.run_count_return`` then
builds the tracker/reserve from the measured ``--drift-profile``/
``--energy-profile`` files, the flight stack from ``client_factory``, the
REAL-mode ``SafetySupervisor``, the goal ledger (``GoalCompiler``) and the
``sentinel.room.wiring`` observation collaborators. It re-checks
connectivity freshness (``TAKEOFF_AUTHORIZED``), takes off through
``SafetySupervisor.start`` and drives ``RoomController.run_until_terminal``.
This CLI then finalizes ``terminal.json``. Whether a REAL-mode session may
fly OBSERVE checkpoints is decided by the controller alone; see
``docs/room-agent-live-runbook.md``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sentinel.emergency import EmergencyHotkey
from sentinel.room import (artifacts, calibration, capabilities, connectivity, live_session, live_stack, replay,
                           wiring)
from sentinel.room.artifacts import ArtifactError, ManifestError, SessionManifest
from sentinel.room.controller import AcceptancePolicy
from sentinel.room.site import ContingencyTrigger, RoomSite, parse_room_site
from sentinel.room.supervisor import calibration_hashes
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


class _ArmedFlightStackFactory:
    """Wrap a flight-stack factory to arm the operator's motor-stop hotkey.

    Q1: every other real-flight path in this codebase arms
    ``EmergencyHotkey(runner.trigger_emergency)`` (``sentinel.cli``'s
    ``cube-run --real``); nothing in ``sentinel.room`` did, although
    ``FlightStack.emergency`` is only a forced landing, never the direct
    motor stop. This arms one for the whole flight, from stack construction
    until landing/close, whenever the built stack exposes
    ``trigger_emergency`` (the real Tello stack does; fakes and replay do
    not, and are left unarmed).
    """

    def __init__(self, inner: Callable[..., "live_session.FlightStack"],
                 on_stack: Callable[..., "live_session.FlightStack"] | None = None) -> None:
        self._inner = inner
        self._on_stack = on_stack
        self.hotkey: EmergencyHotkey | None = None

    def __call__(self, **kwargs) -> "live_session.FlightStack":
        stack = self._inner(**kwargs)
        if self._on_stack is not None:
            # Mission Control's room-agent mode: observe the built stack (its
            # executor for a controlled Land, its motor stop for Emergency)
            # and may wrap its recovery burst for display; never replaces it.
            stack = self._on_stack(stack, **kwargs) or stack
        trigger = getattr(stack, "trigger_emergency", None)
        if trigger is not None:
            print("EMERGENCY: press E to stop motors immediately.", file=sys.stderr)
            self.hotkey = EmergencyHotkey(trigger)
            self.hotkey.start()
        return stack

    def stop(self) -> None:
        if self.hotkey is not None:
            self.hotkey.stop()


def _default_jev() -> object:
    """Task B (L3): the live Jev judge, built only after every gate and a ready preflight."""
    return live_stack.default_jev()


def _default_detector() -> object:
    """Task B (L3): the live gemini-openrouter detector, built only after every gate."""
    return live_stack.default_detector()


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


def _preview_bindings(args: argparse.Namespace) -> tuple[tuple[str, ...], str, str]:
    """The manifest's profile/site/tag-map bindings for preview.

    Task B item 5: given the flight files (``--site``, ``--drift-profile``,
    ``--energy-profile``, optional ``--command-limits`` and
    ``--profile-file``), the profile set is the single grant's binding set
    (``live_session.FlightBinding.sha256s``) and the site/tag-map hashes come
    from the site document -- the same files ``room_capability.py
    issue-trial`` bound the grant to. Raw ``--profile``/``--site-sha256``/
    ``--tag-map-sha256`` values still work and must agree with the files.
    """
    profiles = set(args.profile)
    site_sha256, tag_map_sha256 = args.site_sha256, args.tag_map_sha256
    files = (args.site, args.drift_profile, args.energy_profile)
    if args.site and not (args.drift_profile or args.energy_profile or args.profile_file or args.command_limits):
        # A site alone (the motors-off L0 manifest) binds only its own hashes.
        try:
            site = parse_room_site(json.loads(Path(args.site).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise GateError(f"--site could not be loaded: {exc}") from exc
        for name, value, derived in (("--site-sha256", site_sha256, site.cube_site_sha256),
                                     ("--tag-map-sha256", tag_map_sha256, site.tag_map_sha256)):
            if value is not None and value != derived:
                raise GateError(f"{name} does not match --site")
        return tuple(sorted(profiles)), site.cube_site_sha256, site.tag_map_sha256
    if any(files) or args.profile_file or args.command_limits:
        if not all(files):
            raise GateError("--site, --drift-profile and --energy-profile bind the grant together")
        binding = live_session.load_flight_binding(
            site_path=args.site, drift_profile_path=args.drift_profile, energy_profile_path=args.energy_profile,
            command_limits_path=args.command_limits, profile_paths=tuple(args.profile_file))
        profiles |= set(binding.sha256s)
        for name, value, derived in (("--site-sha256", site_sha256, binding.site.cube_site_sha256),
                                     ("--tag-map-sha256", tag_map_sha256, binding.site.tag_map_sha256)):
            if value is not None and value != derived:
                raise GateError(f"{name} does not match --site")
        site_sha256, tag_map_sha256 = binding.site.cube_site_sha256, binding.site.tag_map_sha256
    if site_sha256 is None or tag_map_sha256 is None:
        raise GateError("--site-sha256 and --tag-map-sha256 (or --site) are required")
    return tuple(sorted(profiles)), site_sha256, tag_map_sha256


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
        profile_sha256s, site_sha256, tag_map_sha256 = _preview_bindings(args)
    except (GateError, live_session.LiveSessionError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    try:
        manifest = SessionManifest(
            schema_version=artifacts.SCHEMA_VERSION, run_id=args.run_id, created_at=now.isoformat(),
            mode=mode, code_revision=args.code_revision, code_dirty=args.code_dirty,
            integration_revision=args.integration_revision, requested_capability=args.capability,
            capability_grant_sha256=args.capability_grant_sha256, capability_grant_state=args.capability_grant_state,
            request_revision=args.request_revision, goal_parser_revision=args.goal_parser_revision,
            site_sha256=site_sha256, tag_map_sha256=tag_map_sha256,
            camera_sha256=args.camera_sha256 or wiring.camera_model_sha256(),
            profile_sha256s=profile_sha256s, aircraft_id=args.aircraft_id,
            firmware_version=args.firmware_version, requested_jev_identity=args.requested_jev_identity,
            resolved_jev_identity="UNVERIFIED", requested_detector_identity=args.requested_detector_identity,
            resolved_detector_identity="UNVERIFIED", max_duration_s=args.max_duration_s,
            max_distance_cm=args.max_distance_cm, max_recovery_attempts=args.max_recovery_attempts,
            provider_deadlines=provider_deadlines, cost_assumptions=cost_assumptions, home_policy=home_policy,
            termination_policy=termination_policy, consent_references=tuple(args.consent_reference),
            trial_protocol=args.trial_protocol, evidence_destination=args.evidence_destination,
        )
        raw = manifest.canonical_bytes()
        # Fix round 1 (Q5): apply the exact same fail-closed check live's own
        # parse_manifest(read_bytes()) will apply later (JSON Schema, not just
        # SessionManifest's own dataclass invariants -- artifacts.write_manifest
        # exists for precisely this reason). Without this, preview could print
        # and write a manifest/digest that live then refuses at run time (a
        # non-hex grant/profile hash, an out-of-range duration, ...), and the
        # operator would only find out after copying the printed command.
        artifacts.parse_manifest(raw)
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
        # Fix round 1 (S1/Q4): `room_session.py`'s parser uses positional
        # subcommands (preview|live|replay|simulate), not a `--mode` flag --
        # the previous "--mode live --execute" text was not runnable
        # (argparse: "unrecognized arguments: --mode"). This is the exact
        # positional syntax live actually accepts, with every flag a
        # flight-capable live invocation requires: the two this preview run
        # already knows (--manifest/--authorization-digest/--capability/
        # --grant-registry) plus explicit `<...>` placeholders for the
        # evidence files/consent flags preview never takes (it binds to
        # concrete files an operator does not necessarily have yet at preview
        # time -- see the runbook's "Known follow-ups"), so the operator
        # knows exactly what to fill in rather than discovering a missing
        # flag only when live refuses.
        next_command = (
            "python scripts/room_session.py live --execute "
            f"--manifest {args.manifest_out} --authorization-digest {digest} "
            f"--capability {args.capability} --grant-registry {args.grant_registry} "
            "--consent-db <consent-db-path> --location <location> --date <YYYY-MM-DD> "
            "--i-authorise-paid-calls --profile-file <profile-file-path> "
            "--site <site-path> --tag-map <tag-map-path> --assurance <assurance-file-path> "
            "--clock-domain-id <clock-domain-id> --drift-profile <drift-profile-path> "
            "--energy-profile <energy-profile-path> --command-limits <command-limits-path> "
            "--run-root <run-artifact-root>"
        )
        print(f"next: {next_command}", file=sys.stderr)
    return 0


def _check_profiles(manifest: SessionManifest, profile_files: list[str], *, grant_state: str,
                    covered: set[str] = frozenset()) -> tuple:
    """Verify every declared profile has an admissible file; return the loaded profiles.

    ``covered`` (Task B item 5) are declared hashes already accounted for by
    the flight calibration files (``calibration_hashes`` of the loaded
    energy/drift/limits/site), so they need no ``--profile-file``."""
    declared = set(manifest.profile_sha256s) - set(covered)
    if not declared:
        return ()
    seen = set()
    loaded = []
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
        loaded.append(profile)
    missing = declared - seen
    if missing:
        raise GateError(f"no --profile-file supplied for declared profile_sha256(s): {sorted(missing)}")
    return tuple(loaded)


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
                          manifest: SessionManifest, clock_domain_id: str | None,
                          now_monotonic_ms: int) -> tuple[RoomSite, tuple]:
    """Verify --site against the manifest and every trigger's assurance.

    Returns the parsed site and the uniquely current assurance per trigger --
    the exact evidence the live session later adopts into its supervisor's
    clock domain (``live_session.adopt_assurances``).
    """
    if site_path is None:
        raise GateError("--site is required to verify site assurance before a live flight")
    if not clock_domain_id:
        raise GateError("--clock-domain-id is required to verify site assurance before a live flight")
    try:
        site: RoomSite = parse_room_site(json.loads(Path(site_path).read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise GateError(f"--site could not be loaded: {exc}") from exc
    # Fix round 1 (Q6): the site-assurance gate previously checked the *shape*
    # of --site's terminal contingencies without ever binding --site itself to
    # the manifest's own declared site_sha256/tag_map_sha256 -- an operator
    # could point --site at any site file at all. Refuse unless it is the same
    # site the manifest was authorized against.
    if site.cube_site_sha256 != manifest.site_sha256 or site.tag_map_sha256 != manifest.tag_map_sha256:
        raise GateError(
            "--site does not match the manifest's bound site_sha256/tag_map_sha256"
        )
    assurances = tuple(_load_assurance(path) for path in assurance_paths)
    verified = []
    for trigger in ContingencyTrigger:
        entries = [entry for entry in site.terminal_contingencies if entry.trigger == trigger]
        entries_by_id = {entry.contingency_id: entry for entry in entries}
        # Fix round 1 (Q6): an assurance not recorded for *this* run's
        # session_id (manifest.run_id) must never count toward clearing a
        # trigger -- otherwise a stale or another session's assurance file
        # could authorize this one.
        matching = [a for a in assurances
                   if a.clock_domain_id == clock_domain_id and a.contingency_id in entries_by_id
                   and a.session_id == manifest.run_id]
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
        verified.extend(current)
    return site, tuple(verified)


def _load_flight_calibration(args: argparse.Namespace):
    """The measured drift/energy evidence and calibrated command limits the
    SafetySupervisor flies with. Never synthesized: a missing or malformed
    file refuses before any run artifact, provider or client exists."""
    if not args.drift_profile or not args.energy_profile:
        raise GateError("--drift-profile and --energy-profile are required for a flight-capable live session")
    try:
        return (live_session.load_drift_profile(args.drift_profile),
                live_session.load_energy_profile(args.energy_profile),
                live_session.load_command_limits(args.command_limits))
    except live_session.LiveSessionError as exc:
        raise GateError(str(exc)) from exc


def _load_bound_tag_map(args: argparse.Namespace, manifest: SessionManifest):
    """The default flight stack's tag map, bound to the manifest's ``tag_map_sha256``
    (``--site`` is already bound to the same hash)."""
    if not args.tag_map:
        raise GateError("--tag-map is required for the default real-Tello flight stack")
    try:
        return live_stack.load_bound_tag_map(args.tag_map, expected_sha256=manifest.tag_map_sha256)
    except (live_stack.LiveStackError, OSError) as exc:
        raise GateError(str(exc)) from exc


def _cmd_live(
    args: argparse.Namespace, *,
    drone_probe: Callable[[], connectivity.ProbeResult] | None,
    provider_probe: Callable[[], connectivity.ProbeResult] | None,
    client_factory: Callable[..., "live_session.FlightStack"] | None,
    jev_factory: Callable[[], object],
    detector_factory: Callable[[], object],
    clock: Callable[[], float],
    wall_clock: Callable[[], datetime],
    request_text: str = DEFAULT_REQUEST_TEXT,
    on_stack: Callable[..., "live_session.FlightStack"] | None = None,
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
    # Fix round 1 (Q7): wall time for a flight-capable live invocation (grant
    # expiry) comes from an injected clock, never an operator-supplied --now --
    # a back-dated --now could otherwise admit an expired PROMOTED/TRIAL grant.
    now = wall_clock()
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

    # The grant registry is loaded in both sub-modes: its matching grants bind
    # the run's session row (the SafetySupervisor reads them there). Loading
    # it is not admission -- connectivity-only still admits nothing.
    try:
        registry = capabilities.load_registry(args.grant_registry)
    except (capabilities.GrantRegistryError, OSError) as exc:
        print(f"refused: grant registry could not be loaded: {exc}", file=sys.stderr)
        return 2
    if not args.check_connectivity_only:
        try:
            winning_grant = capabilities.admit_live(registry, manifest=manifest, now=now)
        except capabilities.GrantRegistryError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        try:
            # Fix round 1 (Q6): site-assurance freshness is a monotonic-clock-domain
            # comparison (assured_monotonic_ms/expires_monotonic_ms) -- evaluate it
            # against the injected monotonic `clock`, the same one connectivity below
            # uses, never wall-clock epoch milliseconds (a different clock domain
            # entirely; a real monotonic assurance would never compare sanely against it).
            site, verified_assurances = _check_site_assurance(
                args.site, args.assurance, manifest=manifest, clock_domain_id=args.clock_domain_id,
                now_monotonic_ms=int(clock() * 1000))
            drift_profile, energy_profile, limits = _load_flight_calibration(args)
            # Task B item 5: one grant binds the calibration profile files AND the
            # exact energy/drift/limits/site flown with; the manifest's declared
            # profile_sha256s may therefore include those flight hashes, which the
            # flight files just loaded (not a --profile-file) account for.
            calibration_profiles = _check_profiles(
                manifest, args.profile_file, grant_state=winning_grant.state,
                covered=set(calibration_hashes(energy_profile, drift_profile, limits, site)))
            tag_map = _load_bound_tag_map(args, manifest) if client_factory is None else None
        except GateError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2

    # Fix round 2 (Q2): open the durable run artifact before the connectivity
    # preflight, not just print its result to stderr. RunArtifacts.open is
    # idempotent for a run_id already opened with the same manifest bytes and
    # capability binding, so a connectivity-only diagnostic followed by the
    # flight on the same manifest share one run.
    try:
        run = artifacts.RunArtifacts.open(
            args.run_root, manifest, now=now, request_text=request_text,
            capabilities=live_session.session_capabilities(registry, manifest))
    except (ArtifactError, OSError) as exc:
        print(f"refused: run artifact could not be opened: {exc}", file=sys.stderr)
        return 2
    link = None
    try:
        # Past every gate: the live defaults are built only now. The default
        # drone probe and flight stack share one Tello connection (DJITelloPy's
        # receivers are process-global), made on the probe's first call.
        if drone_probe is None or client_factory is None:
            link = live_stack.TelloLink(local_ip=args.local_ip, clock=clock)
        if drone_probe is None:
            drone_probe = link.probe
        if provider_probe is None:
            def provider_probe():
                return live_stack.openrouter_provider_probe(clock=clock)
        # Past every capability/consent/(site) gate. A motors-off connectivity
        # preflight always runs before takeoff -- proving fresh drone telemetry
        # and provider reachability concurrently -- whether this is the explicit
        # diagnostic or the last check before a real flight.
        result = connectivity.check(
            drone_probe=drone_probe, provider_probe=provider_probe,
            max_age_s=args.connectivity_max_age_s, timeout_s=args.connectivity_timeout_s, clock=clock,
        )
        run.append_event("CONNECTIVITY_CHECKED", {
            "ready": result.ready, "drone_ready": result.drone_ready, "provider_ready": result.provider_ready,
            "drone_identity": result.drone_identity, "provider_identity": result.provider_identity,
            "drone_checked_at": result.drone_checked_at, "provider_checked_at": result.provider_checked_at,
            "drone_error": result.drone_error, "provider_error": result.provider_error,
            "max_age_s": args.connectivity_max_age_s, "timeout_s": args.connectivity_timeout_s,
            "check_connectivity_only": bool(args.check_connectivity_only),
        }, now=now)
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

        # Fix round 3/4 (Q2): past every gate and a ready preflight. Jev and
        # the detector are constructed (only ever real if a caller overrides
        # the refuse-by-default factories) and their own reported identities
        # are durably recorded before anything can fly.
        try:
            acceptance = AcceptancePolicy(**json.loads(getattr(args, "jev_acceptance_json", None) or "{}"))
        except (TypeError, ValueError) as exc:
            print(f"refused: invalid --jev-acceptance-json: {exc}", file=sys.stderr)
            return 2
        try:
            judge = jev_factory()
            detector = detector_factory()
        except Exception as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        try:
            for role, constructed in (("jev", judge), ("detector", detector)):
                identity = getattr(constructed, "identity", None)
                if not isinstance(identity, str) or not identity:
                    print(f"refused: the constructed {role} object exposes no nonblank string .identity",
                          file=sys.stderr)
                    return 2
                run.record_resolved_identity(role=role, resolved=identity, observed_at=now)
        except (ArtifactError, OSError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        if jev_factory is _default_jev:
            # One timed (paid) micro-decision: live 2026-09-25 the provider's
            # per-choice latency doubled during the evening with no code
            # change; the operator sees it here instead of mid-flight. Only
            # the real judge is probed -- a scripted or fixture judge must
            # never consume an extra call. Informational: never gates takeoff.
            jev_latency_s, jev_warning = live_stack.jev_latency_probe(judge, clock=clock)
            if jev_latency_s is not None:
                print(f"jev_latency_s={jev_latency_s:.2f}", file=sys.stderr)
            if jev_warning:
                print(f"warning: {jev_warning}", file=sys.stderr)

        def before_takeoff() -> None:
            # Fix round 2/4 (Q2): the preflight was fresh when it ran; the
            # supervisor bootstrap and goal compilation have run since. Re-check
            # it immediately before supervisor.start and refuse takeoff on a
            # preflight that has since expired -- never ride a stale verdict.
            takeoff_now = clock()
            drone_expired = (takeoff_now - result.drone_checked_at) > args.connectivity_max_age_s
            provider_expired = (takeoff_now - result.provider_checked_at) > args.connectivity_max_age_s
            if drone_expired or provider_expired:
                run.append_event("CONNECTIVITY_EXPIRED_BEFORE_TAKEOFF", {
                    "drone_checked_at": result.drone_checked_at,
                    "provider_checked_at": result.provider_checked_at,
                    "rechecked_at": takeoff_now, "max_age_s": args.connectivity_max_age_s,
                }, now=wall_clock())
                raise live_session.LiveSessionError("connectivity preflight expired before takeoff")
            run.append_event("TAKEOFF_AUTHORIZED", {
                "drone_identity": result.drone_identity, "provider_identity": result.provider_identity,
                "drone_checked_at": result.drone_checked_at, "provider_checked_at": result.provider_checked_at,
                "rechecked_at": takeoff_now, "max_age_s": args.connectivity_max_age_s,
            }, now=wall_clock())

        if client_factory is None:
            client_factory = live_stack.TelloFlightStackFactory(
                link=link, tag_map=tag_map, run_id=manifest.run_id, run_dir=run.root,
                max_duration_s=manifest.max_duration_s,
                minimum_battery=0 if args.disable_battery_guard else 40,
                record_video=args.record_video)
        # Q1: arm the operator's motor-stop hotkey for the whole flight, from
        # stack construction until landing/close (stopped in the finally
        # below). See _ArmedFlightStackFactory.
        armed_factory = _ArmedFlightStackFactory(client_factory, on_stack=on_stack)
        # One bounded (paid, authorised-only, real-Jev-only) GLM rewrite of
        # the operator's words into the goal taxonomy's phrasing. Fail-open:
        # any failure flies the original text. The operator answer below
        # keeps the original words.
        flight_request_text = _normalize_request_text(
            run, request_text=request_text, wall_clock=wall_clock,
            authorised=bool(args.i_authorise_paid_calls) and jev_factory is _default_jev)
        try:
            try:
                outcome = live_session.run_count_return(
                    store=run.store, manifest=manifest, site=site, drift_profile=drift_profile,
                    energy_profile=energy_profile, limits=limits, calibration_profiles=calibration_profiles,
                    flight_stack_factory=armed_factory, judge=judge, detector=detector,
                    assurances=verified_assurances, request_text=flight_request_text,
                    frames_dir=run.root / "frames", clock=clock, wall_clock=wall_clock,
                    before_takeoff=before_takeoff, acceptance=acceptance,
                    disable_battery_guard=args.disable_battery_guard,
                )
            except live_session.LiveSessionError as exc:
                try:
                    run.append_event("LIVE_SESSION_REFUSED", {"reason": str(exc)}, now=wall_clock())
                except (ArtifactError, OSError):
                    pass
                print(f"refused: {exc}", file=sys.stderr)
                return 2
        finally:
            armed_factory.stop()
        landing = outcome.landing
        try:
            run.finalize({
                "state": outcome.terminal_state.value,
                "completed_at": landing.completed_at if landing is not None else None,
                "landing_state": landing.state if landing is not None else None,
                "landing_reason": landing.reason if landing is not None else None,
                "took_off": outcome.took_off, "ledger_status": outcome.ledger_status, "reason": outcome.reason,
            }, now=wall_clock())
        except (ArtifactError, OSError) as exc:
            print(f"error: session ended {outcome.terminal_state.value} but terminal.json could not be "
                  f"finalized: {exc}", file=sys.stderr)
            return 1
        # Past the durable terminal record: one (paid, authorised-only) GLM
        # call phrases the findings for the operator. It never raises and
        # never touches terminal.json/summary.json; see sentinel.room.operator_answer.
        # Paid only with the flag AND the real Jev: an injected (test/fake)
        # judge means an offline run, which gets the deterministic answer.
        _write_operator_answer(run, manifest.run_id, request_text=request_text,
                               authorised=bool(args.i_authorise_paid_calls) and jev_factory is _default_jev)
        _write_capture_overlays(run)
        print(json.dumps({"run_id": manifest.run_id, "terminal_state": outcome.terminal_state.value}))
        return 0
    finally:
        if link is not None:
            link.close()  # connectivity-only ends here too: the probe's connection is closed
        if getattr(args, "record_video", False):
            try:
                live_stack.write_recording_status(run.root,
                    error=link.recording_error() if link is not None else None)
            except OSError as exc:
                print(f"recording status unavailable: {exc}", file=sys.stderr)
        run.close()


def _normalize_request_text(run, *, request_text: str, authorised: bool,
                            wall_clock: Callable[[], datetime], normalize=None) -> str:
    """The request text to fly: GLM's rewrite when authorised and it worked,
    else the original. Never raises; a changed text is recorded as
    ``REQUEST_NORMALIZED`` with both texts."""
    if not authorised:
        return request_text
    try:
        if normalize is None:
            from sentinel.room import request_normalizer

            normalize = request_normalizer.normalize_request
        live_stack.provider_environ()  # fill OPENROUTER_API_KEY from .env, as Jev does
        normalized, model = normalize(request_text, authorised=True)
        if not isinstance(normalized, str) or not normalized.strip():
            raise ValueError("empty normalized request")
        normalized = normalized.strip()
        if normalized == request_text:
            return request_text
        run.append_event("REQUEST_NORMALIZED", {"original": request_text, "normalized": normalized,
                                                "model": model}, now=wall_clock())
    except Exception as exc:  # fail-open: the mission never blocks on this step
        print(f"request normalization unavailable, flying the original request: {exc}"[:400],
              file=sys.stderr)
        return request_text
    print(f"request normalized: {request_text!r} -> {normalized!r}", file=sys.stderr)
    return normalized


def _write_operator_answer(run, run_id: str, *, request_text: str, authorised: bool) -> None:
    """Best effort, after terminal.json: never changes the CLI's outcome."""
    try:
        from sentinel.room import operator_answer

        terminal = json.loads((run.root / "terminal.json").read_text(encoding="utf-8"))
        if authorised:
            live_stack.provider_environ()  # fill OPENROUTER_API_KEY from .env, as Jev does
        document = operator_answer.finalize_operator_answer(
            run.root, request_text=request_text, events=run.store.events(run_id), terminal=terminal,
            authorised=authorised)
        if document is not None:
            print(f"operator answer ({document['source']}): {document['operator_answer']}", file=sys.stderr)
    except Exception as exc:  # the answer is a courtesy; the run is already recorded
        print(f"operator answer unavailable: {exc}", file=sys.stderr)


def _write_capture_overlays(run) -> None:
    """Best effort, after terminal.json: copies of the captures with their
    verified VLM boxes drawn on; never changes the CLI's outcome."""
    try:
        from sentinel.room.capture_overlays import render_run_overlays

        written = render_run_overlays(run.root)
        if written:
            print(f"capture overlays: {len(written)} in {written[0].parent}", file=sys.stderr)
    except Exception as exc:
        print(f"capture overlays unavailable: {exc}", file=sys.stderr)


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
    preview_parser.add_argument("--site-sha256", default=None)
    preview_parser.add_argument("--tag-map-sha256", default=None)
    # Task B item 5: the flight files one grant binds; preview derives the
    # manifest's profile_sha256s and site/tag-map hashes from them.
    preview_parser.add_argument("--site", default=None)
    preview_parser.add_argument("--drift-profile", default=None)
    preview_parser.add_argument("--energy-profile", default=None)
    preview_parser.add_argument("--command-limits", default=None)
    preview_parser.add_argument("--profile-file", action="append", default=[])
    # Task B: defaults to the production camera model's hash (the one
    # wiring.make_count_geometry places detections with).
    preview_parser.add_argument("--camera-sha256", default=None)
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
    # Live 2026-09-26: with no battery gate at all the packs drained to 9 %
    # and 14 % (l3-20260926-111405, -114306), the first ending on a
    # motor-stop mid-turn. The operator's remedy is a REACTIVE floor, not
    # the predictive one: worst-case projections refuse takeoff around 70 %
    # and turn back mid-mission, which cripples a demo, while the reactive
    # gate (ReserveGuard with the predictive guard disabled) flies normally
    # and orders the safe return once a validated reading drops below the
    # profile's 22 % landing floor. So the default stays disable_battery_guard
    # = True (reactive floor only); --enable-battery-guard opts into the full
    # predictive projections.
    battery_guard = live_parser.add_mutually_exclusive_group()
    battery_guard.add_argument("--disable-battery-guard", dest="disable_battery_guard",
                               action="store_true", default=True,
                               help="reactive-floor-only battery gating (default): fly normally, safe return below "
                                    "the profile's landing floor")
    battery_guard.add_argument("--enable-battery-guard", dest="disable_battery_guard",
                               action="store_false",
                               help="operator opt-in: full projected battery cost and floor admission in Live mode")
    live_parser.add_argument("--site", default=None)
    live_parser.add_argument("--assurance", action="append", default=[])
    live_parser.add_argument("--clock-domain-id", default=None)
    # Fix round 4 (Q2): the SafetySupervisor's measured calibration evidence.
    # Required for a flight-capable session (not for --check-connectivity-only);
    # the flight-calibration grant binds calibration_hashes of exactly these.
    live_parser.add_argument("--drift-profile", default=None)
    live_parser.add_argument("--energy-profile", default=None)
    live_parser.add_argument("--command-limits", default=None)
    # Task A: the default real-Tello flight stack localises from this tag map;
    # its file bytes must hash to the manifest's tag_map_sha256.
    live_parser.add_argument("--tag-map", default=None)
    # The laptop's address on the Tello Wi-Fi; omitted, sentinel.network selects
    # the one reachable Tello adapter itself.
    live_parser.add_argument("--local-ip", default=None)
    live_parser.add_argument("--check-connectivity-only", action="store_true")
    live_parser.add_argument("--connectivity-max-age-s", type=float, default=5.0)
    # Task V2: the room loop's per-kind Jev acceptance thresholds, as a JSON
    # object of AcceptancePolicy fields (default: the room-loop policy).
    live_parser.add_argument("--jev-acceptance-json", default=None)
    live_parser.add_argument("--connectivity-timeout-s", type=float, default=5.0)
    # The durable run-artifact root (RunArtifacts.open lays out
    # root/manifest.run_id/{manifest.json, raw.jsonl, session.sqlite3, ...}).
    # Required for every flight-capable live invocation, including
    # --check-connectivity-only: fix round 2 (Q2) -- the connectivity
    # preflight's result must be recorded durably, not only printed to
    # stderr, so an operator (or room_verify_artifact.py) can later confirm
    # what a session actually observed before refusing or flying.
    live_parser.add_argument("--run-root", required=True)
    live_parser.add_argument("--record-video", action="store_true",
                             help="save the existing Tello H.264 stream beside this run")
    # Fix round 1 (Q7): live never takes an operator-supplied --now. A
    # flight-capable invocation's wall time (grant expiry) comes only from
    # main()'s injected `wall_clock`, which tests can override but which a
    # real invocation always resolves to actual wall time -- a back-dated
    # --now could otherwise admit an expired PROMOTED/TRIAL grant or stale
    # site assurance.
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
    drone_probe: Callable[[], connectivity.ProbeResult] | None = None,
    provider_probe: Callable[[], connectivity.ProbeResult] | None = None,
    client_factory: Callable[..., "live_session.FlightStack"] | None = None,
    jev_factory: Callable[[], object] = _default_jev,
    detector_factory: Callable[[], object] = _default_detector,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    request_text: str = DEFAULT_REQUEST_TEXT,
    on_stack: Callable[..., "live_session.FlightStack"] | None = None,
) -> int:
    """``client_factory(tracker=..., clock=..., site=...)`` returns the connected
    ``live_session.FlightStack``; it is called only after every gate, the
    preflight, and Jev/detector identity recording. ``None`` for a probe or
    ``client_factory`` selects the ``sentinel.room.live_stack`` default."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "live":
        return _cmd_live(args, drone_probe=drone_probe, provider_probe=provider_probe,
                         client_factory=client_factory, jev_factory=jev_factory,
                         detector_factory=detector_factory, clock=clock, wall_clock=wall_clock,
                         request_text=request_text, on_stack=on_stack)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
