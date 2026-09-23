"""Tests for scripts/room_verify_artifact.py (Plan 06 Task 4).

Everything here is OFFLINE: it only ever reads local run-artifact
directories this file builds itself with sentinel.room.artifacts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.room_verify_artifact as verify_cli
from sentinel.room import artifacts, calibration
from sentinel.room.artifacts import SessionManifest

NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _manifest(**overrides) -> SessionManifest:
    kwargs = dict(
        schema_version=artifacts.SCHEMA_VERSION, run_id="run-0001", created_at=NOW.isoformat(),
        mode="live", code_revision="abc123", code_dirty=False, integration_revision="def456",
        requested_capability="count_return", capability_grant_sha256=_hash("grant"),
        capability_grant_state="PROMOTED", request_revision="req-1", goal_parser_revision="goal-1",
        site_sha256=_hash("site"), tag_map_sha256=_hash("tagmap"), camera_sha256=_hash("camera"),
        profile_sha256s=(), aircraft_id="aircraft-1", firmware_version="1.0.0",
        requested_jev_identity="jev-vendor-x", resolved_jev_identity="UNVERIFIED",
        requested_detector_identity="detector-vendor-y", resolved_detector_identity="UNVERIFIED",
        max_duration_s=120.0, max_distance_cm=500.0, max_recovery_attempts=2,
        provider_deadlines={}, cost_assumptions={}, home_policy={}, termination_policy={},
        consent_references=("consent-1",), trial_protocol=None, evidence_destination=None,
    )
    kwargs.update(overrides)
    return SessionManifest(**kwargs)


def _open_run(tmp_path, **overrides) -> "artifacts.RunArtifacts":
    return artifacts.RunArtifacts.open(tmp_path, _manifest(**overrides), now=NOW)


def _finished_run(tmp_path, *, state="COMPLETED", completed_at=1.0, **overrides):
    run = _open_run(tmp_path, **overrides)
    run.record_resolved_identity(role="jev", resolved="jev-vendor-x", observed_at=NOW)
    run.append_event("CHECKPOINT_COMPLETED", {"i": 1}, now=NOW)
    run.finalize({"state": state, "completed_at": completed_at}, now=NOW)
    run.close()
    return tmp_path / "run-0001" / "manifest.json"


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


def test_verify_succeeds_and_prints_the_run_id(tmp_path, capsys):
    manifest_path = _finished_run(tmp_path)
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED",
        "--expect-capability", "count_return",
    ])
    out = capsys.readouterr().out.strip()
    assert exit_code == 0
    assert out == "run-0001"


def test_verify_accepts_a_require_event_that_is_present(tmp_path):
    manifest_path = _finished_run(tmp_path)
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED",
        "--expect-capability", "count_return", "--require-event", "CHECKPOINT_COMPLETED",
    ])
    assert exit_code == 0


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def test_verify_refuses_a_tampered_manifest_digest(tmp_path, capsys):
    manifest_path = _finished_run(tmp_path)
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["max_duration_s"] = 999.0  # content changed; digest now stale
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "authorization_digest" in capsys.readouterr().err


def test_verify_refuses_a_manifest_not_stored_under_its_own_run_id_directory(tmp_path, capsys):
    manifest_path = _finished_run(tmp_path)
    misplaced_dir = tmp_path / "not-the-run-id"
    misplaced_dir.mkdir()
    misplaced_path = misplaced_dir / "manifest.json"
    misplaced_path.write_bytes(manifest_path.read_bytes())
    exit_code = verify_cli.main([
        "--manifest", str(misplaced_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "run_id directory" in capsys.readouterr().err


def test_verify_refuses_a_mismatched_expected_capability(tmp_path, capsys):
    manifest_path = _finished_run(tmp_path)
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "adaptive_movement",
    ])
    assert exit_code == 2
    assert "expect-capability" in capsys.readouterr().err


def test_verify_refuses_when_terminal_json_is_missing(tmp_path, capsys):
    run = _open_run(tmp_path)
    run.close()
    manifest_path = tmp_path / "run-0001" / "manifest.json"
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "terminal.json" in capsys.readouterr().err


def test_verify_refuses_the_wrong_terminal_state(tmp_path, capsys):
    manifest_path = _finished_run(tmp_path, state="ABORTED", completed_at=None)
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "does not match --expect-terminal" in capsys.readouterr().err


def test_verify_refuses_a_safe_termination_state_with_no_completed_at_evidence(tmp_path, capsys):
    manifest_path = _finished_run(tmp_path, state="COMPLETED", completed_at=None)
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "safe-termination" in capsys.readouterr().err


def test_verify_accepts_a_non_safe_terminal_state_with_no_completed_at(tmp_path):
    # BLOCKED/ABORTED/etc never require completed_at evidence -- only the
    # states controller.py only ever reaches after a confirmed landing do.
    manifest_path = _finished_run(tmp_path, state="BLOCKED", completed_at=None)
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "BLOCKED", "--expect-capability", "count_return",
    ])
    assert exit_code == 0


def test_verify_refuses_a_missing_required_event(tmp_path, capsys):
    manifest_path = _finished_run(tmp_path)
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED",
        "--expect-capability", "count_return", "--require-event", "OBSERVE_SUCCEEDED",
    ])
    assert exit_code == 2
    assert "OBSERVE_SUCCEEDED" in capsys.readouterr().err


def test_verify_refuses_a_tampered_durable_event_hash(tmp_path, capsys):
    manifest_path = _finished_run(tmp_path)
    sqlite_path = manifest_path.parent / "session.sqlite3"
    connection = sqlite3.connect(sqlite_path)
    try:
        # The store's own append-only trigger blocks an ordinary UPDATE (proof
        # the store itself resists tampering); this test simulates a SQLite
        # file edited entirely outside the store's write path -- dropping the
        # trigger first, the way an attacker with raw file access could.
        connection.execute("DROP TRIGGER room_events_no_update")
        connection.execute("UPDATE room_events SET payload_json = '{\"i\":999}' WHERE event_type = 'CHECKPOINT_COMPLETED'")
        connection.commit()
    finally:
        connection.close()
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "hash does not match" in capsys.readouterr().err


def test_verify_refuses_a_terminal_identity_mismatch(tmp_path, capsys):
    run = _open_run(tmp_path)
    run.record_resolved_identity(role="jev", resolved="jev-vendor-x", observed_at=NOW)
    run.finalize({"state": "COMPLETED", "completed_at": 1.0}, now=NOW)
    run.close()
    manifest_path = tmp_path / "run-0001" / "manifest.json"
    terminal_path = manifest_path.parent / "terminal.json"
    document = json.loads(terminal_path.read_text(encoding="utf-8"))
    document["resolved_identities"] = {"jev": "some-other-identity"}
    terminal_path.write_text(json.dumps(document), encoding="utf-8")
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "mismatched identity" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# --profile-file
# ---------------------------------------------------------------------------


def _promoted_profile():
    return calibration.CalibrationProfile(
        schema_version=calibration.SCHEMA_VERSION, kind="flight", state="PROMOTED",
        site_sha256=_hash("site"), aircraft_id="aircraft-1", firmware_version="1.0.0",
        camera_sha256=_hash("camera"), tag_map_sha256=_hash("tagmap"), speeds_cm_s=(20,),
        battery=calibration.BatteryBound(0.1, 0.02, 1.0, 15.0), pose_envelope=(),
        training_run_ids=("t",), holdout_run_ids=("h",),
    )


def test_verify_accepts_a_profile_file_matching_a_declared_hash(tmp_path):
    profile = _promoted_profile()
    digest = calibration.canonical_profile_sha256(profile)
    profile_path = tmp_path / "profile.json"
    calibration.write_profile(profile_path, profile)
    manifest_path = _finished_run(tmp_path, profile_sha256s=(digest,))
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
        "--profile-file", str(profile_path),
    ])
    assert exit_code == 0


def test_verify_refuses_a_profile_file_not_among_the_declared_hashes(tmp_path, capsys):
    profile = _promoted_profile()
    profile_path = tmp_path / "profile.json"
    calibration.write_profile(profile_path, profile)
    manifest_path = _finished_run(tmp_path)  # profile_sha256s == ()
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
        "--profile-file", str(profile_path),
    ])
    assert exit_code == 2
    assert "not among the manifest's declared profile_sha256s" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the verify() function directly (argparse-independent)
# ---------------------------------------------------------------------------


def test_verify_function_raises_verification_error_directly(tmp_path):
    manifest_path = _finished_run(tmp_path, state="ABORTED", completed_at=None)
    with pytest.raises(verify_cli.VerificationError):
        verify_cli.verify(manifest_path=manifest_path, expect_terminal="COMPLETED", expect_capability="count_return")


def test_verify_rejects_an_unknown_expect_terminal_via_argparse():
    with pytest.raises(SystemExit):
        verify_cli.main(["--manifest", "nope.json", "--expect-terminal", "BOGUS", "--expect-capability", "x"])
