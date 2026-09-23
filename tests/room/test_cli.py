"""Tests for scripts/room_session.py and src/sentinel/room/connectivity.py
(Plan 06 Task 4: the manifest-bound session CLI).

Everything here is OFFLINE: no provider, network, drone client or hardware
object is constructed anywhere in this file. Live mode is exercised through
its injected drone_probe/provider_probe/client_factory/jev_factory/
detector_factory parameters, or through the Task A live defaults
(``sentinel.room.live_stack``) with their lowest transport seams replaced:
an autouse fixture makes any real Tello client build or provider HTTP call
raise, and the tests that exercise the defaults swap in the fake DJITelloPy
transport from ``tests/room/test_live_stack.py``.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.room_session as cli
from sentinel.room import artifacts, calibration, capabilities, connectivity, live_stack
from sentinel.room.models import CapabilityGrant, canonical_grant_sha256
from sentinel.room.site import room_site_document
from tests.room import test_live_session as live_fakes
from tests.room import test_live_stack as stack_fakes

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "room_agent" / "v1"
SITE_PATH = FIXTURE_DIR / "semantic-site.json"
REPLAY_MANIFEST_PATH = FIXTURE_DIR / "manifest.json"

# Fix round 1 (Q6): the site-assurance gate now binds --site to the manifest's
# declared site_sha256/tag_map_sha256, so any test that exercises that gate to
# success must use *this* fixture's real hashes, not an arbitrary placeholder.
_SITE_DOCUMENT = json.loads(SITE_PATH.read_text(encoding="utf-8"))
SITE_SHA256 = _SITE_DOCUMENT["cube_site_sha256"]
TAG_MAP_SHA256 = _SITE_DOCUMENT["tag_map_sha256"]

NOW = "2026-09-23T12:00:00+00:00"


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


TAG_MAP_FIXTURE = Path(__file__).resolve().parents[2] / stack_fakes.TAG_MAP_FIXTURE


@pytest.fixture(autouse=True)
def _forbid_real_transports(monkeypatch):
    """The live defaults reach hardware and the provider only through these
    seams; in this file every one of them refuses unless a test swaps in a fake."""

    def no_tello(**_kwargs):
        raise RuntimeError("test forbids building a real Tello client")

    def no_http(*_args, **_kwargs):
        raise RuntimeError("test forbids provider network calls")

    monkeypatch.setattr(live_stack, "build_tello_client", no_tello)
    monkeypatch.setattr(live_stack, "_http_get", no_http)
    monkeypatch.setattr(live_stack, "provider_environ", lambda: {})


def _fake_provider(monkeypatch):
    monkeypatch.setattr(live_stack, "provider_environ", lambda: {
        live_stack.PROVIDER_KEY_ENV: "test-key", live_stack.PROVIDER_MODEL_ENV: "google/gemini-test"})
    monkeypatch.setattr(live_stack, "_http_get",
                        lambda url, *, headers, timeout: stack_fakes.FakeResponse(200))


def _fake_tello(monkeypatch, **tello_kwargs):
    transport = stack_fakes.FakeTransport(**tello_kwargs)
    monkeypatch.setattr(live_stack, "build_tello_client", lambda **_kwargs: transport())
    return transport


def _preview_argv(tmp_path, **overrides) -> list[str]:
    args = {
        "--target-mode": "live",
        "--run-id": "run-0001",
        "--capability": "count_return",
        "--grant-registry": str(tmp_path / "registry.json"),
        "--capability-grant-sha256": _hash("grant"),
        "--capability-grant-state": "PROMOTED",
        "--code-revision": "abc123",
        "--integration-revision": "def456",
        "--request-revision": "req-1",
        "--goal-parser-revision": "goal-1",
        "--site-sha256": _hash("site"),
        "--tag-map-sha256": _hash("tagmap"),
        "--camera-sha256": _hash("camera"),
        "--aircraft-id": "aircraft-1",
        "--firmware-version": "1.0.0",
        "--requested-jev-identity": "jev-vendor-x",
        "--requested-detector-identity": "detector-vendor-y",
        "--max-duration-s": "120.0",
        "--max-distance-cm": "500.0",
        "--max-recovery-attempts": "2",
        "--now": NOW,
    }
    args.update(overrides)
    argv = ["preview"]
    profiles = args.pop("--profile", None)
    consent_refs = args.pop("--consent-reference", None)
    flags = args.pop("__flags__", ())
    for key, value in args.items():
        argv.extend([key, value])
    for profile in profiles or ():
        argv.extend(["--profile", profile])
    for ref in consent_refs or ():
        argv.extend(["--consent-reference", ref])
    argv.extend(flags)
    return argv


def _empty_registry(tmp_path) -> Path:
    path = tmp_path / "registry.json"
    capabilities.write_registry(path, capabilities.empty_registry())
    return path


def _promoted_grant(*, capability, site_sha256, aircraft_id, profile_sha256s):
    grant = CapabilityGrant(
        capability=capability, state="PROMOTED", site_sha256=site_sha256, aircraft_id=aircraft_id,
        profile_sha256s=tuple(sorted(profile_sha256s)), evidence_ids=("ev-1",),
        expires_at="2099-01-01T00:00:00+00:00", grant_sha256="",
    )
    return replace(grant, grant_sha256=canonical_grant_sha256(grant))


def _registry_with_grant(tmp_path, grant) -> Path:
    registry = capabilities.empty_registry()
    registry = capabilities.GrantRegistry(schema_version=registry.schema_version, grants=(grant,))
    path = tmp_path / "registry.json"
    capabilities.write_registry(path, registry)
    return path


def _promoted_profile(*, site_sha256, aircraft_id):
    return calibration.CalibrationProfile(
        schema_version=calibration.SCHEMA_VERSION, kind="flight", state="PROMOTED",
        site_sha256=site_sha256, aircraft_id=aircraft_id, firmware_version="1.0.0",
        camera_sha256=_hash("camera"), tag_map_sha256=_hash("tagmap"), speeds_cm_s=(20,),
        battery=calibration.BatteryBound(0.1, 0.02, 1.0, 15.0), pose_envelope=(),
        training_run_ids=("t",), holdout_run_ids=("h",),
    )


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_prints_canonical_manifest_and_writes_manifest_out(tmp_path, capsys):
    _empty_registry(tmp_path)
    manifest_out = tmp_path / "manifest.json"
    exit_code = cli.main(_preview_argv(tmp_path, **{
        "--consent-reference": ["consent-1"], "__flags__": ["--manifest-out", str(manifest_out)],
    }))
    out = capsys.readouterr().out.strip()
    assert exit_code == 0
    document = json.loads(out)
    assert document["run_id"] == "run-0001"
    assert document["mode"] == "live"
    assert manifest_out.read_bytes() == out.encode("utf-8")


def test_preview_omits_manifest_out_creates_nothing(tmp_path, capsys):
    _empty_registry(tmp_path)
    cli.main(_preview_argv(tmp_path, **{"--consent-reference": ["consent-1"]}))
    assert list(tmp_path.iterdir()) == [tmp_path / "registry.json"]


def test_preview_refuses_an_unavailable_capability(tmp_path, capsys):
    _empty_registry(tmp_path)
    exit_code = cli.main(_preview_argv(tmp_path, **{"--capability": "following"}))
    assert exit_code == 2
    assert "not available" in capsys.readouterr().err


def test_preview_rejects_an_unknown_capability_via_argparse(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(_preview_argv(tmp_path, **{"--capability": "bogus"}))


def test_preview_requires_trial_protocol_and_evidence_destination_together(tmp_path, capsys):
    _empty_registry(tmp_path)
    exit_code = cli.main(_preview_argv(tmp_path, **{"__flags__": ["--trial-protocol", "trial-1"]}))
    assert exit_code == 2
    assert "together" in capsys.readouterr().err


def test_preview_reports_a_missing_capability_gate_when_registry_has_no_matching_grant(tmp_path, capsys):
    _empty_registry(tmp_path)
    cli.main(_preview_argv(tmp_path, **{"--consent-reference": ["consent-1"]}))
    err = capsys.readouterr().err
    assert "missing gate: capability admission" in err


def test_preview_reports_a_missing_consent_gate_when_no_consent_references_given(tmp_path, capsys):
    _empty_registry(tmp_path)
    cli.main(_preview_argv(tmp_path))
    err = capsys.readouterr().err
    assert "missing gate: consent" in err


def test_preview_reports_no_missing_gates_and_prints_the_next_command_when_admissible(tmp_path, capsys):
    site_sha256, aircraft_id = _hash("site"), "aircraft-1"
    grant = _promoted_grant(capability="count_return", site_sha256=site_sha256, aircraft_id=aircraft_id,
                            profile_sha256s=())
    _registry_with_grant(tmp_path, grant)
    manifest_out = tmp_path / "manifest.json"
    exit_code = cli.main(_preview_argv(tmp_path, **{
        "--capability-grant-sha256": grant.grant_sha256, "--consent-reference": ["consent-1"],
        "__flags__": ["--manifest-out", str(manifest_out)],
    }))
    out, err = capsys.readouterr()
    assert exit_code == 0
    assert "missing gate" not in err
    assert "next: python scripts/room_session.py live --execute" in err
    assert str(manifest_out) in err


def test_preview_next_command_actually_parses_as_a_live_invocation(tmp_path, capsys):
    # Fix round 1 (S1/Q4): room_session.py's parser uses positional subcommands
    # (preview|live|replay|simulate), not a --mode flag -- the old "--mode live
    # --execute" text argparse-rejected with "unrecognized arguments: --mode".
    # Feed the printed command's own tokens back through the real parser to
    # prove it is actually executable, not just string-matched.
    site_sha256, aircraft_id = _hash("site"), "aircraft-1"
    grant = _promoted_grant(capability="count_return", site_sha256=site_sha256, aircraft_id=aircraft_id,
                            profile_sha256s=())
    _registry_with_grant(tmp_path, grant)
    manifest_out = tmp_path / "manifest.json"
    exit_code = cli.main(_preview_argv(tmp_path, **{
        "--capability-grant-sha256": grant.grant_sha256, "--consent-reference": ["consent-1"],
        "__flags__": ["--manifest-out", str(manifest_out)],
    }))
    assert exit_code == 0
    err = capsys.readouterr().err
    next_line = next(line for line in err.splitlines() if line.startswith("next: "))
    command_text = next_line[len("next: "):]
    tokens = command_text.split()  # no quoting in the printed command; a Windows path can contain "\"
    assert tokens[:2] == ["python", "scripts/room_session.py"]
    parsed = cli.build_parser().parse_args(tokens[2:])  # must not raise/SystemExit
    assert parsed.mode == "live"
    assert parsed.execute is True
    assert parsed.manifest == str(manifest_out)
    assert parsed.capability == "count_return"
    assert parsed.grant_registry == str(tmp_path / "registry.json")


def test_preview_refuses_a_manifest_the_json_schema_rejects(tmp_path, capsys):
    # Fix round 1 (Q5): SessionManifest's own __post_init__ never enforced the
    # schema's max_duration_s > 0 bound, so preview could print and
    # --manifest-out write a manifest+digest that live's own parse_manifest
    # (which does apply the schema) would refuse -- an operator would only
    # discover the mismatch after copying the printed live command.
    _empty_registry(tmp_path)
    manifest_out = tmp_path / "manifest.json"
    exit_code = cli.main(_preview_argv(tmp_path, **{
        "--max-duration-s": "0.0", "__flags__": ["--manifest-out", str(manifest_out)],
    }))
    assert exit_code == 2
    assert "refused" in capsys.readouterr().err
    assert not manifest_out.exists()


def test_preview_invalid_json_option_is_refused(tmp_path, capsys):
    _empty_registry(tmp_path)
    exit_code = cli.main(_preview_argv(tmp_path, **{"__flags__": ["--home-policy-json", "{not json"]}))
    assert exit_code == 2
    assert "invalid JSON" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# live: gating (manifest authorization, capability, consent)
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path, **overrides) -> tuple[Path, str]:
    import contextlib
    import io

    if not (tmp_path / "registry.json").exists():
        _empty_registry(tmp_path)
    manifest_out = tmp_path / "manifest.json"
    # Build via a throwaway buffer, never the pytest capsys fixture: this
    # helper's own preview output must not leak into a caller's later
    # capsys.readouterr() assertions about the *live* command it runs next.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        cli.main(_preview_argv(tmp_path, **{
            "--consent-reference": ["consent-1"], "__flags__": ["--manifest-out", str(manifest_out)], **overrides,
        }))
    manifest = artifacts.parse_manifest(manifest_out.read_bytes())
    digest = manifest.to_document()[artifacts.DIGEST_FIELD]
    return manifest_out, digest


def _live_argv(manifest_path, digest, tmp_path, **overrides) -> list[str]:
    # Fix round 1 (Q7): live no longer takes --now (main()'s injected wall_clock
    # takes its place; every grant/assurance fixture here is valid against real
    # wall time regardless, so the tests need no fixed wall_clock override).
    args = {
        "--manifest": str(manifest_path), "--authorization-digest": digest,
        "--capability": "count_return", "--grant-registry": str(tmp_path / "registry.json"),
        "--run-root": str(tmp_path / "runs"),
    }
    args.update(overrides)
    flags = args.pop("__flags__", ["--execute"])
    argv = ["live", *flags]
    for key, value in args.items():
        argv.extend([key, value])
    return argv


def test_live_requires_execute_flag(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["live", "--manifest", str(manifest_path), "--authorization-digest", digest,
                 "--capability", "count_return", "--grant-registry", str(tmp_path / "registry.json")])


def test_live_refuses_a_digest_mismatch(tmp_path, capsys):
    manifest_path, _ = _write_manifest(tmp_path)
    argv = _live_argv(manifest_path, "0" * 64, tmp_path)
    exit_code = cli.main(argv)
    assert exit_code == 2
    assert "authorization_digest" in capsys.readouterr().err


def test_live_refuses_a_manifest_whose_mode_is_not_flight_capable(tmp_path, capsys):
    manifest_path, digest = _write_manifest(tmp_path, **{"--target-mode": "simulate"})
    argv = _live_argv(manifest_path, digest, tmp_path)
    exit_code = cli.main(argv)
    assert exit_code == 2
    assert "not a flight-capable live mode" in capsys.readouterr().err


def test_live_refuses_an_unavailable_capability(tmp_path, capsys):
    manifest_path, digest = _write_manifest(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path, **{"--capability": "closer_inspection"})
    exit_code = cli.main(argv)
    assert exit_code == 2
    assert "not available" in capsys.readouterr().err


def test_live_refuses_without_consent(tmp_path, capsys):
    manifest_path, digest = _write_manifest(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path, **{"__flags__": ["--execute", "--check-connectivity-only"]})
    exit_code = cli.main(argv)
    assert exit_code == 2
    assert "refused" in capsys.readouterr().err


def _consent_flags(tmp_path):
    from sentinel.survey.consent import record_consent
    from sentinel.survey.store import SurveyStore

    consent_db = tmp_path / "consent.sqlite3"
    store = SurveyStore.open(consent_db)
    record_consent(store, location="office", consent_date="2026-09-23", recorded_by="operator")
    store.connection.close()
    return ["--consent-db", str(consent_db), "--location", "office", "--date", "2026-09-23",
           "--i-authorise-paid-calls"]


# ---------------------------------------------------------------------------
# live: connectivity-only (the sole capability-grant exemption)
# ---------------------------------------------------------------------------


def _fake_probe(identity="drone-1", checked_at=None):
    def probe():
        return connectivity.ProbeResult(identity=identity, checked_at=checked_at if checked_at is not None else time.monotonic())
    return probe


def _fake_collaborator(identity="vendor-x"):
    # Fix round 3 (Q2): jev_factory()/detector_factory()'s returned object
    # must expose a nonblank string .identity -- _cmd_live records it
    # durably via RunArtifacts.record_resolved_identity before reaching its
    # own unwired-controller boundary.
    from types import SimpleNamespace
    return lambda: SimpleNamespace(identity=identity)


def test_connectivity_only_refuses_when_the_default_probes_reach_nothing(tmp_path, capsys):
    manifest_path, digest = _write_manifest(tmp_path)
    consent = _consent_flags(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path,
                      **{"__flags__": ["--execute", "--check-connectivity-only", *consent]})
    exit_code = cli.main(argv)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "not ready" in err


def test_connectivity_only_succeeds_with_injected_fresh_probes_and_never_touches_client_or_jev(tmp_path, capsys):
    manifest_path, digest = _write_manifest(tmp_path)
    consent = _consent_flags(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path,
                      **{"__flags__": ["--execute", "--check-connectivity-only", *consent]})

    def forbidden():
        raise AssertionError("connectivity-only must never touch client_factory/jev_factory")

    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=forbidden, jev_factory=forbidden)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.strip() == "CONNECTIVITY_READY"


def test_connectivity_only_skips_the_capability_grant_gate(tmp_path, capsys):
    # An empty registry (no matching grant at all) would refuse ordinary live
    # admission -- connectivity-only is the sole capability-grant exemption,
    # so it must still succeed here.
    manifest_path, digest = _write_manifest(tmp_path)
    _empty_registry(tmp_path)
    consent = _consent_flags(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path,
                      **{"__flags__": ["--execute", "--check-connectivity-only", *consent]})
    exit_code = cli.main(argv, drone_probe=_fake_probe(), provider_probe=_fake_probe())
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "CONNECTIVITY_READY"


def test_connectivity_only_refuses_a_stale_probe(tmp_path, capsys):
    manifest_path, digest = _write_manifest(tmp_path)
    consent = _consent_flags(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path,
                      **{"__flags__": ["--execute", "--check-connectivity-only", *consent,
                                       "--connectivity-max-age-s", "0.01"]})
    stale = _fake_probe("tello-1", checked_at=time.monotonic() - 10.0)
    exit_code = cli.main(argv, drone_probe=stale, provider_probe=_fake_probe("gemini-1"))
    assert exit_code == 2
    assert "not ready" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# live: full flight-capable gate chain (capability, profile, site assurance)
# ---------------------------------------------------------------------------


def _assurance_file(tmp_path, *, name, contingency_id, clock_domain_id):
    # Fix round 1 (Q6): assurance freshness is checked against the injected
    # monotonic `clock` (real time.monotonic() by default, the same clock
    # connectivity uses), not wall-clock epoch milliseconds -- so this fixture
    # must be assured in that same monotonic domain, just before *real* now,
    # well inside the fixture site's 5000ms max_assurance_age_ms window
    # (site_session_assurance semantics -- see sentinel.room.site.admit_terminal_contingency).
    now_monotonic_ms = int(time.monotonic() * 1000)
    document = {
        "assurance_id": f"a-{name}", "contingency_id": contingency_id, "session_id": "run-0001",
        "clock_domain_id": clock_domain_id, "state": "CURRENT", "assured_at": NOW,
        "assured_monotonic_ms": now_monotonic_ms - 100, "expires_monotonic_ms": now_monotonic_ms + 10 ** 9,
        "evidence_refs": ["ev-1"],
    }
    path = tmp_path / f"assurance-{name}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _full_gate_argv(tmp_path, manifest_path, digest, *, grant, profile_path, extra_flags=(), site_path=SITE_PATH):
    consent = _consent_flags(tmp_path)
    nav = _assurance_file(tmp_path, name="nav", contingency_id="NAVIGATION_UNAVAILABLE", clock_domain_id="cd-1")
    energy = _assurance_file(tmp_path, name="energy", contingency_id="RETURN_ENERGY_UNAVAILABLE", clock_domain_id="cd-1")
    return _live_argv(
        manifest_path, digest, tmp_path,
        **{
            "__flags__": [
                "--execute", *consent, "--profile-file", str(profile_path), "--site", str(site_path),
                "--assurance", str(nav), "--assurance", str(energy), "--clock-domain-id", "cd-1", *extra_flags,
            ],
        },
    )


def _flight_setup(tmp_path, *, flight_grant=True, calibration_flags=True, site=None):
    """Every file a flight-capable live invocation binds to: the admission
    grant (manifest profile hashes), the flight-calibration grant the
    SafetySupervisor itself verifies (calibration_hashes of the drift/energy/
    limits files and the site), the calibration profile, the drift/energy/
    limits evidence files, and a manifest bound to the fixture site (or *site*)."""
    site = site or live_fakes.fixture_site()
    site_path = tmp_path / "site.json"
    site_path.write_text(json.dumps(room_site_document(site)), encoding="utf-8")
    aircraft_id = live_fakes.AIRCRAFT_ID
    profile = _promoted_profile(site_sha256=site.cube_site_sha256, aircraft_id=aircraft_id)
    profile_hash = calibration.canonical_profile_sha256(profile)
    profile_path = tmp_path / "profile.json"
    calibration.write_profile(profile_path, profile)
    admission = _promoted_grant(capability="count_return", site_sha256=site.cube_site_sha256,
                                aircraft_id=aircraft_id, profile_sha256s=(profile_hash,))
    grants = (admission, live_fakes.flight_grant(site)) if flight_grant else (admission,)
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=grants)
    capabilities.write_registry(tmp_path / "registry.json", registry)
    drift_path, energy_path, limits_path = tmp_path / "drift.json", tmp_path / "energy.json", tmp_path / "limits.json"
    drift_path.write_text(json.dumps(asdict(live_fakes.drift_profile())), encoding="utf-8")
    energy_path.write_text(json.dumps(asdict(live_fakes.energy_profile(site))), encoding="utf-8")
    limits_path.write_text(json.dumps({"trim_rounds": 0, "localise": False}), encoding="utf-8")
    manifest_path, digest = _write_manifest(tmp_path, **{
        "--capability-grant-sha256": admission.grant_sha256, "--profile": [profile_hash],
        "--site-sha256": site.cube_site_sha256, "--tag-map-sha256": site.tag_map_sha256,
    })
    extra = (["--drift-profile", str(drift_path), "--energy-profile", str(energy_path),
              "--command-limits", str(limits_path)] if calibration_flags else [])
    argv = _full_gate_argv(tmp_path, manifest_path, digest, grant=admission, profile_path=profile_path,
                           extra_flags=extra, site_path=site_path)
    return argv, manifest_path


def _run_dir(tmp_path, manifest_path):
    return tmp_path / "runs" / artifacts.parse_manifest(Path(manifest_path).read_bytes()).run_id


def _event_types(events):
    return [event.event_type for event in events]


def test_live_full_flight_runs_count_and_return_and_finalizes_a_verifiable_terminal(tmp_path, capsys):
    # Fix round 4 (Q2): one invocation authorizes the bounded session. After
    # every gate and the connectivity preflight, live builds the run artifact
    # and store, the SafetySupervisor (tracker/reserve/limits from the
    # --drift-profile/--energy-profile/--command-limits evidence files), the
    # flight stack, Jev and the detector, compiles the goal ledger, takes off,
    # drives RoomController.run_until_terminal and finalizes terminal.json --
    # which room_verify_artifact.py then verifies end to end.
    import scripts.room_verify_artifact as verify_cli

    argv, manifest_path = _flight_setup(tmp_path)
    stack = live_fakes.FlightStackFactory()
    judge = live_fakes.PolicyJudge("jev-vendor-x")
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=stack, jev_factory=lambda: judge,
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    out, err = capsys.readouterr()
    assert exit_code == 0, err
    assert stack.executor.calls[0] == "TAKEOFF"
    assert stack.executor.calls[-1] == "LAND"
    assert not stack.emergencies
    assert "goal_family" in judge.names and "room_action" in judge.names
    run_dir = _run_dir(tmp_path, manifest_path)
    terminal = json.loads((run_dir / "terminal.json").read_text(encoding="utf-8"))
    assert terminal["took_off"] is True
    assert terminal["ledger_status"] == "PENDING"
    assert terminal["landing_state"] == "LANDED" and terminal["completed_at"]
    # RoomController._observation_enabled (Plan 03-owned) offers OBSERVE only
    # in SIMULATION mode, so a REAL-mode session has no count evidence: Jev
    # reads, finds none, and the controller returns home, lands, and reports
    # BLOCKED. When that gate opens for promoted profiles, this becomes an
    # answered count -- the composition here does not change.
    assert terminal["state"] == "BLOCKED"
    assert json.loads(out.strip().splitlines()[-1]) == {"run_id": "run-0001", "terminal_state": terminal["state"]}
    events = _run_events(tmp_path, "runs", manifest_path)
    types = _event_types(events)
    # Preflight -> identities -> ledger -> fresh-connectivity takeoff authorization -> flight.
    assert types.index("CONNECTIVITY_CHECKED") < types.index("IDENTITY_RESOLVED")
    assert types.index("IDENTITY_RESOLVED") < types.index("GOAL_LEDGER_ACCEPTED")
    assert types.index("GOAL_LEDGER_ACCEPTED") < types.index("TAKEOFF_AUTHORIZED")
    assert types.index("TAKEOFF_AUTHORIZED") < types.index("SAFETY_ASSESSED")
    takeoff = events[types.index("TAKEOFF_AUTHORIZED")].payload
    assert takeoff["drone_identity"] == "tello-1" and takeoff["provider_identity"] == "gemini-1"
    resolved = {e.payload["role"]: e.payload["resolved"] for e in events if e.event_type == "IDENTITY_RESOLVED"}
    assert resolved == {"jev": "jev-vendor-x", "detector": "detector-vendor-y"}
    assert verify_cli.verify(
        manifest_path=run_dir / "manifest.json", expect_terminal=terminal["state"],
        expect_capability="count_return",
        require_event=("CONNECTIVITY_CHECKED", "TAKEOFF_AUTHORIZED", "GOAL_LEDGER_ACCEPTED", "DRONE_STATE_RECORDED"),
    ) == "run-0001"


def test_live_full_flight_rejected_goal_ledger_finalizes_blocked_without_takeoff(tmp_path, capsys):
    argv, manifest_path = _flight_setup(tmp_path)
    stack = live_fakes.FlightStackFactory()
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=stack, jev_factory=lambda: live_fakes.PolicyJudge(clarify=True),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    capsys.readouterr()
    assert exit_code == 0
    assert stack.executor.calls == []
    terminal = json.loads((_run_dir(tmp_path, manifest_path) / "terminal.json").read_text(encoding="utf-8"))
    assert terminal["state"] == "BLOCKED"
    assert terminal["took_off"] is False
    assert "TAKEOFF_AUTHORIZED" not in _event_types(_run_events(tmp_path, "runs", manifest_path))


def test_live_full_flight_refuses_before_takeoff_without_a_flight_calibration_grant(tmp_path, capsys):
    # The admission grant alone (manifest profile hashes) is not enough: the
    # SafetySupervisor independently requires a grant bound to the exact
    # drift/energy/limits/site calibration it flies with.
    argv, manifest_path = _flight_setup(tmp_path, flight_grant=False)
    stack = live_fakes.FlightStackFactory()
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=stack, jev_factory=lambda: live_fakes.PolicyJudge(),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "capability does not match" in err
    assert stack.executor.calls == []
    assert not (_run_dir(tmp_path, manifest_path) / "terminal.json").exists()
    events = _run_events(tmp_path, "runs", manifest_path)
    assert "LIVE_SESSION_REFUSED" in _event_types(events)


def test_live_full_flight_refuses_without_drift_and_energy_evidence_files(tmp_path, capsys):
    argv, _ = _flight_setup(tmp_path, calibration_flags=False)

    def forbidden():
        raise AssertionError("must never reach connectivity without flight calibration evidence")

    exit_code = cli.main(argv, drone_probe=forbidden, provider_probe=forbidden)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "--drift-profile" in err


def test_live_full_flight_emergency_lands_when_the_session_loop_fails_after_takeoff(tmp_path, capsys, monkeypatch):
    from sentinel.room.controller import RoomController

    def broken(self, session_id):
        raise RuntimeError("controller crashed mid-flight")

    monkeypatch.setattr(RoomController, "run_until_terminal", broken)
    argv, manifest_path = _flight_setup(tmp_path)
    stack = live_fakes.FlightStackFactory()
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=stack, jev_factory=lambda: live_fakes.PolicyJudge(),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    capsys.readouterr()
    assert exit_code == 0
    assert stack.executor.calls[0] == "TAKEOFF"
    assert stack.emergencies == [True]
    terminal = json.loads((_run_dir(tmp_path, manifest_path) / "terminal.json").read_text(encoding="utf-8"))
    assert terminal["state"] == "ABORTED"
    assert terminal["took_off"] is True


def test_live_full_flight_takeoff_failure_on_the_ground_is_a_refusal(tmp_path, capsys):
    argv, manifest_path = _flight_setup(tmp_path)
    stack = live_fakes.FlightStackFactory(start_error=RuntimeError("motors did not arm"))
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=stack, jev_factory=lambda: live_fakes.PolicyJudge(),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "takeoff refused" in err
    assert stack.emergencies == []
    assert not (_run_dir(tmp_path, manifest_path) / "terminal.json").exists()


def test_live_full_flight_takeoff_failure_while_airborne_forces_a_landing(tmp_path, capsys):
    # executor.start raising after the aircraft left the ground must never be
    # reported as a mere refusal: the supervisor's forced landing runs and the
    # run is finalized ABORTED.
    argv, manifest_path = _flight_setup(tmp_path)
    stack = live_fakes.FlightStackFactory(start_error=RuntimeError("home measurement timed out"),
                                          airborne_before_error=True)
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=stack, jev_factory=lambda: live_fakes.PolicyJudge(),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    capsys.readouterr()
    assert exit_code == 0
    assert stack.emergencies == [True]
    terminal = json.loads((_run_dir(tmp_path, manifest_path) / "terminal.json").read_text(encoding="utf-8"))
    assert terminal["state"] == "ABORTED"
    assert terminal["took_off"] is True
    assert terminal["reason"] == "START_FAILED"


def test_live_full_flight_refuses_when_the_constructed_jev_has_no_identity(tmp_path, capsys):
    argv, manifest_path = _flight_setup(tmp_path)
    stack = live_fakes.FlightStackFactory()
    # jev's identity is checked (and refused) before detector's, so no
    # IDENTITY_RESOLVED event is ever recorded for either role, and nothing
    # flies.
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=stack, jev_factory=lambda: object(),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "no nonblank string .identity" in err
    assert stack.calls == []
    events = _run_events(tmp_path, "runs", manifest_path)
    assert not [e for e in events if e.event_type == "IDENTITY_RESOLVED"]


def test_live_full_flight_default_detector_factory_refuses(tmp_path, capsys):
    argv, _ = _flight_setup(tmp_path)
    stack = live_fakes.FlightStackFactory()
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=stack, jev_factory=lambda: live_fakes.PolicyJudge())
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "requires an injected detector_factory" in err
    assert stack.calls == []


def test_live_default_flight_stack_requires_a_tag_map_before_connectivity(tmp_path, capsys, monkeypatch):
    transport = _fake_tello(monkeypatch)
    argv, manifest_path = _flight_setup(tmp_path)

    def forbidden():
        raise AssertionError("the tag-map gate must refuse before connectivity")

    exit_code = cli.main(argv, drone_probe=forbidden, provider_probe=forbidden,
                         jev_factory=lambda: live_fakes.PolicyJudge(),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "--tag-map is required" in err
    assert transport.builds == 0
    assert not _run_dir(tmp_path, manifest_path).exists()


def test_live_default_flight_stack_refuses_a_tag_map_not_bound_to_the_manifest(tmp_path, capsys, monkeypatch):
    transport = _fake_tello(monkeypatch)
    argv, _ = _flight_setup(tmp_path)  # the fixture site binds tag_map_sha256 "b" * 64
    exit_code = cli.main([*argv, "--tag-map", str(TAG_MAP_FIXTURE)],
                         jev_factory=lambda: live_fakes.PolicyJudge(),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "tag_map_sha256" in err
    assert transport.builds == 0


def test_live_default_flight_stack_shares_the_probe_connection_and_never_takes_off_without_tag_poses(
        tmp_path, capsys, monkeypatch):
    # The default drone probe and the default flight stack share one client
    # (DJITelloPy's receivers are process-global), built only after every
    # gate. With no tag pose the supervisor refuses takeoff: nothing flies.
    from dataclasses import replace

    tag_map_sha256 = hashlib.sha256(TAG_MAP_FIXTURE.read_bytes()).hexdigest()
    site = replace(live_fakes.fixture_site(), tag_map_sha256=tag_map_sha256)
    argv, manifest_path = _flight_setup(tmp_path, site=site)
    transport = _fake_tello(monkeypatch)
    bursts = []
    monkeypatch.setattr(live_stack, "wp5_pose_burst", lambda *args, **kwargs: bursts.append(kwargs) or [])
    exit_code = cli.main([*argv, "--tag-map", str(TAG_MAP_FIXTURE)], provider_probe=_fake_probe("gemini-1"),
                         jev_factory=lambda: live_fakes.PolicyJudge(),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "takeoff refused" in err and "LOCALIZATION_LOST" in err
    assert transport.builds == 1
    assert bursts  # the default stack asked the WP5 stack for tag poses
    assert stack_fakes.sent_motor_commands(transport.tello.sent) == []
    assert transport.tello.ended is True  # the CLI closes the shared connection
    events = _run_events(tmp_path, "runs", manifest_path)
    checked = next(e for e in events if e.event_type == "CONNECTIVITY_CHECKED").payload
    assert checked["drone_identity"].startswith("tello 192.168.10.1 ")
    assert "LIVE_SESSION_REFUSED" in _event_types(events)
    assert not (_run_dir(tmp_path, manifest_path) / "terminal.json").exists()


def test_live_full_flight_refuses_without_a_matching_profile_file(tmp_path, capsys):
    site_sha256, aircraft_id = _hash("site"), "aircraft-1"
    profile = _promoted_profile(site_sha256=site_sha256, aircraft_id=aircraft_id)
    profile_hash = calibration.canonical_profile_sha256(profile)
    grant = _promoted_grant(capability="count_return", site_sha256=site_sha256, aircraft_id=aircraft_id,
                            profile_sha256s=(profile_hash,))
    _registry_with_grant(tmp_path, grant)
    manifest_path, digest = _write_manifest(tmp_path, **{
        "--capability-grant-sha256": grant.grant_sha256, "--profile": [profile_hash],
    })

    def forbidden():
        raise AssertionError("must never reach connectivity when the profile gate refuses")

    argv = _live_argv(manifest_path, digest, tmp_path, **{
        "__flags__": ["--execute", *_consent_flags(tmp_path)],
    })
    exit_code = cli.main(argv, drone_probe=forbidden, provider_probe=forbidden)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "no --profile-file supplied" in err


def test_live_full_flight_refuses_without_site_assurance(tmp_path, capsys):
    site_sha256, aircraft_id = _hash("site"), "aircraft-1"
    grant = _promoted_grant(capability="count_return", site_sha256=site_sha256, aircraft_id=aircraft_id,
                            profile_sha256s=())
    _registry_with_grant(tmp_path, grant)
    manifest_path, digest = _write_manifest(tmp_path, **{"--capability-grant-sha256": grant.grant_sha256})

    def forbidden():
        raise AssertionError("must never reach connectivity when the site-assurance gate refuses")

    argv = _live_argv(manifest_path, digest, tmp_path, **{
        "__flags__": ["--execute", *_consent_flags(tmp_path)],
    })
    exit_code = cli.main(argv, drone_probe=forbidden, provider_probe=forbidden)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "site assurance" in err or "--site is required" in err


def test_live_refuses_a_now_flag_as_unrecognized(tmp_path):
    # Fix round 1 (Q7): live never accepts --now (main()'s injected wall_clock
    # takes its place). A back-dated --now could otherwise admit an expired
    # PROMOTED/TRIAL grant or stale site assurance.
    manifest_path, digest = _write_manifest(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["live", "--execute", "--manifest", str(manifest_path), "--authorization-digest", digest,
                 "--capability", "count_return", "--grant-registry", str(tmp_path / "registry.json"),
                 "--now", "2026-01-01T00:00:00+00:00"])


def test_live_full_flight_refuses_when_site_does_not_match_the_manifest(tmp_path, capsys):
    # Fix round 1 (Q6): --site must be bound to the manifest's own declared
    # site_sha256/tag_map_sha256. grant/profile/manifest all agree on a
    # placeholder site_sha256 here (so admission and the profile gate both
    # pass); only --site (SITE_PATH, whose real hash differs) mismatches.
    site_sha256, aircraft_id = _hash("site"), "aircraft-1"
    profile = _promoted_profile(site_sha256=site_sha256, aircraft_id=aircraft_id)
    profile_hash = calibration.canonical_profile_sha256(profile)
    profile_path = tmp_path / "profile.json"
    calibration.write_profile(profile_path, profile)
    grant = _promoted_grant(capability="count_return", site_sha256=site_sha256, aircraft_id=aircraft_id,
                            profile_sha256s=(profile_hash,))
    _registry_with_grant(tmp_path, grant)
    manifest_path, digest = _write_manifest(tmp_path, **{
        "--capability-grant-sha256": grant.grant_sha256, "--profile": [profile_hash],
    })  # default --site-sha256/--tag-map-sha256 (== _hash("site")/_hash("tagmap")) left in place

    def forbidden():
        raise AssertionError("must never reach connectivity when --site does not match the manifest")

    argv = _full_gate_argv(tmp_path, manifest_path, digest, grant=grant, profile_path=profile_path)
    exit_code = cli.main(argv, drone_probe=forbidden, provider_probe=forbidden)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "does not match the manifest" in err


def test_live_full_flight_refuses_an_assurance_recorded_for_a_different_session(tmp_path, capsys):
    # Fix round 1 (Q6): an assurance whose session_id is not this run's
    # manifest.run_id must never count toward clearing a trigger.
    site_sha256, aircraft_id = SITE_SHA256, "aircraft-1"
    profile = _promoted_profile(site_sha256=site_sha256, aircraft_id=aircraft_id)
    profile_hash = calibration.canonical_profile_sha256(profile)
    profile_path = tmp_path / "profile.json"
    calibration.write_profile(profile_path, profile)
    grant = _promoted_grant(capability="count_return", site_sha256=site_sha256, aircraft_id=aircraft_id,
                            profile_sha256s=(profile_hash,))
    _registry_with_grant(tmp_path, grant)
    manifest_path, digest = _write_manifest(tmp_path, **{
        "--capability-grant-sha256": grant.grant_sha256, "--profile": [profile_hash],
        "--site-sha256": SITE_SHA256, "--tag-map-sha256": TAG_MAP_SHA256,
    })
    consent = _consent_flags(tmp_path)
    nav = _assurance_file(tmp_path, name="nav", contingency_id="NAVIGATION_UNAVAILABLE", clock_domain_id="cd-1")
    document = json.loads(nav.read_text(encoding="utf-8"))
    document["session_id"] = "some-other-run"
    nav.write_text(json.dumps(document), encoding="utf-8")
    energy = _assurance_file(tmp_path, name="energy", contingency_id="RETURN_ENERGY_UNAVAILABLE", clock_domain_id="cd-1")

    def forbidden():
        raise AssertionError("must never reach connectivity when an assurance belongs to a different session")

    argv = _live_argv(manifest_path, digest, tmp_path, **{
        "__flags__": ["--execute", *consent, "--profile-file", str(profile_path), "--site", str(SITE_PATH),
                     "--assurance", str(nav), "--assurance", str(energy), "--clock-domain-id", "cd-1"],
    })
    exit_code = cli.main(argv, drone_probe=forbidden, provider_probe=forbidden)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "site assurance" in err


def test_live_full_flight_refuses_without_a_unique_matching_grant(tmp_path, capsys):
    manifest_path, digest = _write_manifest(tmp_path)  # registry is empty
    argv = _live_argv(manifest_path, digest, tmp_path, **{"__flags__": ["--execute", *_consent_flags(tmp_path)]})
    exit_code = cli.main(argv)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "no unique matching" in err or "refused" in err


def _run_events(tmp_path, run_root_name, manifest_path):
    manifest = artifacts.parse_manifest(Path(manifest_path).read_bytes())
    run = artifacts.RunArtifacts.open(tmp_path / run_root_name, manifest, now=datetime.now(timezone.utc))
    try:
        return run.store.events(manifest.run_id)
    finally:
        run.close()


def test_live_connectivity_only_records_the_preflight_in_a_run_artifact(tmp_path, capsys):
    manifest_path, digest = _write_manifest(tmp_path)
    consent = _consent_flags(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path,
                      **{"__flags__": ["--execute", "--check-connectivity-only", *consent]})
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"))
    assert exit_code == 0
    capsys.readouterr()
    events = _run_events(tmp_path, "runs", manifest_path)
    checked = [e for e in events if e.event_type == "CONNECTIVITY_CHECKED"]
    assert len(checked) == 1
    assert checked[0].payload["ready"] is True
    assert checked[0].payload["drone_identity"] == "tello-1"
    assert checked[0].payload["provider_identity"] == "gemini-1"
    assert checked[0].payload["check_connectivity_only"] is True


def test_live_connectivity_only_records_a_not_ready_preflight_too(tmp_path, capsys):
    manifest_path, digest = _write_manifest(tmp_path)
    consent = _consent_flags(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path,
                      **{"__flags__": ["--execute", "--check-connectivity-only", *consent]})
    exit_code = cli.main(argv)  # default probes hit the forbidden transports -> not ready
    assert exit_code == 2
    capsys.readouterr()
    events = _run_events(tmp_path, "runs", manifest_path)
    checked = [e for e in events if e.event_type == "CONNECTIVITY_CHECKED"]
    assert len(checked) == 1
    assert checked[0].payload["ready"] is False


def test_live_full_flight_records_connectivity_before_takeoff(tmp_path, capsys):
    argv, manifest_path = _flight_setup(tmp_path)
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=live_fakes.FlightStackFactory(),
                         jev_factory=lambda: live_fakes.PolicyJudge(),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    capsys.readouterr()
    assert exit_code == 0
    events = _run_events(tmp_path, "runs", manifest_path)
    checked = [e for e in events if e.event_type == "CONNECTIVITY_CHECKED"]
    assert len(checked) == 1
    assert checked[0].payload["ready"] is True
    assert checked[0].payload["check_connectivity_only"] is False


def test_live_full_flight_refuses_when_connectivity_has_expired_before_takeoff(tmp_path, capsys):
    # The freshness recheck runs immediately before supervisor.start (after
    # collaborator construction, supervisor bootstrap and goal compilation),
    # not merely right after the preflight. The clock jumps 100s once the
    # flight stack has been built -- well past --connectivity-max-age-s.
    argv, manifest_path = _flight_setup(tmp_path)
    stack = live_fakes.FlightStackFactory()

    def clock():
        return time.monotonic() + (100.0 if stack.calls else 0.0)

    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=stack, jev_factory=lambda: live_fakes.PolicyJudge(),
                         detector_factory=_fake_collaborator("detector-vendor-y"), clock=clock)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "expired before takeoff" in err
    assert stack.calls and stack.executor.calls == []
    events = _run_events(tmp_path, "runs", manifest_path)
    assert len([e for e in events if e.event_type == "CONNECTIVITY_EXPIRED_BEFORE_TAKEOFF"]) == 1
    assert "TAKEOFF_AUTHORIZED" not in _event_types(events)
    assert not (_run_dir(tmp_path, manifest_path) / "terminal.json").exists()


def test_live_default_factories_refuse_real_construction(tmp_path, capsys, monkeypatch):
    transport = _fake_tello(monkeypatch)
    argv, _ = _flight_setup(tmp_path)
    # No probe/client/jev/detector overrides at all: without a bound tag map
    # the defaults refuse before any real client is built.
    exit_code = cli.main(argv)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "--tag-map is required" in err
    assert transport.builds == 0


def test_connectivity_only_defaults_are_the_live_stack_probes(tmp_path, capsys, monkeypatch):
    transport = _fake_tello(monkeypatch, battery=64, sdk="30")
    _fake_provider(monkeypatch)
    manifest_path, digest = _write_manifest(tmp_path)
    consent = _consent_flags(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path,
                      **{"__flags__": ["--execute", "--check-connectivity-only", *consent]})
    exit_code = cli.main(argv)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.strip() == "CONNECTIVITY_READY"
    # Command mode and two reads; no takeoff, motor or RC command; closed.
    assert transport.tello.sent == ["command", "height?", "sdk?"]
    assert stack_fakes.sent_motor_commands(transport.tello.sent) == []
    assert transport.tello.ended is True
    checked = next(e for e in _run_events(tmp_path, "runs", manifest_path)
                   if e.event_type == "CONNECTIVITY_CHECKED").payload
    assert checked["drone_identity"] == "tello 192.168.10.1 sdk=30 battery=64% height=0cm"
    assert checked["provider_identity"] == "openrouter:google/gemini-test"


def test_connectivity_only_default_provider_probe_names_a_missing_key(tmp_path, capsys, monkeypatch):
    _fake_tello(monkeypatch)
    manifest_path, digest = _write_manifest(tmp_path)
    consent = _consent_flags(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path,
                      **{"__flags__": ["--execute", "--check-connectivity-only", *consent]})
    exit_code = cli.main(argv)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "provider_ready=False" in err and live_stack.PROVIDER_KEY_ENV in err


def test_live_default_probes_are_never_built_when_a_gate_refuses(tmp_path, capsys, monkeypatch):
    transport = _fake_tello(monkeypatch)
    manifest_path, digest = _write_manifest(tmp_path)
    argv = _live_argv(manifest_path, digest, tmp_path, **{"__flags__": ["--execute", "--check-connectivity-only"]})
    exit_code = cli.main(argv)  # no consent flags: refused before any probe runs
    capsys.readouterr()
    assert exit_code == 2
    assert transport.builds == 0


# ---------------------------------------------------------------------------
# replay / simulate
# ---------------------------------------------------------------------------


def test_replay_reproduces_the_fixture_and_prints_its_terminal_state(capsys):
    exit_code = cli.main(["replay", "--fixture", str(REPLAY_MANIFEST_PATH)])
    out = capsys.readouterr().out.strip()
    assert exit_code == 0
    document = json.loads(out)
    assert "terminal_state" in document
    assert "final_snapshot_sha256" in document


def test_replay_refuses_a_missing_fixture(tmp_path, capsys):
    exit_code = cli.main(["replay", "--fixture", str(tmp_path / "nope.json")])
    assert exit_code == 2
    assert "refused" in capsys.readouterr().err


def test_replay_never_constructs_a_provider_or_client():
    # replay_fixture's own default factories (`forbidden`) already assert
    # this; reaching the end without an exception is the proof.
    cli.main(["replay", "--fixture", str(REPLAY_MANIFEST_PATH)])


RUNBOOK_PATH = Path(__file__).resolve().parents[2] / "docs" / "room-agent-live-runbook.md"
L0_HEADING = "## L0: motors-off connectivity check"


def _runbook_l0_section() -> str:
    text = RUNBOOK_PATH.read_text(encoding="utf-8")
    start = text.index(L0_HEADING)
    end = text.find("\n## ", start + len(L0_HEADING))
    return text[start:] if end == -1 else text[start:end]


def _runbook_commands(section: str, substitutions: dict) -> list[tuple[str, list[str]]]:
    """Every ``python scripts/...`` command in the section's PowerShell blocks,
    backtick continuations joined and the given variables substituted."""
    import re
    import shlex

    commands = []
    for block in re.findall(r"```powershell\n(.*?)```", section, flags=re.S):
        for line in re.sub(r"[ \t]*`\n[ \t]*", " ", block).splitlines():
            line = line.strip()
            if not line.startswith("python scripts/"):
                continue
            for name, value in substitutions.items():
                line = line.replace(name, value)
            tokens = shlex.split(line)
            commands.append((tokens[1], tokens[2:]))
    return commands


def test_runbook_l0_sequence_runs_end_to_end_against_the_fake_transports(tmp_path, capsys, monkeypatch):
    # The L0 section's own copy-paste commands, in order, run from a scratch
    # repository root: registry init, preview, the motors-off connectivity
    # check through the live defaults (fake Tello and provider transports),
    # then the artifact check.
    import scripts.room_capability as capability_cli
    import scripts.room_verify_artifact as verify_cli
    from sentinel.survey.consent import record_consent
    from sentinel.survey.store import SurveyStore

    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    store = SurveyStore.open(tmp_path / "runs" / "consent.sqlite3")
    record_consent(store, location="CanningSt", consent_date="2026-09-23", recorded_by="operator")
    store.connection.close()
    transport = _fake_tello(monkeypatch)
    _fake_provider(monkeypatch)
    commands = _runbook_commands(_runbook_l0_section(), {
        "$RUN": "l0-test", "$TODAY": "2026-09-23", "$(git rev-parse HEAD)": "abc123"})
    mains = {"scripts/room_capability.py": capability_cli.main, "scripts/room_session.py": cli.main,
             "scripts/room_verify_artifact.py": verify_cli.main}
    assert [(script, argv[0]) for script, argv in commands] == [
        ("scripts/room_capability.py", "init"), ("scripts/room_session.py", "preview"),
        ("scripts/room_session.py", "live"), ("scripts/room_verify_artifact.py", "--manifest")]
    outputs = []
    for script, argv in commands:
        if "$DIGEST" in argv:
            manifest = json.loads((tmp_path / "runs" / "l0-test.manifest.json").read_text(encoding="utf-8"))
            argv = [manifest["authorization_digest"] if token == "$DIGEST" else token for token in argv]
        exit_code = mains[script](argv)
        out, err = capsys.readouterr()
        assert exit_code == 0, (script, err)
        outputs.append(out.strip())
    assert outputs[2] == "CONNECTIVITY_READY"
    assert outputs[3] == "l0-test"
    assert stack_fakes.sent_motor_commands(transport.tello.sent) == []


def test_runbook_l0_preview_binds_the_named_site_fixture():
    commands = _runbook_commands(_runbook_l0_section(), {})
    preview = next(argv for script, argv in commands if argv[0] == "preview")
    parsed = cli.build_parser().parse_args(preview)
    assert "fixtures/room_agent/v1/semantic-site.json" in _runbook_l0_section()
    assert (parsed.site_sha256, parsed.tag_map_sha256) == (SITE_SHA256, TAG_MAP_SHA256)
    assert parsed.capability == "connectivity"


def test_runbook_l0_states_prerequisites_expected_output_and_failure_meanings():
    section = _runbook_l0_section()
    for needle in (
        "CONNECTIVITY_READY", live_stack.PROVIDER_KEY_ENV, live_stack.PROVIDER_MODEL_ENV,
        live_stack.OPENROUTER_KEY_URL, "PYTHONPATH", "Tello Wi-Fi", "authorization_digest",
        "drone_ready=False", "provider_ready=False", "stale telemetry", "stale reachability check",
        "TimeoutError", "no filming consent recorded", "authorization_digest does not match",
        "already exists", "missing gate: capability admission", "--connectivity-only",
    ):
        assert needle in section, needle


def test_default_request_text_matches_the_runbook():
    runbook = (Path(__file__).resolve().parents[2] / "docs" / "room-agent-live-runbook.md").read_text(encoding="utf-8")
    assert " ".join(cli.DEFAULT_REQUEST_TEXT.split()) in " ".join(runbook.split())


def test_simulate_always_refuses_as_deferred(tmp_path, capsys):
    exit_code = cli.main(["simulate", "--fixture", str(tmp_path / "does-not-exist.json")])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert cli.SIMULATION_DEFERRED_MESSAGE in err


# ---------------------------------------------------------------------------
# sentinel.room.connectivity: dual-path preflight
# ---------------------------------------------------------------------------


def test_connectivity_check_ready_when_both_paths_are_fresh():
    clock_values = iter([0.0, 0.0, 0.0, 1.0])
    result = connectivity.check(
        drone_probe=_fake_probe("tello-1", checked_at=0.0), provider_probe=_fake_probe("gemini-1", checked_at=0.0),
        max_age_s=5.0, timeout_s=2.0, clock=lambda: next(clock_values, 1.0),
    )
    assert result.ready is True
    assert result.drone_identity == "tello-1"
    assert result.provider_identity == "gemini-1"


def test_connectivity_check_refuses_when_one_path_errors():
    def failing():
        raise RuntimeError("no telemetry")

    result = connectivity.check(drone_probe=failing, provider_probe=_fake_probe("gemini-1"),
                                max_age_s=5.0, timeout_s=2.0)
    assert result.ready is False
    assert result.drone_ready is False
    assert result.provider_ready is True
    assert "no telemetry" in result.drone_error


def test_connectivity_check_refuses_when_a_probe_hangs_past_timeout():
    def hangs():
        time.sleep(5.0)
        return connectivity.ProbeResult("tello-1", time.monotonic())

    started = time.monotonic()
    result = connectivity.check(drone_probe=hangs, provider_probe=_fake_probe("gemini-1"),
                                max_age_s=5.0, timeout_s=0.2)
    elapsed = time.monotonic() - started
    assert result.ready is False
    assert result.drone_ready is False
    assert elapsed < 2.0  # never waits for the hung probe past its own timeout


def test_connectivity_check_runs_both_probes_concurrently_not_sequentially():
    def slow(identity):
        def probe():
            time.sleep(0.3)
            return connectivity.ProbeResult(identity, time.monotonic())
        return probe

    started = time.monotonic()
    result = connectivity.check(drone_probe=slow("tello-1"), provider_probe=slow("gemini-1"),
                                max_age_s=5.0, timeout_s=2.0)
    elapsed = time.monotonic() - started
    assert result.ready is True
    assert elapsed < 0.6  # well under 2x0.3s -- proves the two probes overlap


def test_connectivity_check_rejects_invalid_bounds():
    with pytest.raises(connectivity.ConnectivityError):
        connectivity.check(drone_probe=_fake_probe(), provider_probe=_fake_probe(), max_age_s=0, timeout_s=1.0)
    with pytest.raises(connectivity.ConnectivityError):
        connectivity.check(drone_probe=_fake_probe(), provider_probe=_fake_probe(), max_age_s=1.0, timeout_s=-1)
