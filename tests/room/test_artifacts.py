"""Failing-first tests for src/sentinel/room/artifacts.py (Plan 06 Task 1, ARTIFACTS half).

Everything here is OFFLINE: no provider, network, drone client or hardware object is
constructed anywhere in this file.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError as JsonSchemaValidationError

from sentinel.room import artifacts
from sentinel.room.artifacts import SessionManifest

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "superpowers" / "specs" / "contracts" / "room-session-manifest-v1.schema.json"
)

NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _base_kwargs(**overrides: object) -> dict:
    kwargs = dict(
        schema_version=artifacts.SCHEMA_VERSION,
        run_id="run-0001",
        created_at="2026-09-23T00:00:00+00:00",
        mode="preview",
        code_revision="abc123",
        code_dirty=False,
        integration_revision="def456",
        requested_capability="survey.count",
        capability_grant_sha256=_hash("grant"),
        capability_grant_state="PROMOTED",
        request_revision="req-1",
        goal_parser_revision="goal-1",
        site_sha256=_hash("site"),
        tag_map_sha256=_hash("tagmap"),
        camera_sha256=_hash("camera"),
        profile_sha256s=tuple(sorted((_hash("profileA"), _hash("profileB")))),
        aircraft_id="aircraft-1",
        firmware_version="1.0.0",
        requested_jev_identity="jev-vendor-x",
        resolved_jev_identity="UNVERIFIED",
        requested_detector_identity="detector-vendor-y",
        resolved_detector_identity="UNVERIFIED",
        max_duration_s=120.0,
        max_distance_cm=500.0,
        max_recovery_attempts=2,
        provider_deadlines={"jev_decision": 5.0, "detector_call": 3.0},
        cost_assumptions={"currency": "USD", "per_call": 0.01},
        home_policy={"return_on_low_energy": True},
        termination_policy={"no_progress_s": 30.0},
        consent_references=("consent-1",),
        trial_protocol=None,
        evidence_destination=None,
    )
    kwargs.update(overrides)
    return kwargs


def _manifest(**overrides: object) -> SessionManifest:
    return SessionManifest(**_base_kwargs(**overrides))


def _open_run(tmp_path: Path, **overrides: object) -> "artifacts.RunArtifacts":
    return artifacts.RunArtifacts.open(tmp_path, _manifest(**overrides), now=NOW)


# ---------------------------------------------------------------------------
# canonical_json / canonical_bytes / authorization_digest
# ---------------------------------------------------------------------------

def test_canonical_json_is_sorted_and_compact():
    assert artifacts.canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_json_rejects_non_finite_numbers():
    with pytest.raises(ValueError):
        artifacts.canonical_json(float("nan"))
    with pytest.raises(ValueError):
        artifacts.canonical_json(float("inf"))


def test_canonical_bytes_is_canonical_json_encoded_utf8():
    assert artifacts.canonical_bytes({"a": 1}) == b'{"a":1}'


def test_authorization_digest_excludes_only_the_digest_field():
    document = {"a": 1, "b": 2, artifacts.DIGEST_FIELD: "ignored-value"}
    expected = hashlib.sha256(artifacts.canonical_bytes({"a": 1, "b": 2})).hexdigest()
    assert artifacts.authorization_digest(document) == expected


# ---------------------------------------------------------------------------
# reject_forbidden
# ---------------------------------------------------------------------------

def test_reject_forbidden_blocks_credential_key_and_nested_in_list():
    with pytest.raises(artifacts.ArtifactError):
        artifacts.reject_forbidden({"api_key": "secret"})
    with pytest.raises(artifacts.ArtifactError):
        artifacts.reject_forbidden({"items": [{"authorization": "Bearer xyz"}]})
    artifacts.reject_forbidden({"safe": "value"})  # does not raise


# ---------------------------------------------------------------------------
# SessionManifest construction rules
# ---------------------------------------------------------------------------

def test_manifest_requires_unverified_resolved_identities():
    with pytest.raises(artifacts.ManifestError):
        _manifest(resolved_jev_identity="jev-vendor-x")
    with pytest.raises(artifacts.ManifestError):
        _manifest(resolved_detector_identity="detector-vendor-y")


def test_manifest_requires_sorted_profile_hashes():
    unsorted = tuple(reversed(sorted((_hash("profileA"), _hash("profileB")))))
    with pytest.raises(artifacts.ManifestError):
        _manifest(profile_sha256s=unsorted)


def test_acceptance_mode_requires_trial_protocol_and_evidence_destination():
    with pytest.raises(artifacts.ManifestError):
        _manifest(mode="acceptance")
    ok = _manifest(mode="acceptance", trial_protocol="trial-1", evidence_destination="evidence://dest")
    assert ok.mode == "acceptance"


def test_non_acceptance_mode_forbids_trial_protocol_and_evidence_destination():
    with pytest.raises(artifacts.ManifestError):
        _manifest(mode="preview", trial_protocol="trial-1")
    with pytest.raises(artifacts.ManifestError):
        _manifest(mode="preview", evidence_destination="evidence://dest")


def test_manifest_rejects_unknown_mode():
    with pytest.raises(artifacts.ManifestError):
        _manifest(mode="unsupported-mode")


def test_manifest_has_no_arbitrary_total_model_call_cap():
    pattern = re.compile(r"model_call|max_calls|call_budget", re.IGNORECASE)
    names = [field.name for field in dataclasses.fields(SessionManifest)]
    assert not any(pattern.search(name) for name in names), names


def test_manifest_rejects_credential_in_a_nested_policy_field():
    manifest = _manifest(cost_assumptions={"api_key": "secret"})
    with pytest.raises(artifacts.ArtifactError):
        manifest.to_document()


# ---------------------------------------------------------------------------
# Canonical form, digest, non-finite numbers
# ---------------------------------------------------------------------------

def test_default_preview_creates_nothing(tmp_path, monkeypatch):
    import os as os_module

    def _forbid(*args, **kwargs):
        raise AssertionError("SessionManifest must not touch the filesystem")

    # A weak version of this test only checked tmp_path.iterdir(), which would
    # not notice a write anywhere else (a different directory, a NamedTemporaryFile,
    # cwd, ...). Intercepting the actual write primitives catches any of those.
    monkeypatch.setattr(os_module, "replace", _forbid)
    monkeypatch.setattr(os_module, "fsync", _forbid)
    monkeypatch.setattr(Path, "mkdir", _forbid)
    monkeypatch.setattr(Path, "write_bytes", _forbid)
    monkeypatch.setattr(Path, "write_text", _forbid)
    manifest = _manifest()
    manifest.canonical_bytes()
    manifest.to_document()
    assert list(tmp_path.iterdir()) == []


def test_manifest_to_document_includes_digest_field():
    manifest = _manifest()
    document = manifest.to_document()
    assert document[artifacts.DIGEST_FIELD] == artifacts.authorization_digest(document)


def test_manifest_with_non_finite_number_fails_closed_at_serialization():
    manifest = _manifest(max_duration_s=float("nan"))
    with pytest.raises(artifacts.ManifestError):
        manifest.canonical_bytes()
    with pytest.raises(artifacts.ManifestError):
        manifest.to_document()


def test_write_manifest_is_byte_identical_to_canonical_bytes(tmp_path):
    manifest = _manifest()
    path = tmp_path / "manifest.json"
    artifacts.write_manifest(path, manifest)
    file_bytes = path.read_bytes()
    assert file_bytes == manifest.canonical_bytes()
    recomputed_digest = artifacts.authorization_digest(json.loads(file_bytes))
    assert recomputed_digest == manifest.to_document()[artifacts.DIGEST_FIELD]
    assert not path.with_name("manifest.json.tmp").exists()


# ---------------------------------------------------------------------------
# parse_manifest: fail closed
# ---------------------------------------------------------------------------

def test_parse_manifest_round_trips(tmp_path):
    manifest = _manifest()
    path = tmp_path / "manifest.json"
    artifacts.write_manifest(path, manifest)
    parsed = artifacts.parse_manifest(path.read_bytes())
    assert parsed == manifest


def test_parse_manifest_rejects_digest_mismatch():
    manifest = _manifest()
    document = manifest.to_document()
    document[artifacts.DIGEST_FIELD] = "0" * 64
    with pytest.raises(artifacts.ManifestError):
        artifacts.parse_manifest(json.dumps(document).encode("utf-8"))


def test_parse_manifest_rejects_unknown_field():
    manifest = _manifest()
    document = manifest.to_document()
    document["extra_field"] = "nope"
    with pytest.raises(artifacts.ManifestError):
        artifacts.parse_manifest(json.dumps(document).encode("utf-8"))


def test_parse_manifest_rejects_missing_field():
    manifest = _manifest()
    document = manifest.to_document()
    del document["aircraft_id"]
    with pytest.raises(artifacts.ManifestError):
        artifacts.parse_manifest(json.dumps(document).encode("utf-8"))


def test_parse_manifest_rejects_wrong_schema_version():
    manifest = _manifest()
    document = manifest.to_document()
    document["schema_version"] = "room-session-manifest-v0"
    with pytest.raises(artifacts.ManifestError):
        artifacts.parse_manifest(json.dumps(document).encode("utf-8"))


def test_parse_manifest_rejects_non_finite_number_in_raw_bytes():
    manifest = _manifest()
    document = manifest.to_document()
    document["max_duration_s"] = 999999.5
    raw = json.dumps(document).replace("999999.5", "NaN").encode("utf-8")
    with pytest.raises(artifacts.ManifestError):
        artifacts.parse_manifest(raw)


def test_parse_manifest_never_repairs_it_only_rejects():
    manifest = _manifest()
    document = manifest.to_document()
    document["mode"] = "not-a-real-mode"
    document[artifacts.DIGEST_FIELD] = artifacts.authorization_digest(document)
    with pytest.raises(artifacts.ManifestError):
        artifacts.parse_manifest(json.dumps(document).encode("utf-8"))


# ---------------------------------------------------------------------------
# JSON Schema
# ---------------------------------------------------------------------------

def test_manifest_schema_accepts_a_valid_document():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = _manifest()
    # Validate the actual wire form (JSON has no tuple type, so round-trip
    # through canonical bytes rather than validating the raw dataclass dict).
    wire_document = json.loads(manifest.canonical_bytes())
    Draft202012Validator(schema).validate(wire_document)


def test_manifest_schema_rejects_an_unknown_key():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = _manifest()
    document = {**manifest.to_document(), "unexpected_field": True}
    validator = Draft202012Validator(schema)
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(document)


# ---------------------------------------------------------------------------
# RunArtifacts: layout, append-only raw.jsonl, durability
# ---------------------------------------------------------------------------

def test_open_creates_manifest_sqlite_and_frames_dir_but_no_flight_video(tmp_path):
    run = _open_run(tmp_path)
    run_dir = tmp_path / run.manifest.run_id
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "session.sqlite3").exists()
    assert (run_dir / "frames").is_dir()
    assert not (run_dir / "flight.h264").exists()
    run.close()


def test_append_event_writes_monotonic_sequence_and_unique_event_ids(tmp_path):
    run = _open_run(tmp_path)
    first = run.append_event("GOAL_ACCEPTED", {"goal": "count"}, now=NOW)
    second = run.append_event("GOAL_ACCEPTED", {"goal": "count-again"}, now=NOW)
    assert first.sequence_no == 1
    assert second.sequence_no == 2
    assert first.event_id != second.event_id
    run.close()


def test_raw_jsonl_sequence_continues_on_reopen(tmp_path):
    manifest = _manifest()
    run = artifacts.RunArtifacts.open(tmp_path, manifest, now=NOW)
    run.append_event("A", {"i": 1}, now=NOW)
    run.append_event("A", {"i": 2}, now=NOW)
    run.close()

    reopened = artifacts.RunArtifacts.open(tmp_path, manifest, now=NOW)
    third = reopened.append_event("A", {"i": 3}, now=NOW)
    assert third.sequence_no == 3

    lines = (tmp_path / manifest.run_id / "raw.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    records = [json.loads(line) for line in lines]
    assert [record["sequence_no"] for record in records] == [1, 2, 3]
    assert len({record["event_id"] for record in records}) == 3
    reopened.close()


def test_reopen_with_a_different_manifest_is_refused(tmp_path):
    manifest = _manifest()
    run = artifacts.RunArtifacts.open(tmp_path, manifest, now=NOW)
    run.close()
    # Force the actual collision this test is named for: same run_id, different
    # content. (The previous object.__setattr__(different, "run_id", ...) line
    # was a no-op -- both manifests already share run_id="run-0001" by
    # construction -- so this test would not have noticed _manifest() ever
    # starting to generate distinct run_ids.)
    different = _manifest(run_id=manifest.run_id, requested_capability="survey.other")
    assert different.run_id == manifest.run_id
    assert different != manifest
    with pytest.raises(artifacts.ArtifactError):
        artifacts.RunArtifacts.open(tmp_path, different, now=NOW)


def test_append_event_fsyncs_raw_jsonl(tmp_path, monkeypatch):
    import os as os_module

    run = _open_run(tmp_path)
    raw_path = tmp_path / run.manifest.run_id / "raw.jsonl"
    real_open = os_module.open
    real_fsync = os_module.fsync
    opened_raw_fds = []
    synced_fds = []

    def spy_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if str(path) == str(raw_path):
            opened_raw_fds.append(fd)
        return fd

    def spy_fsync(fd):
        synced_fds.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os_module, "open", spy_open)
    monkeypatch.setattr(os_module, "fsync", spy_fsync)
    run.append_event("GOAL_ACCEPTED", {"goal": "count"}, now=NOW)

    # Weak version of this test only counted global os.fsync calls, so it
    # would not notice an unrelated fd being synced instead of raw.jsonl's.
    assert opened_raw_fds, "raw.jsonl was never opened for the append"
    assert opened_raw_fds[-1] in synced_fds, "the raw.jsonl fd itself was never fsynced"
    # By the time append_event returns, the line must already be durable on
    # disk -- not merely fsynced after being reported to the caller.
    assert raw_path.read_text(encoding="utf-8").strip() != ""
    run.close()


def test_append_event_rejects_credential_payload_key(tmp_path):
    run = _open_run(tmp_path)
    with pytest.raises(artifacts.ArtifactError):
        run.append_event("A", {"api_key": "secret"}, now=NOW)
    run.close()


def test_append_event_rejects_credential_nested_in_list(tmp_path):
    run = _open_run(tmp_path)
    with pytest.raises(artifacts.ArtifactError):
        run.append_event("A", {"items": [{"authorization": "Bearer xyz"}]}, now=NOW)
    run.close()


# ---------------------------------------------------------------------------
# Resolved identity evidence: append-only, fails closed, never touches manifest.json
# ---------------------------------------------------------------------------

def test_record_resolved_identity_is_idempotent_for_the_same_value(tmp_path):
    run = _open_run(tmp_path)
    first = run.record_resolved_identity(role="jev", resolved="jev-vendor-x", observed_at=NOW)
    second = run.record_resolved_identity(role="jev", resolved="jev-vendor-x", observed_at=NOW)
    assert first.sequence_no == 1
    assert second.sequence_no == 2
    run.close()


def test_record_resolved_identity_fails_closed_on_conflicting_observation(tmp_path):
    run = _open_run(tmp_path)
    run.record_resolved_identity(role="jev", resolved="jev-vendor-x", observed_at=NOW)
    with pytest.raises(artifacts.ArtifactError):
        run.record_resolved_identity(role="jev", resolved="jev-vendor-other", observed_at=NOW)
    run.close()


def test_record_resolved_identity_never_touches_manifest_json(tmp_path):
    run = _open_run(tmp_path)
    manifest_path = tmp_path / run.manifest.run_id / "manifest.json"
    before = manifest_path.read_bytes()
    run.record_resolved_identity(role="jev", resolved="jev-vendor-x", observed_at=NOW)
    after = manifest_path.read_bytes()
    assert before == after
    run.close()


# ---------------------------------------------------------------------------
# finalize: credential rejection, immutability, interrupted finalization
# ---------------------------------------------------------------------------

def test_finalize_rejects_credential_in_terminal_record(tmp_path):
    run = _open_run(tmp_path)
    with pytest.raises(artifacts.ArtifactError):
        run.finalize({"state": "COMPLETED", "api_key": "secret"}, now=NOW)
    run_dir = tmp_path / run.manifest.run_id
    assert not (run_dir / "terminal.json").exists()
    assert not (run_dir / "summary.json").exists()
    run.close()


def test_finalize_checks_forbidden_content_in_both_terminal_and_summary_documents(tmp_path, monkeypatch):
    run = _open_run(tmp_path)
    seen = []
    original = artifacts.reject_forbidden

    def spy(value):
        seen.append(value)
        return original(value)

    monkeypatch.setattr(artifacts, "reject_forbidden", spy)
    run.finalize({"state": "COMPLETED"}, now=NOW)
    # One call for the terminal document, one for the summary document.
    assert any("event_count" in doc for doc in seen), seen
    assert any("finalized_at" in doc and "event_count" not in doc for doc in seen), seen
    run.close()


def test_finalize_terminal_record_is_immutable(tmp_path):
    run = _open_run(tmp_path)
    run.finalize({"state": "COMPLETED"}, now=NOW)
    run_dir = tmp_path / run.manifest.run_id
    first_bytes = (run_dir / "terminal.json").read_bytes()

    run.finalize({"state": "COMPLETED"}, now=NOW)  # byte-identical repeat: no-op
    assert (run_dir / "terminal.json").read_bytes() == first_bytes

    with pytest.raises(artifacts.ArtifactError):
        run.finalize({"state": "ABORTED"}, now=NOW)
    assert (run_dir / "terminal.json").read_bytes() == first_bytes
    run.close()


def test_interrupted_finalization_leaves_the_run_inspectable(tmp_path, monkeypatch):
    run = _open_run(tmp_path)
    run.append_event("A", {"i": 1}, now=NOW)
    run.append_event("A", {"i": 2}, now=NOW)

    def boom(document):
        raise RuntimeError("summary serialization exploded")

    monkeypatch.setattr(artifacts, "_serialize_summary", boom)
    with pytest.raises(RuntimeError):
        run.finalize({"state": "COMPLETED"}, now=NOW)

    run_dir = tmp_path / run.manifest.run_id
    assert not (run_dir / "terminal.json").exists()
    assert not (run_dir / "terminal.json.tmp").exists()
    assert not (run_dir / "summary.json").exists()

    # manifest.json and every previously appended raw.jsonl line still parse.
    artifacts.parse_manifest((run_dir / "manifest.json").read_bytes())
    lines = (run_dir / "raw.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)
    run.close()


# ---------------------------------------------------------------------------
# backup_sqlite: quiescent WAL backup opens without recovery
# ---------------------------------------------------------------------------

def test_backup_sqlite_opens_without_recovery_and_matches_appended_events(tmp_path):
    run = _open_run(tmp_path)
    run.append_event("A", {"i": 1}, now=NOW)
    run.append_event("A", {"i": 2}, now=NOW)
    appended = run.store.events(run.manifest.run_id)

    destination = tmp_path / "backup" / "session.sqlite3"
    result = run.backup_sqlite(destination)
    assert result == destination

    connection = sqlite3.connect(str(destination))
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"
        rows = connection.execute(
            "SELECT sequence_no, event_id, event_type FROM room_events "
            "WHERE session_id = ? ORDER BY sequence_no",
            (run.manifest.run_id,),
        ).fetchall()
    finally:
        connection.close()

    assert [tuple(row) for row in rows] == [
        (event.sequence_no, event.event_id, event.event_type) for event in appended
    ]
    assert not destination.with_name(destination.name + "-wal").exists()
    assert not destination.with_name(destination.name + "-journal").exists()
    run.close()


# ---------------------------------------------------------------------------
# append_event: raw.jsonl is only ever a mirror of what the store committed
# ---------------------------------------------------------------------------

def test_append_event_does_not_write_raw_jsonl_when_the_store_rejects_the_event(tmp_path, monkeypatch):
    run = _open_run(tmp_path)
    raw_path = tmp_path / run.manifest.run_id / "raw.jsonl"

    def boom(**kwargs):
        raise RuntimeError("store rejected the event")

    monkeypatch.setattr(run.store, "append_event_current", boom)
    with pytest.raises(RuntimeError):
        run.append_event("BOOM", {"i": 1}, now=NOW)
    # No orphan line: the store rejected the event before raw.jsonl was touched.
    assert not raw_path.exists()

    monkeypatch.undo()
    first = run.append_event("OK", {"i": 1}, now=NOW)
    assert first.sequence_no == 1
    assert first.event_id == f"{run.manifest.run_id}:{1:012d}"
    lines = raw_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    run.close()


def test_reopening_after_a_store_rejection_does_not_reuse_the_sequence_no_or_event_id(tmp_path, monkeypatch):
    manifest = _manifest()
    run = artifacts.RunArtifacts.open(tmp_path, manifest, now=NOW)
    run.append_event("A", {"i": 1}, now=NOW)

    def boom(**kwargs):
        raise RuntimeError("store rejected the event")

    monkeypatch.setattr(run.store, "append_event_current", boom)
    with pytest.raises(RuntimeError):
        run.append_event("BOOM", {"i": 2}, now=NOW)
    run.close()
    monkeypatch.undo()

    reopened = artifacts.RunArtifacts.open(tmp_path, manifest, now=NOW)
    second = reopened.append_event("A", {"i": 2}, now=NOW)
    # sequence_no is derived from the store (which only ever saw one committed
    # event), not from raw.jsonl, so it is neither reused (1 again) nor
    # corrupted by the rejected attempt.
    assert second.sequence_no == 2
    lines = (tmp_path / manifest.run_id / "raw.jsonl").read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]
    assert [record["sequence_no"] for record in records] == [1, 2]
    assert len({record["event_id"] for record in records}) == 2
    reopened.close()


# ---------------------------------------------------------------------------
# resolved identities are rebuilt from the durable log on every open, not
# just remembered for the lifetime of one open() call
# ---------------------------------------------------------------------------

def test_resolved_identity_conflict_is_detected_across_a_reopen(tmp_path):
    manifest = _manifest()
    run = artifacts.RunArtifacts.open(tmp_path, manifest, now=NOW)
    run.record_resolved_identity(role="jev", resolved="vendor-x", observed_at=NOW)
    run.close()

    reopened = artifacts.RunArtifacts.open(tmp_path, manifest, now=NOW)
    with pytest.raises(artifacts.ArtifactError):
        reopened.record_resolved_identity(role="jev", resolved="vendor-EVIL", observed_at=NOW)
    reopened.close()

    # The first observation -- not the rejected, conflicting one -- is what a
    # later finalize() records as the run's resolved identity.
    third = artifacts.RunArtifacts.open(tmp_path, manifest, now=NOW)
    third.finalize({"state": "COMPLETED"}, now=NOW)
    terminal = json.loads((tmp_path / manifest.run_id / "terminal.json").read_bytes())
    assert terminal["resolved_identities"]["jev"] == "vendor-x"
    third.close()


def test_record_resolved_identity_is_still_idempotent_for_the_same_value_across_a_reopen(tmp_path):
    manifest = _manifest()
    run = artifacts.RunArtifacts.open(tmp_path, manifest, now=NOW)
    run.record_resolved_identity(role="jev", resolved="vendor-x", observed_at=NOW)
    run.close()

    reopened = artifacts.RunArtifacts.open(tmp_path, manifest, now=NOW)
    # Re-observing the *same* identity after a reopen must still be accepted.
    reopened.record_resolved_identity(role="jev", resolved="vendor-x", observed_at=NOW)
    reopened.close()


# ---------------------------------------------------------------------------
# reject_forbidden: a wider credential-key-name blocklist than store.py's own
# (store.py is outside this packet's owned files, so this is defense in depth
# fully inside artifacts.py -- see final concerns)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "secret", "private_key", "passphrase", "session_key", "signing_key",
    "bearer", "cookie", "ssh_key",
])
def test_reject_forbidden_blocks_a_wider_set_of_credential_key_names(key):
    with pytest.raises(artifacts.ArtifactError):
        artifacts.reject_forbidden({key: "should-never-be-stored"})


def test_reject_forbidden_still_allows_the_authorization_digest_field():
    # The wider stem list must not collide with the manifest's own derived
    # digest field.
    artifacts.reject_forbidden({artifacts.DIGEST_FIELD: "0" * 64})


def test_manifest_rejects_a_wider_credential_key_name_in_a_free_form_field():
    manifest = _manifest(home_policy={"private_key": "sk-secret"})
    with pytest.raises(artifacts.ArtifactError):
        manifest.to_document()


def test_append_event_rejects_a_wider_credential_key_name_nested_in_a_list(tmp_path):
    run = _open_run(tmp_path)
    with pytest.raises(artifacts.ArtifactError):
        run.append_event("A", {"nested": [{"passphrase": "sk-secret"}]}, now=NOW)
    run.close()


def test_finalize_rejects_a_wider_credential_key_name(tmp_path):
    run = _open_run(tmp_path)
    with pytest.raises(artifacts.ArtifactError):
        run.finalize({"state": "COMPLETED", "session_key": "sk-secret"}, now=NOW)
    run_dir = tmp_path / run.manifest.run_id
    assert not (run_dir / "terminal.json").exists()
    run.close()


# ---------------------------------------------------------------------------
# parse_manifest: the shipped JSON Schema is actually applied at runtime
# ---------------------------------------------------------------------------

def test_parse_manifest_rejects_a_schema_invalid_but_digest_consistent_document():
    manifest = _manifest()
    document = dict(manifest.to_document())
    document["max_duration_s"] = -1.0
    document["max_recovery_attempts"] = -5
    document["aircraft_id"] = " "
    document[artifacts.DIGEST_FIELD] = artifacts.authorization_digest(document)
    with pytest.raises(artifacts.ManifestError):
        artifacts.parse_manifest(json.dumps(document).encode("utf-8"))


def test_write_manifest_refuses_what_parse_manifest_would_refuse(tmp_path):
    """The write path is as strict as the read path.

    A preview that writes a manifest the live run cannot load is a trap: the digest is printed,
    the file looks authoritative, and the refusal only arrives at flight time.
    """
    manifest = _manifest()
    out = tmp_path / "manifest.json"

    for field, value in (("site_sha256", "not-a-digest"), ("max_duration_s", -1.0),
                         ("max_recovery_attempts", -5)):
        invalid = dataclasses.replace(manifest, **{field: value})
        with pytest.raises(artifacts.ManifestError):
            artifacts.write_manifest(out, invalid)
        assert not out.exists(), f"{field} left a file behind after a refused write"

    artifacts.write_manifest(out, manifest)
    assert out.read_bytes() == manifest.canonical_bytes()
    assert artifacts.parse_manifest(out.read_bytes()) == manifest
