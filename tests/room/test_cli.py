"""Tests for scripts/room_session.py and src/sentinel/room/connectivity.py
(Plan 06 Task 4: the manifest-bound session CLI).

Everything here is OFFLINE: no provider, network, drone client or hardware
object is constructed anywhere in this file. Live mode is exercised through
its injected drone_probe/provider_probe/client_factory/jev_factory/
detector_factory parameters only; every default (unauthorized) factory is
proven to refuse, and this file never overrides them with anything that
reaches a socket.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.room_session as cli
from sentinel.room import artifacts, calibration, capabilities, connectivity
from sentinel.room.models import CapabilityGrant, canonical_grant_sha256

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


def test_connectivity_only_refuses_with_default_unauthorized_probes(tmp_path, capsys):
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


def _full_gate_argv(tmp_path, manifest_path, digest, *, grant, profile_path, extra_flags=()):
    consent = _consent_flags(tmp_path)
    nav = _assurance_file(tmp_path, name="nav", contingency_id="NAVIGATION_UNAVAILABLE", clock_domain_id="cd-1")
    energy = _assurance_file(tmp_path, name="energy", contingency_id="RETURN_ENERGY_UNAVAILABLE", clock_domain_id="cd-1")
    return _live_argv(
        manifest_path, digest, tmp_path,
        **{
            "__flags__": [
                "--execute", *consent, "--profile-file", str(profile_path), "--site", str(SITE_PATH),
                "--assurance", str(nav), "--assurance", str(energy), "--clock-domain-id", "cd-1", *extra_flags,
            ],
        },
    )


def test_live_full_flight_gates_pass_then_refuses_at_the_unwired_controller_boundary(tmp_path, capsys):
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
    argv = _full_gate_argv(tmp_path, manifest_path, digest, grant=grant, profile_path=profile_path)
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=lambda: object(), jev_factory=_fake_collaborator("jev-vendor-x"),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "not wired in this build" in err


def test_live_full_flight_records_resolved_jev_and_detector_identity_before_the_boundary(tmp_path, capsys):
    # Fix round 3 (Q2): the manifest's own resolved_jev_identity/
    # resolved_detector_identity are always "UNVERIFIED" (SessionManifest
    # refuses any other value); the *actual* resolved identity only ever
    # lives in the run artifact's durable IDENTITY_RESOLVED event log.
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
    argv = _full_gate_argv(tmp_path, manifest_path, digest, grant=grant, profile_path=profile_path)
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=lambda: object(), jev_factory=_fake_collaborator("jev-vendor-x"),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    assert exit_code == 2
    capsys.readouterr()
    events = _run_events(tmp_path, "runs", manifest_path)
    resolved = {e.payload["role"]: e.payload["resolved"] for e in events if e.event_type == "IDENTITY_RESOLVED"}
    assert resolved == {"jev": "jev-vendor-x", "detector": "detector-vendor-y"}
    not_wired = [e for e in events if e.event_type == "LIVE_EXECUTION_NOT_WIRED"]
    assert len(not_wired) == 1
    assert set(not_wired[0].payload["missing"]) == {"goal_ledger_bootstrap", "safety_supervisor_tracker_reserve"}


def test_live_full_flight_refuses_when_the_constructed_jev_has_no_identity(tmp_path, capsys):
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
    argv = _full_gate_argv(tmp_path, manifest_path, digest, grant=grant, profile_path=profile_path)
    # jev's identity is checked (and refused) before detector's, in the same
    # order both are constructed and then validated -- so no IDENTITY_RESOLVED
    # event is ever recorded for either role, even though detector_factory
    # itself is still constructed alongside client/jev (all three are cheap,
    # side-effect-free construction checks; only what they *report* differs).
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=lambda: object(), jev_factory=lambda: object(),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "no nonblank string .identity" in err
    events = _run_events(tmp_path, "runs", manifest_path)
    assert not [e for e in events if e.event_type == "IDENTITY_RESOLVED"]


def test_live_full_flight_default_detector_factory_refuses(tmp_path, capsys):
    # Fix round 3 (Q2): detector_factory now defaults to refuse, the same
    # convention client_factory/jev_factory already use.
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
    argv = _full_gate_argv(tmp_path, manifest_path, digest, grant=grant, profile_path=profile_path)
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=lambda: object(), jev_factory=_fake_collaborator("jev-vendor-x"))
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "requires an injected detector_factory" in err


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
    exit_code = cli.main(argv)  # default probes refuse -> not ready
    assert exit_code == 2
    capsys.readouterr()
    events = _run_events(tmp_path, "runs", manifest_path)
    checked = [e for e in events if e.event_type == "CONNECTIVITY_CHECKED"]
    assert len(checked) == 1
    assert checked[0].payload["ready"] is False


def test_live_full_flight_records_connectivity_before_refusing_at_the_unwired_controller_boundary(tmp_path, capsys):
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
    argv = _full_gate_argv(tmp_path, manifest_path, digest, grant=grant, profile_path=profile_path)
    exit_code = cli.main(argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
                         client_factory=lambda: object(), jev_factory=_fake_collaborator("jev-vendor-x"),
                         detector_factory=_fake_collaborator("detector-vendor-y"))
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "not wired in this build" in err
    events = _run_events(tmp_path, "runs", manifest_path)
    checked = [e for e in events if e.event_type == "CONNECTIVITY_CHECKED"]
    assert len(checked) == 1
    assert checked[0].payload["ready"] is True
    assert checked[0].payload["check_connectivity_only"] is False


def test_live_full_flight_refuses_when_connectivity_has_expired_before_takeoff(tmp_path, capsys):
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
    argv = _full_gate_argv(tmp_path, manifest_path, digest, grant=grant, profile_path=profile_path)

    def forbidden():
        raise AssertionError("must never construct a client/jev once connectivity has expired before takeoff")

    # `clock` is called once by the site-assurance freshness check and three
    # more times inside connectivity.check() (started, remaining, final now)
    # before this fix's own before-takeoff recheck -- the 5th call. The first
    # four must stay close to real monotonic time (the assurance fixture and
    # the fake probes below are both stamped against real time.monotonic());
    # only the 5th jumps far enough past --connectivity-max-age-s (default
    # 5.0s) to have expired.
    calls = []

    def clock():
        value = time.monotonic()
        calls.append(value)
        return value + 100.0 if len(calls) >= 5 else value

    exit_code = cli.main(
        argv, drone_probe=_fake_probe("tello-1"), provider_probe=_fake_probe("gemini-1"),
        client_factory=forbidden, jev_factory=forbidden, clock=clock,
    )
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "expired before takeoff" in err
    events = _run_events(tmp_path, "runs", manifest_path)
    expired = [e for e in events if e.event_type == "CONNECTIVITY_EXPIRED_BEFORE_TAKEOFF"]
    assert len(expired) == 1


def test_live_default_client_and_jev_factories_refuse_real_construction(tmp_path, capsys):
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
    argv = _full_gate_argv(tmp_path, manifest_path, digest, grant=grant, profile_path=profile_path)
    # No drone_probe/provider_probe/client_factory/jev_factory overrides at all:
    # every one of this build's defaults must refuse before anything real is reached.
    exit_code = cli.main(argv)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "never construct" in err or "requires an injected" in err


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
