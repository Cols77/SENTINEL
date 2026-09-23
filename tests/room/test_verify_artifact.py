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


_SAFE_TERMINATION_STATES = frozenset({"COMPLETED", "INCOMPLETE_RETURNED", "SAFE_LANDED", "LANDED_AT_CERTIFIED_ZONE"})


def _finished_run(tmp_path, *, state="COMPLETED", completed_at=1.0, record_landing=None, **overrides):
    run = _open_run(tmp_path, **overrides)
    run.record_resolved_identity(role="jev", resolved="jev-vendor-x", observed_at=NOW)
    run.append_event("CHECKPOINT_COMPLETED", {"i": 1}, now=NOW)
    # Fix round 1 (Q8): a safe-termination state now requires durable
    # DRONE_STATE_RECORDED evidence of a confirmed landing, not just a
    # caller-supplied terminal.json completed_at key.
    if record_landing is None:
        record_landing = state in _SAFE_TERMINATION_STATES
    if record_landing:
        run.append_event(
            "DRONE_STATE_RECORDED",
            {"observed_at": NOW.isoformat(), "scope": f"aircraft:{run.manifest.run_id}", "uncertainty": "measured",
             "state": {"terminal_state": "LANDED", "completion_known": True, "flight_state": "LANDED"}},
            now=NOW,
        )
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


def test_verify_refuses_a_safe_termination_state_with_completed_at_but_no_durable_landing_event(tmp_path, capsys):
    # Fix round 1 (Q8): terminal.json's completed_at is a caller-supplied
    # mapping nothing durable backs it -- a run claiming COMPLETED with a
    # truthy completed_at but no DRONE_STATE_RECORDED landing evidence in the
    # durable event log must still be refused.
    manifest_path = _finished_run(tmp_path, state="COMPLETED", completed_at=1.0, record_landing=False)
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "no DRONE_STATE_RECORDED evidence" in capsys.readouterr().err


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


def test_verify_refuses_a_gap_in_durable_event_sequence_numbers(tmp_path, capsys):
    # Fix round 1 (Q8): a gap in sequence_no (here simulated by renumbering one
    # event outside the store's append-only write path, the same
    # trigger-dropping technique the pre-existing tampered-hash test uses)
    # leaves every remaining event's own hash intact -- only an explicit
    # contiguity check catches it.
    manifest_path = _finished_run(tmp_path)
    sqlite_path = manifest_path.parent / "session.sqlite3"
    connection = sqlite3.connect(sqlite_path)
    try:
        connection.execute("DROP TRIGGER room_events_no_update")
        connection.execute("UPDATE room_events SET sequence_no = 999 WHERE event_type = 'CHECKPOINT_COMPLETED'")
        connection.commit()
    finally:
        connection.close()
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "sequence_no is not contiguous" in capsys.readouterr().err


def test_verify_refuses_a_summary_event_count_mismatch(tmp_path, capsys):
    manifest_path = _finished_run(tmp_path)
    summary_path = manifest_path.parent / "summary.json"
    document = json.loads(summary_path.read_text(encoding="utf-8"))
    document["event_count"] = document["event_count"] + 1
    summary_path.write_text(json.dumps(document), encoding="utf-8")
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "event_count" in capsys.readouterr().err


def test_verify_refuses_a_missing_summary_json(tmp_path, capsys):
    manifest_path = _finished_run(tmp_path)
    (manifest_path.parent / "summary.json").unlink()
    exit_code = verify_cli.main([
        "--manifest", str(manifest_path), "--expect-terminal", "COMPLETED", "--expect-capability", "count_return",
    ])
    assert exit_code == 2
    assert "summary.json" in capsys.readouterr().err


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
    run.append_event(
        "DRONE_STATE_RECORDED",
        {"observed_at": NOW.isoformat(), "scope": "aircraft:run-0001", "uncertainty": "measured",
         "state": {"terminal_state": "LANDED", "completion_known": True, "flight_state": "LANDED"}},
        now=NOW,
    )
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


# ---------------------------------------------------------------------------
# --connectivity-only: an L0 run (a connectivity preflight, never a terminal)
# ---------------------------------------------------------------------------


def _connectivity_run(tmp_path, *, ready=True, connectivity_only=True, checks=1):
    run = _open_run(tmp_path, requested_capability="connectivity")
    for _ in range(checks):
        run.append_event("CONNECTIVITY_CHECKED", {
            "ready": ready, "drone_ready": ready, "provider_ready": True,
            "drone_identity": "tello 192.168.10.1 sdk=30 battery=87% height=0cm" if ready else None,
            "provider_identity": "openrouter:google/gemini-test", "drone_checked_at": 1.0 if ready else None,
            "provider_checked_at": 1.0, "drone_error": None if ready else "TimeoutError: no reply",
            "provider_error": None, "max_age_s": 5.0, "timeout_s": 5.0,
            "check_connectivity_only": connectivity_only,
        }, now=NOW)
    run.close()
    return tmp_path / "run-0001" / "manifest.json"


def _verify_l0(manifest_path, *extra):
    return verify_cli.main(["--manifest", str(manifest_path), "--connectivity-only",
                            "--expect-capability", "connectivity", *extra])


def test_verify_connectivity_only_accepts_a_ready_l0_run_without_a_terminal(tmp_path, capsys):
    manifest_path = _connectivity_run(tmp_path)
    assert not (manifest_path.parent / "terminal.json").exists()
    assert _verify_l0(manifest_path) == 0
    assert capsys.readouterr().out.strip() == "run-0001"


@pytest.mark.parametrize("ready, connectivity_only", [(False, True), (True, False)])
def test_verify_connectivity_only_refuses_unless_the_latest_check_is_a_ready_l0(tmp_path, capsys, ready,
                                                                               connectivity_only):
    manifest_path = _connectivity_run(tmp_path, ready=ready, connectivity_only=connectivity_only)
    assert _verify_l0(manifest_path) == 2
    assert "connectivity" in capsys.readouterr().err


def test_verify_connectivity_only_refuses_a_run_with_no_preflight(tmp_path, capsys):
    run = _open_run(tmp_path, requested_capability="connectivity")
    run.close()
    assert _verify_l0(tmp_path / "run-0001" / "manifest.json") == 2
    assert "zero durable events" in capsys.readouterr().err


def test_verify_connectivity_only_still_rehashes_every_event(tmp_path, capsys):
    manifest_path = _connectivity_run(tmp_path)
    connection = sqlite3.connect(manifest_path.parent / "session.sqlite3")
    try:
        connection.execute("DROP TRIGGER room_events_no_update")
        connection.execute("UPDATE room_events SET payload_json = '{\"ready\":true}' "
                           "WHERE event_type = 'CONNECTIVITY_CHECKED'")
        connection.commit()
    finally:
        connection.close()
    assert _verify_l0(manifest_path) == 2
    assert "hash does not match" in capsys.readouterr().err


def test_verify_connectivity_only_checks_the_manifest_capability(tmp_path, capsys):
    manifest_path = _connectivity_run(tmp_path)
    exit_code = verify_cli.main(["--manifest", str(manifest_path), "--connectivity-only",
                                 "--expect-capability", "count_return"])
    assert exit_code == 2
    assert "expect-capability" in capsys.readouterr().err


@pytest.mark.parametrize("mode_flags", [[], ["--connectivity-only", "--expect-terminal", "COMPLETED"]])
def test_verify_takes_exactly_one_of_expect_terminal_or_connectivity_only(mode_flags):
    with pytest.raises(SystemExit):
        verify_cli.main(["--manifest", "nope.json", "--expect-capability", "x", *mode_flags])
