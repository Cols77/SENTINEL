"""Immutable contracts used by the room session kernel."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
from typing import Literal


class GoalKind(StrEnum):
    ANSWER_STATE = "ANSWER_STATE"
    COUNT = "COUNT"
    FIND_OBSERVE = "FIND_OBSERVE"
    FOLLOW = "FOLLOW"
    LAND_NEAR = "LAND_NEAR"


class CandidateKind(StrEnum):
    READ_DRONE = "READ_DRONE"
    READ_OBSERVATIONS = "READ_OBSERVATIONS"
    READ_COUNTS = "READ_COUNTS"
    OBSERVE = "OBSERVE"
    VALIDATE_DETECTION = "VALIDATE_DETECTION"
    VERIFY_RELATION = "VERIFY_RELATION"
    REACQUIRE_TARGET = "REACQUIRE_TARGET"
    MAINTAIN_OBSERVATION = "MAINTAIN_OBSERVATION"
    FOLLOW_STEP = "FOLLOW_STEP"
    LAND_AT_ZONE = "LAND_AT_ZONE"
    RETURN_HOME = "RETURN_HOME"
    ANSWER = "ANSWER"
    CLARIFY = "CLARIFY"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class PhysicalCost:
    distance_cm: float = 0.0
    upper_bound_s: float = 0.0
    provider_wait_s: float = 0.0
    requires_airborne: bool = False


@dataclass(frozen=True)
class RoomCandidate:
    candidate_id: str
    kind: CandidateKind
    snapshot_version: int
    relevant_state_sha256: str
    description: str
    predicted_benefit: str
    evidence_refs: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    physical_cost: PhysicalCost = PhysicalCost()


class TerminalState(StrEnum):
    COMPLETED = "COMPLETED"
    INCOMPLETE_RETURNED = "INCOMPLETE_RETURNED"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    ABORTED = "ABORTED"
    RECOVERY_FAILURE = "RECOVERY_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    SAFE_LANDED = "SAFE_LANDED"
    LANDED_AT_CERTIFIED_ZONE = "LANDED_AT_CERTIFIED_ZONE"


class GoalStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SATISFIED = "SATISFIED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class RoomSnapshot:
    session_id: str
    state_version: int
    goal_revision: int
    pose_revision: int
    home_revision: int
    energy_revision: int
    coverage_revision: int
    static_evidence_revision: int
    target_revision: int
    landing_zone_revision: int
    tool_outcome_revision: int


@dataclass(frozen=True)
class AcceptanceManifest:
    """The one capability and session authorized for an acceptance trial."""

    session_id: str
    capability: str
    manifest_sha256: str


@dataclass(frozen=True)
class CapabilityGrant:
    capability: str
    state: Literal["SIMULATION_ONLY", "DISABLED", "TRIAL", "PROMOTED"]
    site_sha256: str | None
    aircraft_id: str | None
    profile_sha256s: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    expires_at: str | None
    grant_sha256: str

    def allows(
        self,
        *,
        capability: str,
        site_sha256: str | None,
        aircraft_id: str | None,
        profile_sha256s: tuple[str, ...],
        now: datetime,
        acceptance_manifest: AcceptanceManifest | None = None,
    ) -> bool:
        """Check the complete binding before a live capability is admitted."""
        if self.grant_sha256 != canonical_grant_sha256(self):
            return False
        if self.capability != capability or self.site_sha256 != site_sha256:
            return False
        if self.aircraft_id != aircraft_id:
            return False
        if tuple(sorted(self.profile_sha256s)) != tuple(sorted(profile_sha256s)):
            return False
        expiry = _parse_expiry(self.expires_at)
        if expiry is None or now.tzinfo is None or now >= expiry:
            return False
        if self.state == "PROMOTED":
            return True
        if self.state != "TRIAL" or acceptance_manifest is None:
            return False
        return (
            acceptance_manifest.capability == capability
            and acceptance_manifest.session_id.startswith(f"acceptance:{capability}:")
            and bool(acceptance_manifest.manifest_sha256)
        )


@dataclass(frozen=True)
class CapabilityProfile:
    grants: tuple[CapabilityGrant, ...]

    def allows(self, **bindings: object) -> bool:
        return any(grant.allows(**bindings) for grant in self.grants)  # type: ignore[arg-type]


def canonical_grant_sha256(grant: CapabilityGrant) -> str:
    """Hash grant content in a stable representation, excluding its signature field."""
    body = {
        "aircraft_id": grant.aircraft_id,
        "capability": grant.capability,
        "evidence_ids": sorted(grant.evidence_ids),
        "expires_at": grant.expires_at,
        "profile_sha256s": sorted(grant.profile_sha256s),
        "site_sha256": grant.site_sha256,
        "state": grant.state,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_expiry(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
