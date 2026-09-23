"""Offline session manifests and append-only run artifacts.

Every write in this module is either a pure in-memory computation (canonical
JSON, digests) or an explicit, atomic, fsynced filesystem operation. Nothing
here constructs a provider, drone client or network object; callers own that.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from jsonschema import Draft202012Validator, ValidationError as _JsonSchemaValidationError

from .models import CapabilityProfile
from .store import RoomStore
# store.py owns the canonical JSON form and the credential/raw-bytes payload
# filter used by room_events. Importing the private helpers (rather than
# duplicating the blocklist here) keeps one definition of "forbidden payload"
# in the codebase instead of two that could silently drift apart.
from .store import _canonical_json as _store_canonical_json
from .store import _reject_forbidden_payload as _store_reject_forbidden_payload

SCHEMA_VERSION = "room-session-manifest-v1"
DIGEST_FIELD = "authorization_digest"

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs" / "superpowers" / "specs" / "contracts" / "room-session-manifest-v1.schema.json"
)
_schema_validator_cache: Draft202012Validator | None = None


def _schema_validator() -> Draft202012Validator:
    """Lazily load and cache the manifest's own JSON Schema validator.

    parse_manifest previously validated only key presence/schema_version/digest
    and never applied this schema at runtime, so a digest-consistent document
    with out-of-range values (e.g. max_duration_s=-1.0) passed the "fail
    closed" live-validation path. This closes that gap.
    """
    global _schema_validator_cache
    if _schema_validator_cache is None:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        _schema_validator_cache = Draft202012Validator(schema)
    return _schema_validator_cache

_VALID_MODES = frozenset({"preview", "replay", "simulate", "live", "acceptance"})
_UNVERIFIED = "UNVERIFIED"


class ManifestError(ValueError):
    """A manifest document is malformed, tampered with, or internally inconsistent."""


class ArtifactError(RuntimeError):
    """A run-artifact operation violated an append-only or durability invariant."""


def canonical_json(value: object) -> str:
    """Render *value* as canonical JSON: sorted keys, compact separators, finite numbers only.

    Delegates to store._canonical_json (same sort_keys/separators/ensure_ascii/allow_nan=False
    form) so this packet has exactly one canonical-JSON definition, not a second copy that could
    silently drift from the store's.
    """
    return _store_canonical_json(value)


def canonical_bytes(value: object) -> bytes:
    return canonical_json(value).encode("utf-8")


def authorization_digest(document: Mapping[str, object]) -> str:
    """sha256 hex digest of *document* with the derived digest field omitted."""
    body = {key: value for key, value in document.items() if key != DIGEST_FIELD}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


# store._reject_forbidden_payload's blocklist (api_key, authorization, password,
# credential, access_token, token, ...) misses several common credential key
# names (secret, private_key, passphrase, session_key, signing_key, bearer,
# cookie, ssh_key). Widening that blocklist requires editing store.py, which is
# outside this packet's owned files -- reported as a missing interface (see
# final concerns). As a defense-in-depth measure fully inside this module,
# this packet also refuses this second, wider set of stems, over the exact
# same manifest/event-payload/terminal/summary documents store's filter
# already covers -- so a name on either list is rejected.
_EXTRA_FORBIDDEN_KEY_STEMS = frozenset({
    "secret",
    "privatekey",
    "passphrase",
    "sessionkey",
    "signingkey",
    "bearer",
    "cookie",
    "sshkey",
})


def _normalize_forbidden_key(key: object) -> str:
    return "".join(character for character in str(key).casefold() if character.isalnum())


def _reject_extra_forbidden(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalize_forbidden_key(key)
            if any(stem in normalized for stem in _EXTRA_FORBIDDEN_KEY_STEMS):
                raise ArtifactError(f"payload key {key!r} looks like a credential and is forbidden")
            _reject_extra_forbidden(child)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            _reject_extra_forbidden(child)


def reject_forbidden(value: object) -> None:
    """Reject credentials/headers/raw bytes anywhere in *value*.

    Thin wrapper over RoomStore's payload filter (see the import comment
    above): this is the one place this packet decides what "forbidden"
    means, and it always means at least what store.py means -- plus the
    wider stem list above, since store.py's list is not this packet's to
    widen. See ``_EXTRA_FORBIDDEN_KEY_STEMS``.
    """
    try:
        _store_reject_forbidden_payload(value)
    except Exception as exc:  # store.ForbiddenPayload, kept as an implementation detail
        raise ArtifactError(str(exc)) from exc
    _reject_extra_forbidden(value)


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Write *data* to *path* atomically: tmp file, flush, fsync, replace, (best-effort) dir fsync."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)
    try:
        # Windows has no directory file descriptor to fsync; os.open with
        # O_RDONLY on a directory raises there. The file-level fsync plus
        # os.replace's atomic rename is the best durability guarantee this
        # platform offers, so a failure here is not treated as fatal.
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _append_jsonl_line(path: str | Path, record: Mapping[str, object]) -> None:
    """Append one canonical JSON line to *path*, fsynced before returning."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (canonical_json(record) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@dataclass(frozen=True)
class SessionManifest:
    """The immutable, authorization-bound description of one room-agent run."""

    schema_version: str
    run_id: str
    created_at: str
    mode: str
    code_revision: str
    code_dirty: bool
    integration_revision: str
    requested_capability: str
    capability_grant_sha256: str
    capability_grant_state: str
    request_revision: str
    goal_parser_revision: str
    site_sha256: str
    tag_map_sha256: str
    camera_sha256: str
    profile_sha256s: tuple[str, ...]
    aircraft_id: str
    firmware_version: str
    requested_jev_identity: str
    resolved_jev_identity: str
    requested_detector_identity: str
    resolved_detector_identity: str
    max_duration_s: float
    max_distance_cm: float
    max_recovery_attempts: int
    provider_deadlines: Mapping[str, float]
    cost_assumptions: Mapping[str, object]
    home_policy: Mapping[str, object]
    termination_policy: Mapping[str, object]
    consent_references: tuple[str, ...]
    trial_protocol: str | None = None
    evidence_destination: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ManifestError(f"unsupported schema_version {self.schema_version!r}")
        if self.mode not in _VALID_MODES:
            raise ManifestError(f"unknown mode {self.mode!r}")
        if self.resolved_jev_identity != _UNVERIFIED or self.resolved_detector_identity != _UNVERIFIED:
            raise ManifestError(
                "an authorized manifest must record resolved identities as UNVERIFIED; "
                "resolution is recorded later as append-only evidence, never in the manifest"
            )
        if tuple(sorted(self.profile_sha256s)) != tuple(self.profile_sha256s):
            raise ManifestError("profile_sha256s must be a sorted tuple")
        if self.mode == "acceptance":
            if self.trial_protocol is None or self.evidence_destination is None:
                raise ManifestError("acceptance mode requires trial_protocol and evidence_destination")
        else:
            if self.trial_protocol is not None or self.evidence_destination is not None:
                raise ManifestError("trial_protocol and evidence_destination are acceptance-only fields")

    def to_document(self) -> dict:
        body = {field.name: getattr(self, field.name) for field in fields(self)}
        try:
            digest = authorization_digest(body)
        except ValueError as exc:
            raise ManifestError(f"manifest is not canonicalizable: {exc}") from exc
        document = {**body, DIGEST_FIELD: digest}
        reject_forbidden(document)
        return document

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_document())


_MANIFEST_FIELD_NAMES = frozenset(field.name for field in fields(SessionManifest))
_MANIFEST_TUPLE_FIELDS = ("profile_sha256s", "consent_references")


def _reject_non_finite(value: object, *, _raw) -> None:
    raise ManifestError(f"manifest contains a non-finite number ({_raw!r})")


def parse_manifest(raw: bytes) -> SessionManifest:
    """Parse, then fail closed: unknown/missing fields, wrong schema_version, digest mismatch."""
    try:
        document = json.loads(
            raw,
            parse_constant=lambda token: _reject_non_finite(token, _raw=token),
        )
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ManifestError("manifest document must be a JSON object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"unsupported schema_version {document.get('schema_version')!r}")
    known_keys = _MANIFEST_FIELD_NAMES | {DIGEST_FIELD}
    unknown = set(document.keys()) - known_keys
    if unknown:
        raise ManifestError(f"unknown manifest field(s): {sorted(unknown)}")
    missing = _MANIFEST_FIELD_NAMES - set(document.keys())
    if missing:
        raise ManifestError(f"missing manifest field(s): {sorted(missing)}")
    supplied_digest = document.get(DIGEST_FIELD)
    if not isinstance(supplied_digest, str):
        raise ManifestError("manifest is missing its authorization_digest")
    body = {key: value for key, value in document.items() if key != DIGEST_FIELD}
    try:
        recomputed = authorization_digest(body)
    except ValueError as exc:
        raise ManifestError(f"manifest is not canonicalizable: {exc}") from exc
    if recomputed != supplied_digest:
        raise ManifestError("manifest authorization_digest does not match its content")
    try:
        _schema_validator().validate(document)
    except _JsonSchemaValidationError as exc:
        raise ManifestError(f"manifest fails its JSON Schema: {exc.message}") from exc
    kwargs = dict(body)
    for name in _MANIFEST_TUPLE_FIELDS:
        kwargs[name] = tuple(kwargs[name])
    try:
        return SessionManifest(**kwargs)
    except TypeError as exc:
        raise ManifestError(f"manifest fields do not match SessionManifest: {exc}") from exc


def write_manifest(path: str | Path, manifest: SessionManifest) -> None:
    """Atomically write exactly manifest.canonical_bytes() to *path*.

    The write path validates against the same schema the read path applies. Without this the
    two disagree: SessionManifest enforces only its own invariants, so preview could emit a
    manifest -- and print its digest -- that parse_manifest then refuses, and the operator
    would not find out until the live run loaded those exact bytes.
    """
    raw = manifest.canonical_bytes()
    parse_manifest(raw)
    atomic_write_bytes(path, raw)


@dataclass(frozen=True)
class RawEvent:
    event_id: str
    sequence_no: int
    event_type: str
    payload: dict
    recorded_at: str


def _serialize_summary(document: Mapping[str, object]) -> bytes:
    """Serialize the run summary document. A seam tests can monkeypatch to force finalize to fail
    after terminal validation but before any file is written, proving interrupted finalization
    leaves no partial terminal.json."""
    return canonical_bytes(document)


class RunArtifacts:
    """The on-disk, append-only record of one authorized run.

    Layout under ``root/manifest.run_id``::

        manifest.json   raw.jsonl   session.sqlite3   terminal.json   summary.json
        frames/
    """

    def __init__(
        self, *, root: Path, manifest: SessionManifest, store: RoomStore,
        next_sequence: int, resolved_identities: Mapping[str, str],
    ) -> None:
        self.root = root
        self.manifest = manifest
        self.store = store
        self._raw_path = root / "raw.jsonl"
        # The store is the durable, trigger-protected append-only ledger; the
        # next sequence number is derived from it (not from the mutable
        # raw.jsonl mirror file) so a lost/corrupted/truncated raw.jsonl can
        # never cause a sequence_no or event_id to be reused.
        self._next_sequence = next_sequence
        self._resolved_identities: dict[str, str] = dict(resolved_identities)
        self._closed = False

    @classmethod
    def open(cls, root: str | Path, manifest: SessionManifest, *, now: datetime) -> "RunArtifacts":
        run_dir = Path(root) / manifest.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "frames").mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.json"
        manifest_bytes = manifest.canonical_bytes()
        if manifest_path.exists():
            existing = parse_manifest(manifest_path.read_bytes())
            if existing.canonical_bytes() != manifest_bytes:
                raise ArtifactError(
                    f"run {manifest.run_id!r} was already opened with a different manifest"
                )
        else:
            atomic_write_bytes(manifest_path, manifest_bytes)
        store = RoomStore.open(run_dir / "session.sqlite3")
        try:
            store.session_version(manifest.run_id)
        except KeyError:
            store.create_session(
                manifest.run_id, f"room-agent session {manifest.run_id}", CapabilityProfile(()),
            )
        stored_events = store.events(manifest.run_id)
        next_sequence = len(stored_events) + 1
        # Rebuild resolved-identity state from the durable event log on every
        # open, not just within one in-memory session: otherwise reopening a
        # run and recording a *different* identity for an already-resolved
        # role would be silently accepted instead of failing closed.
        resolved_identities = _replay_resolved_identities(stored_events)
        instance = cls(
            root=run_dir, manifest=manifest, store=store,
            next_sequence=next_sequence, resolved_identities=resolved_identities,
        )
        instance._opened_at = _iso(now)  # bookkeeping only; never fed into a digest
        return instance

    def append_event(self, event_type: str, payload: Mapping[str, object], *, now: datetime) -> RawEvent:
        payload = dict(payload)
        reject_forbidden(payload)
        sequence_no = self._next_sequence
        event_id = f"{self.manifest.run_id}:{sequence_no:012d}"
        # Commit to the store FIRST. It is the durable, trigger-protected,
        # append-only ledger (immutability triggers, UNIQUE idempotency key,
        # PRIMARY KEY(session_id, sequence_no)); raw.jsonl is only ever a
        # mirror of what the store has already durably committed. If the
        # store call raises for any reason (idempotency conflict, a reserved
        # payload key, a version conflict, ...), no raw.jsonl line is written
        # and self._next_sequence is not advanced, so the next call reuses
        # neither this sequence_no nor this event_id.
        self.store.append_event_current(
            session_id=self.manifest.run_id,
            assessed_snapshot_version=None,
            event_type=event_type,
            payload=payload,
            correlation_id=event_id,
            causation_id=None,
            idempotency_key=event_id,
        )
        recorded_at = _iso(now)
        record = {
            "event_id": event_id,
            "sequence_no": sequence_no,
            "event_type": event_type,
            "payload": payload,
            "recorded_at": recorded_at,
        }
        _append_jsonl_line(self._raw_path, record)
        self._next_sequence = sequence_no + 1
        return RawEvent(
            event_id=event_id, sequence_no=sequence_no, event_type=event_type,
            payload=payload, recorded_at=recorded_at,
        )

    def record_resolved_identity(self, *, role: str, resolved: str, observed_at: datetime) -> RawEvent:
        if role not in {"jev", "detector"}:
            raise ArtifactError(f"unknown identity role {role!r}")
        previous = self._resolved_identities.get(role)
        if previous is not None and previous != resolved:
            raise ArtifactError(
                f"role {role!r} was already resolved to {previous!r}; cannot re-resolve to {resolved!r}"
            )
        event = self.append_event(
            "IDENTITY_RESOLVED",
            {"role": role, "resolved": resolved, "observed_at": _iso(observed_at)},
            now=observed_at,
        )
        self._resolved_identities[role] = resolved
        return event

    def finalize(self, terminal: Mapping[str, object], *, now: datetime) -> None:
        terminal_document = {
            **dict(terminal),
            "run_id": self.manifest.run_id,
            "resolved_identities": dict(self._resolved_identities),
            "finalized_at": _iso(now),
        }
        reject_forbidden(terminal_document)
        terminal_bytes = canonical_bytes(terminal_document)
        terminal_path = self.root / "terminal.json"
        if terminal_path.exists():
            existing_bytes = terminal_path.read_bytes()
            if existing_bytes == terminal_bytes:
                return
            raise ArtifactError(f"run {self.manifest.run_id!r} already has a different terminal record")
        summary_document = {
            "run_id": self.manifest.run_id,
            "mode": self.manifest.mode,
            "terminal_state": terminal_document.get("state"),
            "resolved_identities": dict(self._resolved_identities),
            "event_count": self._next_sequence - 1,
            "finalized_at": terminal_document["finalized_at"],
        }
        reject_forbidden(summary_document)
        # Summary is serialized (and written) before terminal.json. If this
        # step raises -- including the deliberate monkeypatch of
        # _serialize_summary tests use to prove interrupted finalization is
        # safe -- terminal.json's .tmp is never created, so no partial
        # terminal.json can exist.
        summary_bytes = _serialize_summary(summary_document)
        atomic_write_bytes(self.root / "summary.json", summary_bytes)
        atomic_write_bytes(terminal_path, terminal_bytes)

    def backup_sqlite(self, destination: str | Path) -> Path:
        destination = Path(destination)
        self.store.snapshot_to(destination)
        return destination

    def close(self) -> None:
        if not self._closed:
            self.store.close()
            self._closed = True


def _replay_resolved_identities(stored_events) -> dict:
    """Rebuild the resolved-identity map from the store's durable event log.

    Used by RunArtifacts.open on every open (not just the first), so a second,
    different observation for a role that was already resolved in a *prior*
    open() call is detected and fails closed, exactly as it would within a
    single open() call.
    """
    resolved: dict = {}
    for event in stored_events:
        if event.event_type != "IDENTITY_RESOLVED":
            continue
        role = event.payload.get("role")
        value = event.payload.get("resolved")
        previous = resolved.get(role)
        if previous is not None and previous != value:
            raise ArtifactError(
                f"stored history for role {role!r} contains conflicting resolved "
                f"identities ({previous!r} and {value!r}); the event log is "
                "corrupted or has been tampered with"
            )
        resolved[role] = value
    return resolved
