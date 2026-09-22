from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.room.models import (
    AcceptanceManifest,
    CandidateKind,
    CapabilityGrant,
    CapabilityProfile,
    GoalKind,
    GoalStatus,
    PhysicalCost,
    RoomCandidate,
    RoomSnapshot,
    TerminalState,
    canonical_grant_sha256,
)


def _grant(**changes: object) -> CapabilityGrant:
    grant = CapabilityGrant(
        capability="room.flight",
        state="PROMOTED",
        site_sha256="site-a",
        aircraft_id="tello-1",
        profile_sha256s=("flight-profile",),
        evidence_ids=("evidence-1",),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        grant_sha256="",
    )
    grant = replace(grant, **changes)
    return replace(grant, grant_sha256=canonical_grant_sha256(grant))


def test_room_contracts_are_immutable_and_expose_the_complete_enums():
    candidate = RoomCandidate(
        candidate_id="c1",
        kind=CandidateKind.READ_COUNTS,
        snapshot_version=2,
        relevant_state_sha256="a" * 64,
        description="Read current counts",
        predicted_benefit="Answers the count request",
    )
    assert GoalKind.COUNT.value == "COUNT"
    assert GoalStatus.CLARIFICATION_REQUIRED.value == "CLARIFICATION_REQUIRED"
    assert TerminalState.LANDED_AT_CERTIFIED_ZONE.value == "LANDED_AT_CERTIFIED_ZONE"
    assert CandidateKind.RETURN_HOME.value == "RETURN_HOME"
    assert candidate.physical_cost == PhysicalCost()
    with pytest.raises(FrozenInstanceError):
        candidate.description = "mutated"  # type: ignore[misc]


def test_snapshot_revisions_are_independent():
    before = RoomSnapshot("s1", 3, 1, 2, 3, 4, 5, 6, 7, 8, 9)
    after = replace(before, target_revision=8)
    assert after.target_revision == 8
    assert after.goal_revision == before.goal_revision
    assert after.pose_revision == before.pose_revision
    assert before.target_revision == 7


def test_capability_grant_hash_is_canonical_and_binds_every_grant_field():
    grant = _grant()
    reordered = replace(grant, profile_sha256s=("flight-profile",))
    assert canonical_grant_sha256(grant) == grant.grant_sha256
    assert canonical_grant_sha256(reordered) == grant.grant_sha256
    assert canonical_grant_sha256(replace(grant, site_sha256="site-b")) != grant.grant_sha256
    assert canonical_grant_sha256(replace(grant, evidence_ids=("evidence-2",))) != grant.grant_sha256
    assert CapabilityProfile((grant,)).grants == (grant,)


def test_promoted_grant_requires_exact_unexpired_site_aircraft_and_profile_bindings():
    grant = _grant()
    now = datetime.now(timezone.utc)
    assert grant.allows(
        capability="room.flight", site_sha256="site-a", aircraft_id="tello-1",
        profile_sha256s=("flight-profile",), now=now,
    )
    assert not grant.allows(
        capability="room.flight", site_sha256="site-b", aircraft_id="tello-1",
        profile_sha256s=("flight-profile",), now=now,
    )
    assert not grant.allows(
        capability="room.flight", site_sha256="site-a", aircraft_id="other",
        profile_sha256s=("flight-profile",), now=now,
    )
    assert not grant.allows(
        capability="room.flight", site_sha256="site-a", aircraft_id="tello-1",
        profile_sha256s=("other-profile",), now=now,
    )
    assert not _grant(expires_at=(now - timedelta(seconds=1)).isoformat()).allows(
        capability="room.flight", site_sha256="site-a", aircraft_id="tello-1",
        profile_sha256s=("flight-profile",), now=now,
    )


def test_trial_grant_only_allows_its_named_manifest_bound_acceptance_session():
    trial = _grant(state="TRIAL")
    manifest = AcceptanceManifest(
        session_id="acceptance:room.flight:case-1",
        capability="room.flight",
        manifest_sha256="manifest-a",
    )
    arguments = dict(
        capability="room.flight", site_sha256="site-a", aircraft_id="tello-1",
        profile_sha256s=("flight-profile",), now=datetime.now(timezone.utc),
    )
    assert not trial.allows(**arguments)
    assert trial.allows(**arguments, acceptance_manifest=manifest)
    assert not trial.allows(**arguments, acceptance_manifest=replace(manifest, capability="room.observe"))
    assert not trial.allows(**arguments, acceptance_manifest=replace(manifest, session_id="live-session"))
