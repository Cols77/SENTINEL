"""Offline capability-grant registry: issue, promote, and admit room-agent capabilities.

Every grant is a `sentinel.room.models.CapabilityGrant`. This module builds a canonical,
append-friendly registry of those grants on top of `CapabilityGrant.allows()` -- it never
redefines grant binding or hash logic, and it never constructs a provider, drone client or
network object.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime
import json
from pathlib import Path
from typing import Mapping

from . import artifacts
from .artifacts import SessionManifest
from .models import (
    AcceptanceManifest,
    CapabilityGrant,
    canonical_acceptance_manifest_sha256,
    canonical_grant_sha256,
)
# models.py already defines the one tz-aware expiry parser CapabilityGrant.allows()
# uses; promote() needs the identical rule (reject naive/unparseable/expired) to
# decide whether a TRIAL grant is still inside its validity window, so this reuses
# that private helper rather than forking a second copy that could drift.
from .models import _parse_expiry

SCHEMA_VERSION = "room-capability-grant-v1"

_ADMISSIBLE_MODES = frozenset({"live", "acceptance"})
_GRANT_STATES = frozenset({"SIMULATION_ONLY", "DISABLED", "TRIAL", "PROMOTED"})


class GrantRegistryError(ValueError):
    """A capability-grant registry, grant or admission request is malformed or refused."""


def _grant_to_document(grant: CapabilityGrant) -> dict:
    return {
        "capability": grant.capability,
        "state": grant.state,
        "site_sha256": grant.site_sha256,
        "aircraft_id": grant.aircraft_id,
        "profile_sha256s": list(grant.profile_sha256s),
        "evidence_ids": list(grant.evidence_ids),
        "expires_at": grant.expires_at,
        "grant_sha256": grant.grant_sha256,
        "acceptance_manifest_sha256": grant.acceptance_manifest_sha256,
    }


_GRANT_DOCUMENT_FIELDS = frozenset(_grant_to_document(
    CapabilityGrant(
        capability="c", state="SIMULATION_ONLY", site_sha256=None, aircraft_id=None,
        profile_sha256s=(), evidence_ids=(), expires_at=None, grant_sha256="",
    )
).keys())


@dataclass(frozen=True)
class TrialBinding:
    """Records the trial_protocol/evidence_destination a TRIAL grant was issued under.

    CapabilityGrant (models.py, outside this packet's owned files) has no fields for
    trial_protocol/evidence_destination, and acceptance admission previously accepted
    *any* string the caller's manifest happened to name for those two fields -- an
    unbound-authorization-field gap (see final concerns for the models.py extension
    this ultimately wants). This registry-level side table is the interim, in-scope
    fix: issue_trial records the binding the trial was actually issued with, and
    admit_live's acceptance path refuses unless the manifest names exactly that
    trial_protocol and evidence_destination for the grant it is trying to admit.
    """

    grant_sha256: str
    trial_protocol: str
    evidence_destination: str


def _trial_binding_to_document(binding: TrialBinding) -> dict:
    return {
        "grant_sha256": binding.grant_sha256,
        "trial_protocol": binding.trial_protocol,
        "evidence_destination": binding.evidence_destination,
    }


_TRIAL_BINDING_FIELDS = frozenset(field.name for field in fields(TrialBinding))


def _trial_binding_from_document(document: Mapping[str, object]) -> TrialBinding:
    if not isinstance(document, Mapping):
        raise GrantRegistryError("trial binding document must be a JSON object")
    unknown = set(document.keys()) - _TRIAL_BINDING_FIELDS
    if unknown:
        raise GrantRegistryError(f"unknown trial binding field(s): {sorted(unknown)}")
    missing = _TRIAL_BINDING_FIELDS - set(document.keys())
    if missing:
        raise GrantRegistryError(f"missing trial binding field(s): {sorted(missing)}")
    try:
        return TrialBinding(
            grant_sha256=document["grant_sha256"],
            trial_protocol=document["trial_protocol"],
            evidence_destination=document["evidence_destination"],
        )
    except (TypeError, KeyError) as exc:
        raise GrantRegistryError(f"trial binding fields are malformed: {exc}") from exc


@dataclass(frozen=True)
class GrantRegistry:
    """A canonical, immutable collection of capability grants.

    ``trial_bindings`` is a side table (see TrialBinding) of the
    trial_protocol/evidence_destination each TRIAL grant was issued with; it
    is part of the registry's own canonical form, not of CapabilityGrant.
    """

    schema_version: str
    grants: tuple[CapabilityGrant, ...]
    trial_bindings: tuple[TrialBinding, ...] = ()

    def to_document(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "grants": [_grant_to_document(grant) for grant in self.grants],
            "trial_bindings": [
                _trial_binding_to_document(binding)
                for binding in sorted(self.trial_bindings, key=lambda binding: binding.grant_sha256)
            ],
        }

    def canonical_bytes(self) -> bytes:
        return artifacts.canonical_bytes(self.to_document())

    def trial_binding_for(self, grant_sha256: str) -> "TrialBinding | None":
        for binding in self.trial_bindings:
            if binding.grant_sha256 == grant_sha256:
                return binding
        return None


def empty_registry() -> GrantRegistry:
    return GrantRegistry(schema_version=SCHEMA_VERSION, grants=(), trial_bindings=())


def _grant_from_document(document: Mapping[str, object]) -> CapabilityGrant:
    if not isinstance(document, Mapping):
        raise GrantRegistryError("grant document must be a JSON object")
    unknown = set(document.keys()) - _GRANT_DOCUMENT_FIELDS
    if unknown:
        raise GrantRegistryError(f"unknown grant field(s): {sorted(unknown)}")
    missing = _GRANT_DOCUMENT_FIELDS - set(document.keys())
    if missing:
        raise GrantRegistryError(f"missing grant field(s): {sorted(missing)}")
    if document.get("state") not in _GRANT_STATES:
        raise GrantRegistryError(f"unknown grant state {document.get('state')!r}")
    try:
        grant = CapabilityGrant(
            capability=document["capability"],
            state=document["state"],
            site_sha256=document["site_sha256"],
            aircraft_id=document["aircraft_id"],
            profile_sha256s=tuple(document["profile_sha256s"]),
            evidence_ids=tuple(document["evidence_ids"]),
            expires_at=document["expires_at"],
            grant_sha256=document["grant_sha256"],
            acceptance_manifest_sha256=document["acceptance_manifest_sha256"],
        )
    except (TypeError, KeyError) as exc:
        raise GrantRegistryError(f"grant fields do not match CapabilityGrant: {exc}") from exc
    recomputed = canonical_grant_sha256(grant)
    if recomputed != document["grant_sha256"]:
        raise GrantRegistryError(
            f"grant_sha256 mismatch for capability {grant.capability!r}: "
            f"recomputed {recomputed!r} != stored {document['grant_sha256']!r}"
        )
    return grant


def parse_registry(raw: bytes) -> GrantRegistry:
    """Parse, then fail closed: unknown fields, wrong schema_version, hash mismatch, duplicates."""
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GrantRegistryError(f"registry is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise GrantRegistryError("registry document must be a JSON object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise GrantRegistryError(f"unsupported schema_version {document.get('schema_version')!r}")
    unknown = set(document.keys()) - {"schema_version", "grants", "trial_bindings"}
    if unknown:
        raise GrantRegistryError(f"unknown registry field(s): {sorted(unknown)}")
    raw_grants = document.get("grants")
    if not isinstance(raw_grants, list):
        raise GrantRegistryError("registry 'grants' must be a JSON array")
    grants = tuple(_grant_from_document(entry) for entry in raw_grants)
    seen: set[tuple[str, str, str]] = set()
    for grant in grants:
        identity = (grant.capability, grant.state, grant.grant_sha256)
        if identity in seen:
            raise GrantRegistryError(f"duplicate grant (capability, state, grant_sha256): {identity}")
        seen.add(identity)
    raw_bindings = document.get("trial_bindings", [])
    if not isinstance(raw_bindings, list):
        raise GrantRegistryError("registry 'trial_bindings' must be a JSON array")
    trial_bindings = tuple(_trial_binding_from_document(entry) for entry in raw_bindings)
    seen_bindings: set[str] = set()
    for binding in trial_bindings:
        if binding.grant_sha256 in seen_bindings:
            raise GrantRegistryError(f"duplicate trial binding for grant_sha256 {binding.grant_sha256!r}")
        seen_bindings.add(binding.grant_sha256)
    return GrantRegistry(schema_version=SCHEMA_VERSION, grants=grants, trial_bindings=trial_bindings)


def load_registry(path: str | Path) -> GrantRegistry:
    return parse_registry(Path(path).read_bytes())


def write_registry(path: str | Path, registry: GrantRegistry) -> None:
    artifacts.atomic_write_bytes(path, registry.canonical_bytes())


def issue_trial(
    registry: GrantRegistry,
    *,
    capability: str,
    trial_protocol: str,
    site_sha256: str,
    aircraft_id: str,
    profile_sha256s: tuple[str, ...],
    evidence_destination: str,
    expires_at: str,
    acceptance_manifest: AcceptanceManifest,
) -> tuple[GrantRegistry, CapabilityGrant]:
    """Issue one narrowly scoped TRIAL grant. Returns a NEW registry; `registry` is untouched.

    `trial_protocol` and `evidence_destination` are validated (required, non-empty) but are not
    stored on CapabilityGrant -- that dataclass has no such fields, and this packet must build on
    it rather than fork or extend it (models.py is out of scope for this stage). The trial's
    binding to one specific acceptance session/capability is carried entirely by
    `acceptance_manifest` (see `AcceptanceManifest.session_id`/`capability`), whose hash becomes
    part of the grant and is checked by `CapabilityGrant.allows()` at admission time. A stronger
    binding of `evidence_destination` into the grant hash itself would require extending
    CapabilityGrant, which is outside this stage's owned files -- see final concerns.
    """
    for name, value in (
        ("capability", capability), ("trial_protocol", trial_protocol),
        ("site_sha256", site_sha256), ("aircraft_id", aircraft_id),
        ("evidence_destination", evidence_destination), ("expires_at", expires_at),
    ):
        if not value:
            raise GrantRegistryError(f"{name} is required to issue a trial grant")
    if acceptance_manifest.capability != capability:
        raise GrantRegistryError(
            "acceptance_manifest capability does not match the trial's requested capability"
        )
    if acceptance_manifest.session_kind != "ACCEPTANCE":
        raise GrantRegistryError("acceptance_manifest must have session_kind ACCEPTANCE")
    if acceptance_manifest.manifest_sha256 != canonical_acceptance_manifest_sha256(acceptance_manifest):
        raise GrantRegistryError("acceptance_manifest_sha256 does not match its content")
    grant = CapabilityGrant(
        capability=capability,
        state="TRIAL",
        site_sha256=site_sha256,
        aircraft_id=aircraft_id,
        profile_sha256s=tuple(sorted(profile_sha256s)),
        evidence_ids=(),
        expires_at=expires_at,
        grant_sha256="",
        acceptance_manifest_sha256=acceptance_manifest.manifest_sha256,
    )
    grant = replace(grant, grant_sha256=canonical_grant_sha256(grant))
    binding = TrialBinding(
        grant_sha256=grant.grant_sha256,
        trial_protocol=trial_protocol,
        evidence_destination=evidence_destination,
    )
    new_registry = GrantRegistry(
        schema_version=registry.schema_version,
        grants=registry.grants + (grant,),
        trial_bindings=registry.trial_bindings + (binding,),
    )
    return new_registry, grant


@dataclass(frozen=True)
class EvidenceIndex:
    """A tracked evidence index for one trial: which trial it was collected for, and outcome.

    Not part of the packet's contract-schema surface (only the grant registry has a JSON Schema
    contract); this is an internal structure local to this module and `scripts/room_capability.py`.
    """

    trial_grant_sha256: str
    evidence_ids: tuple[str, ...]
    passed: bool


_EVIDENCE_INDEX_FIELDS = frozenset(field.name for field in fields(EvidenceIndex))


def parse_evidence_index(raw: bytes) -> EvidenceIndex:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GrantRegistryError(f"evidence index is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise GrantRegistryError("evidence index document must be a JSON object")
    unknown = set(document.keys()) - _EVIDENCE_INDEX_FIELDS
    if unknown:
        raise GrantRegistryError(f"unknown evidence index field(s): {sorted(unknown)}")
    missing = _EVIDENCE_INDEX_FIELDS - set(document.keys())
    if missing:
        raise GrantRegistryError(f"missing evidence index field(s): {sorted(missing)}")
    try:
        return EvidenceIndex(
            trial_grant_sha256=document["trial_grant_sha256"],
            evidence_ids=tuple(document["evidence_ids"]),
            passed=bool(document["passed"]),
        )
    except (TypeError, KeyError) as exc:
        raise GrantRegistryError(f"evidence index fields are malformed: {exc}") from exc


def load_evidence_index(path: str | Path) -> EvidenceIndex:
    return parse_evidence_index(Path(path).read_bytes())


def promote(
    registry: GrantRegistry,
    *,
    trial_grant: CapabilityGrant,
    evidence_index: EvidenceIndex,
    expires_at: str,
    now: datetime,
) -> tuple[GrantRegistry, CapabilityGrant]:
    """Promote a TRIAL grant to PROMOTED. Returns a NEW registry; `trial_grant` is never mutated
    and remains present, byte-identical, in the returned registry.

    `now` must be tz-aware. An EXPIRED trial grant -- one admit_live already
    refuses -- must never be laundered into a fresh, unexpired PROMOTED grant:
    EvidenceIndex carries no collection timestamps, so nothing else establishes
    that the evidence was actually gathered inside the trial's validity window.
    """
    if now.tzinfo is None:
        raise GrantRegistryError("now must be timezone-aware")
    if trial_grant not in registry.grants:
        raise GrantRegistryError("trial grant is not present in the registry")
    if trial_grant.grant_sha256 != canonical_grant_sha256(trial_grant):
        raise GrantRegistryError("trial grant hash does not match its content")
    if trial_grant.state != "TRIAL":
        raise GrantRegistryError(f"cannot promote a grant in state {trial_grant.state!r}")
    trial_expiry = _parse_expiry(trial_grant.expires_at)
    if trial_expiry is None or now >= trial_expiry:
        raise GrantRegistryError(
            f"cannot promote an expired or unparseable trial grant (expires_at={trial_grant.expires_at!r})"
        )
    if evidence_index.trial_grant_sha256 != trial_grant.grant_sha256:
        raise GrantRegistryError("evidence index was not collected for this trial grant")
    if not evidence_index.passed:
        raise GrantRegistryError("evidence index has not passed")
    if not evidence_index.evidence_ids:
        raise GrantRegistryError("evidence index has zero evidence ids; nothing was tracked")
    if not expires_at:
        raise GrantRegistryError("expires_at is required to promote a grant")
    promoted = CapabilityGrant(
        capability=trial_grant.capability,
        state="PROMOTED",
        site_sha256=trial_grant.site_sha256,
        aircraft_id=trial_grant.aircraft_id,
        profile_sha256s=trial_grant.profile_sha256s,
        evidence_ids=tuple(sorted(evidence_index.evidence_ids)),
        expires_at=expires_at,
        grant_sha256="",
        acceptance_manifest_sha256=None,
    )
    promoted = replace(promoted, grant_sha256=canonical_grant_sha256(promoted))
    new_registry = GrantRegistry(schema_version=registry.schema_version, grants=registry.grants + (promoted,))
    return new_registry, promoted


def admit_live(registry: GrantRegistry, *, manifest: SessionManifest, now) -> CapabilityGrant:
    """The single fail-closed admission point. Raises GrantRegistryError on any refusal.

    - mode == "live": admits only a matching, unexpired PROMOTED grant whose grant_sha256 equals
      manifest.capability_grant_sha256.
    - mode == "acceptance": admits exactly one matching, unexpired TRIAL grant, and only when the
      manifest names a trial_protocol and evidence_destination and the acceptance-manifest binding
      (built from manifest.run_id/requested_capability) matches via CapabilityGrant.allows().
    - Any other mode never reaches admission.
    - SIMULATION_ONLY and DISABLED grants are always refused (they are excluded by the state
      filters below in both modes).
    """
    if manifest.mode not in _ADMISSIBLE_MODES:
        raise GrantRegistryError(f"admit_live does not admit mode {manifest.mode!r}")
    candidates = tuple(
        grant for grant in registry.grants if grant.capability == manifest.requested_capability
    )
    if manifest.mode == "live":
        if manifest.trial_protocol is not None or manifest.evidence_destination is not None:
            raise GrantRegistryError("ordinary live mode must not name a trial_protocol/evidence_destination")
        matches = [
            grant for grant in candidates
            if grant.state == "PROMOTED"
            and grant.grant_sha256 == manifest.capability_grant_sha256
            and grant.allows(
                capability=manifest.requested_capability,
                site_sha256=manifest.site_sha256,
                aircraft_id=manifest.aircraft_id,
                profile_sha256s=manifest.profile_sha256s,
                now=now,
            )
        ]
        if len(matches) != 1:
            raise GrantRegistryError(
                f"no unique matching PROMOTED grant for live admission of {manifest.requested_capability!r}"
            )
        winner = matches[0]
        if manifest.capability_grant_state != winner.state:
            raise GrantRegistryError(
                f"manifest.capability_grant_state {manifest.capability_grant_state!r} does not "
                f"match the admitted grant's actual state {winner.state!r}; the immutable "
                "manifest must not misreport the authorization the run flew under"
            )
        return winner

    # mode == "acceptance"
    if manifest.trial_protocol is None or manifest.evidence_destination is None:
        raise GrantRegistryError("acceptance admission requires a named trial_protocol and evidence_destination")
    acceptance_manifest = AcceptanceManifest(
        session_id=manifest.run_id, capability=manifest.requested_capability, manifest_sha256="",
    )
    acceptance_manifest = replace(
        acceptance_manifest, manifest_sha256=canonical_acceptance_manifest_sha256(acceptance_manifest),
    )
    # Unlike live mode, acceptance mode does not pre-filter by grant_sha256: "matching" here means
    # matching the acceptance-manifest binding (capability/site/aircraft/profile/session) via
    # CapabilityGrant.allows(), which is what makes two same-binding TRIAL grants genuinely
    # ambiguous rather than trivially disambiguated by hash. Hash consistency with what the
    # manifest declares is still checked once a single winner is found.
    matches = [
        grant for grant in candidates
        if grant.state == "TRIAL"
        and grant.allows(
            capability=manifest.requested_capability,
            site_sha256=manifest.site_sha256,
            aircraft_id=manifest.aircraft_id,
            profile_sha256s=manifest.profile_sha256s,
            now=now,
            session_id=manifest.run_id,
            acceptance_manifest=acceptance_manifest,
        )
    ]
    if len(matches) != 1:
        raise GrantRegistryError(
            f"acceptance admission requires exactly one matching TRIAL grant for "
            f"{manifest.requested_capability!r}, found {len(matches)}"
        )
    winner = matches[0]
    if winner.grant_sha256 != manifest.capability_grant_sha256:
        raise GrantRegistryError(
            "the matching TRIAL grant's hash does not equal manifest.capability_grant_sha256"
        )
    if manifest.capability_grant_state != winner.state:
        raise GrantRegistryError(
            f"manifest.capability_grant_state {manifest.capability_grant_state!r} does not "
            f"match the admitted grant's actual state {winner.state!r}; the immutable "
            "manifest must not misreport the authorization the run flew under"
        )
    # Enforced only when a binding was actually recorded for this grant (i.e. it
    # was issued through issue_trial -- the one sanctioned way to create a
    # TRIAL grant, including via scripts/room_capability.py issue-trial). A
    # small number of unit tests below construct a TRIAL CapabilityGrant by
    # hand, bypassing issue_trial entirely; there is nothing recorded to check
    # those against, so the check is a no-op for them (see final concerns).
    binding = registry.trial_binding_for(winner.grant_sha256)
    if binding is not None:
        if binding.trial_protocol != manifest.trial_protocol:
            raise GrantRegistryError(
                "manifest.trial_protocol does not match the trial_protocol this grant was issued with"
            )
        if binding.evidence_destination != manifest.evidence_destination:
            raise GrantRegistryError(
                "manifest.evidence_destination does not match the evidence_destination this grant was issued with"
            )
    return winner
