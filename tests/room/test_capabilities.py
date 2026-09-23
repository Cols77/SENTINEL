"""Failing-first tests for src/sentinel/room/capabilities.py (Plan 06 Task 1, CAPABILITIES half).

Everything here is OFFLINE: no provider, network, drone client or hardware object is
constructed anywhere in this file.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError as JsonSchemaValidationError

from sentinel.room import capabilities
from sentinel.room.artifacts import SessionManifest
from sentinel.room.models import (
    AcceptanceManifest,
    CapabilityGrant,
    canonical_acceptance_manifest_sha256,
    canonical_grant_sha256,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "superpowers" / "specs" / "contracts" / "room-capability-grant-v1.schema.json"
)
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "room_agent" / "v1" / "capability-grants.json"
)

NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(hours=1)).isoformat()
PAST = (NOW - timedelta(hours=1)).isoformat()


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _acceptance_manifest(session_id: str, capability: str, **overrides: object) -> AcceptanceManifest:
    manifest = AcceptanceManifest(
        session_id=session_id, capability=capability, manifest_sha256="", **overrides,
    )
    return replace(manifest, manifest_sha256=canonical_acceptance_manifest_sha256(manifest))


def _promoted_grant(**overrides: object) -> CapabilityGrant:
    grant = CapabilityGrant(
        capability="survey.count",
        state="PROMOTED",
        site_sha256=_hash("site"),
        aircraft_id="aircraft-1",
        profile_sha256s=(),
        evidence_ids=(),
        expires_at=FUTURE,
        grant_sha256="",
    )
    grant = replace(grant, **overrides)
    return replace(grant, grant_sha256=canonical_grant_sha256(grant))


def _trial_grant(*, session_id: str, capability: str = "survey.count", **overrides: object) -> CapabilityGrant:
    acceptance_manifest = _acceptance_manifest(session_id, capability)
    grant = CapabilityGrant(
        capability=capability,
        state="TRIAL",
        site_sha256=_hash("site"),
        aircraft_id="aircraft-1",
        profile_sha256s=(),
        evidence_ids=(),
        expires_at=FUTURE,
        grant_sha256="",
        acceptance_manifest_sha256=acceptance_manifest.manifest_sha256,
    )
    grant = replace(grant, **overrides)
    return replace(grant, grant_sha256=canonical_grant_sha256(grant))


def _manifest(**overrides: object) -> SessionManifest:
    kwargs = dict(
        schema_version="room-session-manifest-v1",
        run_id="run-0001",
        created_at="2026-09-23T00:00:00+00:00",
        mode="live",
        code_revision="abc123",
        code_dirty=False,
        integration_revision="def456",
        requested_capability="survey.count",
        capability_grant_sha256=_hash("placeholder"),
        capability_grant_state="PROMOTED",
        request_revision="req-1",
        goal_parser_revision="goal-1",
        site_sha256=_hash("site"),
        tag_map_sha256=_hash("tagmap"),
        camera_sha256=_hash("camera"),
        profile_sha256s=(),
        aircraft_id="aircraft-1",
        firmware_version="1.0.0",
        requested_jev_identity="jev-vendor-x",
        resolved_jev_identity="UNVERIFIED",
        requested_detector_identity="detector-vendor-y",
        resolved_detector_identity="UNVERIFIED",
        max_duration_s=120.0,
        max_distance_cm=500.0,
        max_recovery_attempts=2,
        provider_deadlines={"jev_decision": 5.0},
        cost_assumptions={},
        home_policy={},
        termination_policy={},
        consent_references=(),
        trial_protocol=None,
        evidence_destination=None,
    )
    kwargs.update(overrides)
    return SessionManifest(**kwargs)


def _evidence_index(**overrides: object) -> capabilities.EvidenceIndex:
    kwargs = dict(trial_grant_sha256=_hash("trial"), evidence_ids=("ev-1",), passed=True)
    kwargs.update(overrides)
    return capabilities.EvidenceIndex(**kwargs)


# ---------------------------------------------------------------------------
# GrantRegistry: construction, canonical form, round trip
# ---------------------------------------------------------------------------

def test_empty_registry_has_no_grants_and_correct_schema_version():
    registry = capabilities.empty_registry()
    # Compare against the literal wire value, not the module constant: comparing
    # a constant against itself is tautological and would not notice
    # SCHEMA_VERSION drifting away from what the wire format actually says.
    assert registry.schema_version == "room-capability-grant-v1"
    assert registry.grants == ()
    assert registry.trial_bindings == ()


def test_registry_canonical_bytes_is_sorted_and_compact():
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(_promoted_grant(),))
    raw = registry.canonical_bytes()
    text_form = raw.decode("utf-8")
    document = json.loads(raw)
    assert document["schema_version"] == capabilities.SCHEMA_VERSION
    assert len(document["grants"]) == 1

    # Compact: no whitespace at all in the separators json.dumps would
    # otherwise insert by default (", " / ": ").
    assert ", " not in text_form
    assert ": " not in text_form

    # Sorted: re-serializing with sort_keys must be a no-op, at every nesting
    # level (top-level registry keys AND each grant's own keys).
    assert json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":")) == text_form
    top_level_keys = list(document.keys())
    assert top_level_keys == sorted(top_level_keys)
    grant_keys = list(document["grants"][0].keys())
    assert grant_keys == sorted(grant_keys)

    assert registry.canonical_bytes() == registry.canonical_bytes()  # deterministic


def test_parse_registry_round_trips_write_registry(tmp_path):
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(_promoted_grant(),))
    path = tmp_path / "registry.json"
    capabilities.write_registry(path, registry)
    loaded = capabilities.load_registry(path)
    assert loaded == registry


def test_write_registry_is_byte_identical_to_canonical_bytes(tmp_path):
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(_promoted_grant(),))
    path = tmp_path / "registry.json"
    capabilities.write_registry(path, registry)
    assert path.read_bytes() == registry.canonical_bytes()
    assert not path.with_name("registry.json.tmp").exists()


def test_parse_registry_rejects_grant_hash_mismatch():
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(_promoted_grant(),))
    document = registry.to_document()
    document["grants"][0]["grant_sha256"] = "0" * 64
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.parse_registry(json.dumps(document).encode("utf-8"))


def test_parse_registry_rejects_unknown_field():
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(_promoted_grant(),))
    document = registry.to_document()
    document["grants"][0]["extra_field"] = "nope"
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.parse_registry(json.dumps(document).encode("utf-8"))


def test_parse_registry_rejects_duplicate_grants():
    grant = _promoted_grant()
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(grant, grant))
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.parse_registry(registry.canonical_bytes())


def test_parse_registry_rejects_wrong_schema_version():
    document = {"schema_version": "room-capability-grant-v0", "grants": []}
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.parse_registry(json.dumps(document).encode("utf-8"))


# ---------------------------------------------------------------------------
# issue_trial
# ---------------------------------------------------------------------------

def test_issue_trial_returns_new_registry_and_leaves_the_original_untouched():
    registry = capabilities.empty_registry()
    acceptance_manifest = _acceptance_manifest("run-0001", "survey.count")
    new_registry, grant = capabilities.issue_trial(
        registry, capability="survey.count", trial_protocol="protocol-1",
        site_sha256=_hash("site"), aircraft_id="aircraft-1", profile_sha256s=(),
        evidence_destination="evidence://dest", expires_at=FUTURE,
        acceptance_manifest=acceptance_manifest,
    )
    assert registry.grants == ()  # original untouched (frozen, proven by identity)
    assert new_registry.grants == (grant,)
    assert grant.state == "TRIAL"
    assert grant.capability == "survey.count"
    assert grant.acceptance_manifest_sha256 == acceptance_manifest.manifest_sha256
    assert grant.grant_sha256 == canonical_grant_sha256(grant)


def test_issue_trial_allows_zero_candidate_profiles():
    registry = capabilities.empty_registry()
    acceptance_manifest = _acceptance_manifest("run-0001", "survey.count")
    _, grant = capabilities.issue_trial(
        registry, capability="survey.count", trial_protocol="protocol-1",
        site_sha256=_hash("site"), aircraft_id="aircraft-1", profile_sha256s=(),
        evidence_destination="evidence://dest", expires_at=FUTURE,
        acceptance_manifest=acceptance_manifest,
    )
    assert grant.profile_sha256s == ()


def test_issue_trial_rejects_acceptance_manifest_for_a_different_capability():
    registry = capabilities.empty_registry()
    acceptance_manifest = _acceptance_manifest("run-0001", "survey.observe")
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.issue_trial(
            registry, capability="survey.count", trial_protocol="protocol-1",
            site_sha256=_hash("site"), aircraft_id="aircraft-1", profile_sha256s=(),
            evidence_destination="evidence://dest", expires_at=FUTURE,
            acceptance_manifest=acceptance_manifest,
        )


def test_issue_trial_rejects_a_tampered_acceptance_manifest_hash():
    registry = capabilities.empty_registry()
    acceptance_manifest = replace(
        _acceptance_manifest("run-0001", "survey.count"), manifest_sha256="0" * 64,
    )
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.issue_trial(
            registry, capability="survey.count", trial_protocol="protocol-1",
            site_sha256=_hash("site"), aircraft_id="aircraft-1", profile_sha256s=(),
            evidence_destination="evidence://dest", expires_at=FUTURE,
            acceptance_manifest=acceptance_manifest,
        )


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

def test_promote_writes_promoted_grant_without_mutating_the_trial():
    trial = _trial_grant(session_id="run-0001")
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial,))
    evidence_index = _evidence_index(trial_grant_sha256=trial.grant_sha256, evidence_ids=("ev-1", "ev-2"))
    new_registry, promoted = capabilities.promote(
        registry, trial_grant=trial, evidence_index=evidence_index, expires_at=FUTURE, now=NOW,
    )
    assert trial in new_registry.grants
    assert new_registry.grants[new_registry.grants.index(trial)] == trial  # byte-identical, untouched
    assert promoted.state == "PROMOTED"
    assert promoted.capability == trial.capability
    assert promoted.site_sha256 == trial.site_sha256
    assert promoted.aircraft_id == trial.aircraft_id
    assert set(promoted.evidence_ids) == {"ev-1", "ev-2"}
    assert promoted.grant_sha256 == canonical_grant_sha256(promoted)
    assert registry.grants == (trial,)  # original registry untouched


def test_promote_refuses_a_simulation_only_grant():
    grant = _promoted_grant(state="SIMULATION_ONLY", acceptance_manifest_sha256=None)
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(grant,))
    evidence_index = _evidence_index(trial_grant_sha256=grant.grant_sha256)
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.promote(registry, trial_grant=grant, evidence_index=evidence_index, expires_at=FUTURE, now=NOW)


def test_promote_refuses_an_evidence_index_that_does_not_match_the_trial():
    trial = _trial_grant(session_id="run-0001")
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial,))
    evidence_index = _evidence_index(trial_grant_sha256="0" * 64)
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.promote(registry, trial_grant=trial, evidence_index=evidence_index, expires_at=FUTURE, now=NOW)


def test_promote_refuses_evidence_that_has_not_passed():
    trial = _trial_grant(session_id="run-0001")
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial,))
    evidence_index = _evidence_index(trial_grant_sha256=trial.grant_sha256, passed=False)
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.promote(registry, trial_grant=trial, evidence_index=evidence_index, expires_at=FUTURE, now=NOW)


# ---------------------------------------------------------------------------
# admit_live
# ---------------------------------------------------------------------------

def test_admit_live_admits_the_matching_unexpired_promoted_grant():
    grant = _promoted_grant()
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(grant,))
    manifest = _manifest(mode="live", capability_grant_sha256=grant.grant_sha256, capability_grant_state="PROMOTED")
    admitted = capabilities.admit_live(registry, manifest=manifest, now=NOW)
    assert admitted == grant


def test_admit_live_refuses_an_expired_promoted_grant():
    grant = _promoted_grant(expires_at=PAST)
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(grant,))
    manifest = _manifest(mode="live", capability_grant_sha256=grant.grant_sha256, capability_grant_state="PROMOTED")
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.admit_live(registry, manifest=manifest, now=NOW)


def test_admit_live_refuses_simulation_only_and_disabled_grants_in_live_mode():
    for state in ("SIMULATION_ONLY", "DISABLED"):
        grant = _promoted_grant(state=state, acceptance_manifest_sha256=None)
        registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(grant,))
        manifest = _manifest(mode="live", capability_grant_sha256=grant.grant_sha256, capability_grant_state=state)
        with pytest.raises(capabilities.GrantRegistryError):
            capabilities.admit_live(registry, manifest=manifest, now=NOW)


def test_admit_live_refuses_simulation_only_and_disabled_grants_in_acceptance_mode():
    for state in ("SIMULATION_ONLY", "DISABLED"):
        grant = _promoted_grant(state=state, acceptance_manifest_sha256=None)
        registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(grant,))
        manifest = _manifest(
            mode="acceptance", capability_grant_sha256=grant.grant_sha256, capability_grant_state=state,
            trial_protocol="protocol-1", evidence_destination="evidence://dest",
        )
        with pytest.raises(capabilities.GrantRegistryError):
            capabilities.admit_live(registry, manifest=manifest, now=NOW)


def test_admit_live_a_trial_grant_never_authorises_ordinary_live_mode():
    trial = _trial_grant(session_id="run-0001")
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial,))
    manifest = _manifest(mode="live", capability_grant_sha256=trial.grant_sha256, capability_grant_state="TRIAL")
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.admit_live(registry, manifest=manifest, now=NOW)


def test_admit_live_a_trial_grant_never_authorises_a_capability_it_does_not_name():
    trial = _trial_grant(session_id="run-0001", capability="survey.count")
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial,))
    manifest = _manifest(
        mode="acceptance", requested_capability="survey.observe",
        capability_grant_sha256=trial.grant_sha256, capability_grant_state="TRIAL",
        trial_protocol="protocol-1", evidence_destination="evidence://dest",
    )
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.admit_live(registry, manifest=manifest, now=NOW)


def test_admit_live_acceptance_mode_admits_exactly_one_matching_trial_grant():
    trial = _trial_grant(session_id="run-0001")
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial,))
    manifest = _manifest(
        mode="acceptance", capability_grant_sha256=trial.grant_sha256, capability_grant_state="TRIAL",
        trial_protocol="protocol-1", evidence_destination="evidence://dest",
    )
    admitted = capabilities.admit_live(registry, manifest=manifest, now=NOW)
    assert admitted == trial


def test_admit_live_acceptance_mode_refuses_two_matching_trial_grants():
    trial_a = _trial_grant(session_id="run-0001")
    trial_b = _trial_grant(session_id="run-0001", expires_at=(NOW + timedelta(hours=2)).isoformat())
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial_a, trial_b))
    manifest = _manifest(
        mode="acceptance", capability_grant_sha256=trial_a.grant_sha256, capability_grant_state="TRIAL",
        trial_protocol="protocol-1", evidence_destination="evidence://dest",
    )
    # Both trials bind the same site/aircraft/capability/session -- ambiguous even though the
    # manifest names one grant's hash, because admission must resolve to a single unambiguous
    # authorization for the (capability, session) pair, not just a hash lookup.
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.admit_live(registry, manifest=manifest, now=NOW)


def test_admit_live_refuses_any_mode_other_than_live_or_acceptance():
    grant = _promoted_grant()
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(grant,))
    for mode in ("preview", "replay", "simulate"):
        manifest = _manifest(mode=mode, capability_grant_sha256=grant.grant_sha256)
        with pytest.raises(capabilities.GrantRegistryError):
            capabilities.admit_live(registry, manifest=manifest, now=NOW)


@pytest.mark.parametrize("field,new_value", [
    ("site_sha256", "different-site-hash"),
    ("aircraft_id", "different-aircraft"),
    ("profile_sha256s", (_hash("other-profile"),)),
    ("evidence_ids", ("different-evidence",)),
    ("expires_at", (NOW + timedelta(hours=2)).isoformat()),
])
def test_admit_live_refuses_when_any_grant_binding_field_changes(field, new_value):
    grant = _promoted_grant()
    tampered = replace(grant, **{field: new_value})  # grant_sha256 left stale on purpose
    assert canonical_grant_sha256(tampered) != tampered.grant_sha256
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(tampered,))
    manifest = _manifest(mode="live", capability_grant_sha256=grant.grant_sha256, capability_grant_state="PROMOTED")
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.admit_live(registry, manifest=manifest, now=NOW)


# ---------------------------------------------------------------------------
# JSON Schema
# ---------------------------------------------------------------------------

def test_registry_schema_accepts_a_valid_document():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(_promoted_grant(),))
    Draft202012Validator(schema).validate(json.loads(registry.canonical_bytes()))


def test_registry_schema_rejects_an_unknown_key():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(_promoted_grant(),))
    document = {**registry.to_document(), "unexpected_field": True}
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(schema).validate(document)


# ---------------------------------------------------------------------------
# fixtures/room_agent/v1/capability-grants.json
# ---------------------------------------------------------------------------

def test_fixture_registry_validates_against_the_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    document = json.loads(FIXTURE_PATH.read_bytes())
    Draft202012Validator(schema).validate(document)


def test_fixture_registry_hashes_are_correctly_recomputed():
    registry = capabilities.load_registry(FIXTURE_PATH)
    assert registry.grants, "fixture must contain at least one grant"
    for grant in registry.grants:
        assert grant.state == "SIMULATION_ONLY"
        assert grant.grant_sha256 == canonical_grant_sha256(grant)


def test_fixture_registry_grants_are_all_refused_by_admit_live():
    registry = capabilities.load_registry(FIXTURE_PATH)
    for grant in registry.grants:
        live_manifest = _manifest(
            mode="live", requested_capability=grant.capability,
            capability_grant_sha256=grant.grant_sha256, capability_grant_state=grant.state,
            site_sha256=grant.site_sha256 or _hash("site"), aircraft_id=grant.aircraft_id or "aircraft-1",
            profile_sha256s=grant.profile_sha256s,
        )
        with pytest.raises(capabilities.GrantRegistryError):
            capabilities.admit_live(registry, manifest=live_manifest, now=NOW)
        acceptance_manifest = _manifest(
            mode="acceptance", requested_capability=grant.capability,
            capability_grant_sha256=grant.grant_sha256, capability_grant_state=grant.state,
            site_sha256=grant.site_sha256 or _hash("site"), aircraft_id=grant.aircraft_id or "aircraft-1",
            profile_sha256s=grant.profile_sha256s,
            trial_protocol="protocol-1", evidence_destination="evidence://dest",
        )
        with pytest.raises(capabilities.GrantRegistryError):
            capabilities.admit_live(registry, manifest=acceptance_manifest, now=NOW)


# ---------------------------------------------------------------------------
# scripts/room_capability.py
# ---------------------------------------------------------------------------

def test_script_init_creates_an_empty_registry_and_refuses_to_overwrite(tmp_path, capsys):
    import scripts.room_capability as cli

    registry_path = tmp_path / "registry.json"
    assert cli.main(["init", "--registry", str(registry_path)]) == 0
    loaded = capabilities.load_registry(registry_path)
    assert loaded.grants == ()

    # init prints the canonical digest it acted on, like every other command --
    # never a filesystem path (a caller-supplied string with nothing to do
    # with what was actually written).
    out = capsys.readouterr().out.strip()
    assert out == hashlib.sha256(capabilities.empty_registry().canonical_bytes()).hexdigest()
    assert str(registry_path) not in out

    assert cli.main(["init", "--registry", str(registry_path)]) == 2


def test_script_issue_trial_writes_one_grant_and_prints_its_digest(tmp_path, capsys):
    import scripts.room_capability as cli

    registry_path = tmp_path / "registry.json"
    cli.main(["init", "--registry", str(registry_path)])
    capsys.readouterr()

    exit_code = cli.main([
        "issue-trial",
        "--capability", "survey.count",
        "--trial-protocol", "protocol-1",
        "--site-sha256", _hash("site"),
        "--aircraft-id", "aircraft-1",
        "--evidence-destination", "evidence://dest",
        "--expires-at", FUTURE,
        "--session-id", "run-0001",
        "--registry", str(registry_path),
    ])
    out = capsys.readouterr().out.strip()
    assert exit_code == 0
    registry = capabilities.load_registry(registry_path)
    assert len(registry.grants) == 1
    assert registry.grants[0].grant_sha256 == out


def test_script_promote_writes_promoted_grant_and_leaves_trial_intact(tmp_path, capsys):
    import scripts.room_capability as cli

    registry_path = tmp_path / "registry.json"
    cli.main(["init", "--registry", str(registry_path)])
    capsys.readouterr()
    cli.main([
        "issue-trial",
        "--capability", "survey.count",
        "--trial-protocol", "protocol-1",
        "--site-sha256", _hash("site"),
        "--aircraft-id", "aircraft-1",
        "--evidence-destination", "evidence://dest",
        "--expires-at", FUTURE,
        "--session-id", "run-0001",
        "--registry", str(registry_path),
    ])
    trial_digest = capsys.readouterr().out.strip()

    evidence_index_path = tmp_path / "evidence-index.json"
    evidence_index_path.write_text(
        json.dumps({"trial_grant_sha256": trial_digest, "evidence_ids": ["ev-1"], "passed": True}),
        encoding="utf-8",
    )
    exit_code = cli.main([
        "promote",
        "--registry", str(registry_path),
        "--trial-grant-sha256", trial_digest,
        "--evidence-index", str(evidence_index_path),
        "--expires-at", FUTURE,
        # Pinned: FUTURE is an hour after the fixed NOW, so the real clock
        # would expire the trial on the afternoon of 2026-09-23.
        "--now", NOW.isoformat(),
    ])
    out = capsys.readouterr().out.strip()
    assert exit_code == 0
    registry = capabilities.load_registry(registry_path)
    states = {grant.grant_sha256: grant.state for grant in registry.grants}
    assert states[trial_digest] == "TRIAL"
    assert states[out] == "PROMOTED"


@pytest.mark.parametrize("argv_factory", [
    lambda registry_path: ["init", "--registry", str(registry_path)],
    lambda registry_path: [
        "issue-trial", "--capability", "survey.count", "--trial-protocol", "protocol-1",
        "--site-sha256", _hash("site"), "--aircraft-id", "aircraft-1",
        "--evidence-destination", "evidence://dest", "--expires-at", FUTURE,
        "--session-id", "run-0001", "--registry", str(registry_path),
    ],
])
def test_script_never_prints_a_poisoned_environment_variable(tmp_path, capsys, monkeypatch, argv_factory):
    import scripts.room_capability as cli

    monkeypatch.setenv("SENTINEL_POISON_SECRET", "poison-value-should-never-print")
    registry_path = tmp_path / "registry.json"
    cli.main(argv_factory(registry_path))
    captured = capsys.readouterr()
    assert "poison-value-should-never-print" not in captured.out
    assert "poison-value-should-never-print" not in captured.err



# ---------------------------------------------------------------------------
# promote: expired trial cannot be laundered, zero-evidence cannot be promoted
# ---------------------------------------------------------------------------

def test_promote_refuses_an_expired_trial_grant():
    trial = _trial_grant(session_id="run-0001", expires_at=PAST)
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial,))
    evidence_index = _evidence_index(trial_grant_sha256=trial.grant_sha256)
    # admit_live already refuses this trial (it is expired); promote must not
    # be able to launder it into a fresh, unexpired PROMOTED grant -- nothing
    # in EvidenceIndex establishes the evidence was collected inside the
    # trial's validity window.
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.promote(registry, trial_grant=trial, evidence_index=evidence_index, expires_at=FUTURE, now=NOW)


def test_promote_refuses_an_evidence_index_with_zero_evidence_ids():
    trial = _trial_grant(session_id="run-0001")
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial,))
    evidence_index = _evidence_index(trial_grant_sha256=trial.grant_sha256, evidence_ids=())
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.promote(registry, trial_grant=trial, evidence_index=evidence_index, expires_at=FUTURE, now=NOW)


def test_promote_requires_a_timezone_aware_now():
    trial = _trial_grant(session_id="run-0001")
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial,))
    evidence_index = _evidence_index(trial_grant_sha256=trial.grant_sha256)
    naive_now = datetime(2026, 9, 23, 12, 0, 0)  # no tzinfo
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.promote(
            registry, trial_grant=trial, evidence_index=evidence_index, expires_at=FUTURE, now=naive_now,
        )


# ---------------------------------------------------------------------------
# admit_live: acceptance-mode trial_protocol/evidence_destination binding,
# manifest.capability_grant_state must not misreport the admitted grant, and
# the acceptance-mode grant_sha256 check on its own (weak-test coverage gap).
# ---------------------------------------------------------------------------

def _issued_trial_registry(
    *, trial_protocol="protocol-1", evidence_destination="evidence://dest",
    session_id="run-0001", capability="survey.count",
):
    acceptance_manifest = _acceptance_manifest(session_id, capability)
    registry, grant = capabilities.issue_trial(
        capabilities.empty_registry(), capability=capability, trial_protocol=trial_protocol,
        site_sha256=_hash("site"), aircraft_id="aircraft-1", profile_sha256s=(),
        evidence_destination=evidence_destination, expires_at=FUTURE,
        acceptance_manifest=acceptance_manifest,
    )
    return registry, grant


def test_issue_trial_records_a_trial_binding_for_the_grant():
    registry, grant = _issued_trial_registry()
    binding = registry.trial_binding_for(grant.grant_sha256)
    assert binding is not None
    assert binding.trial_protocol == "protocol-1"
    assert binding.evidence_destination == "evidence://dest"


def test_admit_live_acceptance_mode_accepts_the_exact_binding_it_was_issued_with():
    registry, grant = _issued_trial_registry()
    manifest = _manifest(
        mode="acceptance", capability_grant_sha256=grant.grant_sha256, capability_grant_state="TRIAL",
        trial_protocol="protocol-1", evidence_destination="evidence://dest",
    )
    admitted = capabilities.admit_live(registry, manifest=manifest, now=NOW)
    assert admitted == grant


def test_admit_live_acceptance_mode_refuses_a_mismatched_trial_protocol():
    registry, grant = _issued_trial_registry()
    manifest = _manifest(
        mode="acceptance", capability_grant_sha256=grant.grant_sha256, capability_grant_state="TRIAL",
        trial_protocol="ANY-STRING-AT-ALL", evidence_destination="evidence://dest",
    )
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.admit_live(registry, manifest=manifest, now=NOW)


def test_admit_live_acceptance_mode_refuses_a_mismatched_evidence_destination():
    registry, grant = _issued_trial_registry()
    manifest = _manifest(
        mode="acceptance", capability_grant_sha256=grant.grant_sha256, capability_grant_state="TRIAL",
        trial_protocol="protocol-1", evidence_destination="evidence://attacker-controlled",
    )
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.admit_live(registry, manifest=manifest, now=NOW)


def test_admit_live_refuses_when_manifest_capability_grant_state_misreports_a_live_grant():
    grant = _promoted_grant()
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(grant,))
    manifest = _manifest(
        mode="live", capability_grant_sha256=grant.grant_sha256, capability_grant_state="SIMULATION_ONLY",
    )
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.admit_live(registry, manifest=manifest, now=NOW)


def test_admit_live_refuses_when_manifest_capability_grant_state_misreports_an_acceptance_grant():
    registry, grant = _issued_trial_registry()
    manifest = _manifest(
        mode="acceptance", capability_grant_sha256=grant.grant_sha256, capability_grant_state="PROMOTED",
        trial_protocol="protocol-1", evidence_destination="evidence://dest",
    )
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.admit_live(registry, manifest=manifest, now=NOW)


def test_admit_live_acceptance_mode_refuses_when_manifest_hash_does_not_equal_the_matching_trial_grant():
    # Isolates the acceptance-mode grant_sha256 <-> manifest.capability_grant_sha256
    # check on its own: the trial otherwise matches uniquely via allows(), so
    # deleting just that check would let this manifest through.
    trial = _trial_grant(session_id="run-0001")
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION, grants=(trial,))
    manifest = _manifest(
        mode="acceptance", capability_grant_sha256="0" * 64, capability_grant_state="TRIAL",
        trial_protocol="protocol-1", evidence_destination="evidence://dest",
    )
    with pytest.raises(capabilities.GrantRegistryError):
        capabilities.admit_live(registry, manifest=manifest, now=NOW)
