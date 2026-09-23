"""Offline replay of a frozen room-agent event fixture.

A fixture is two files under one directory: ``manifest.json`` (the binding --
session identity, request text, a synthetic/recorded label, the room-store
schema revision and this module's own projector revision, and a file-hash map)
and an event/perception-ingestion stream (conventionally ``events.jsonl``).
``replay_fixture`` verifies every manifest-bound file hash, verifies the
stream's structural and content integrity (no missing, duplicate, reordered,
corrupted or stale-authorizing record), then replays it into a fresh, private
``RoomStore`` through exactly the same projectors live mode uses --
``ingest_result`` and ``ReadQueries.project`` -- and renders the same terminal
report ``render_answer`` would. It never constructs a provider or drone
client; the ``provider_factory``/``client_factory`` parameters exist only to
make that contract explicit and are never called.

This is offline replay evidence only: byte-stable reproduction of a frozen,
already-recorded trace, never a fresh model or hardware measurement (see
``docs/reports/room-agent-count-readiness.md``).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import tempfile

from .ingest import ingest_result, result_from_dict
from .models import CapabilityProfile, TerminalState
from .queries import ReadQueries, render_answer
from .store import RoomStore, _canonical_json, _canonical_payload, _event_sha256

#: Bumped whenever a change to the store/query projectors this module relies
#: on (``RoomStore``, ``ingest_result``, ``ReadQueries.project``,
#: ``render_answer``) could change a frozen fixture's replayed output. A
#: fixture's manifest binds the revision it was captured against; replay
#: refuses to run a fixture bound to a different one rather than silently
#: reproducing a value the current code no longer actually computes.
PROJECTOR_REVISION = "room-projectors-v1"

_REQUIRED_MANIFEST_KEYS = (
    "session_id", "request_text", "label", "room_schema_revision", "projector_revision", "files",
)
_ROOM_EVENT_KEYS = frozenset({
    "record_no", "record_type", "sequence_no", "event_id", "session_id", "state_version",
    "event_type", "correlation_id", "causation_id", "idempotency_key", "event_sha256",
    "payload", "wall_time", "monotonic_ms",
})
_PERCEPTION_KEYS = frozenset({"record_no", "record_type", "current_snapshot_version", "result"})
_LABELS = ("synthetic", "recorded")


class ReplayIntegrityError(RuntimeError):
    """A replay fixture's manifest binding or recorded stream failed verification."""


@dataclass(frozen=True)
class ReplayResult:
    final_snapshot_sha256: str
    decision_stream_sha256: str
    report_sha256: str
    terminal_state: TerminalState


def forbidden(*args, **kwargs):
    """Default for ``provider_factory``/``client_factory``: replay must never call either."""
    raise AssertionError("replay_fixture must never construct a provider or drone client")


def replay_fixture(manifest_path: Path, *, provider_factory=forbidden, client_factory=forbidden) -> ReplayResult:
    manifest_path = Path(manifest_path)
    manifest = _load_manifest(manifest_path)
    base = manifest_path.parent
    _verify_file_hashes(manifest, base)
    if manifest["label"] not in _LABELS:
        raise ValueError(f"unsupported fixture label: {manifest['label']!r}")
    if manifest["projector_revision"] != PROJECTOR_REVISION:
        raise ReplayIntegrityError(
            f"projector revision {manifest['projector_revision']!r} does not match this "
            f"build's binding {PROJECTOR_REVISION!r}")
    events_name = next((name for name in manifest["files"] if name.endswith(".jsonl")), None)
    if events_name is None:
        raise ValueError("manifest does not bind an event stream file")
    records = _load_records(base / events_name)
    _validate_records(records)

    session_id = manifest["session_id"]
    with tempfile.TemporaryDirectory(prefix="room-replay-") as tmp:
        store = RoomStore.open(Path(tmp) / "replay.sqlite3")
        try:
            _verify_schema_revision(store, manifest["room_schema_revision"])
            store.create_session(session_id, manifest["request_text"], CapabilityProfile(()))
            for record in records:
                if record["record_type"] == "ROOM_EVENT":
                    _insert_room_event(store, record)
                else:
                    _ingest_perception(store, session_id, record, base)
            snapshot = ReadQueries(store).project(session_id)
            decision_stream_sha256 = _sha_json(_decision_stream(records))
            final_snapshot_sha256 = _sha_json(asdict(snapshot))
            report_sha256 = _sha_bytes(_render_report(snapshot, records).encode("utf-8"))
            return ReplayResult(final_snapshot_sha256, decision_stream_sha256, report_sha256, snapshot.terminal_state)
        finally:
            store.close()


def _load_manifest(manifest_path: Path) -> dict:
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReplayIntegrityError(f"manifest is unavailable: {manifest_path}") from exc
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReplayIntegrityError(f"manifest is not valid JSON: {manifest_path}") from exc
    if not isinstance(manifest, dict) or any(key not in manifest for key in _REQUIRED_MANIFEST_KEYS):
        raise ValueError(f"manifest is missing required keys: {manifest_path}")
    if not isinstance(manifest["files"], dict) or not manifest["files"]:
        raise ValueError("manifest must bind at least one file hash")
    return manifest


def _verify_file_hashes(manifest: dict, base: Path) -> None:
    # Every manifest-bound file's hash is checked before the event stream --
    # or anything else in the fixture directory -- is ever opened.
    for name, expected_hash in manifest["files"].items():
        path = base / name
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ReplayIntegrityError(f"missing manifest-bound file: {name}") from exc
        if _sha_bytes(data) != expected_hash:
            raise ReplayIntegrityError(f"file hash mismatch: {name}")


def _verify_schema_revision(store: RoomStore, expected: int) -> None:
    with store._lock:
        row = store.connection.execute("SELECT COALESCE(MAX(version), 0) FROM room_schema_version").fetchone()
    actual = int(row[0])
    if actual != expected:
        raise ReplayIntegrityError(
            f"room schema revision {expected} does not match this build's migrated schema {actual}")


def _load_records(events_path: Path) -> list[dict]:
    try:
        text = events_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReplayIntegrityError(f"event stream is unavailable: {events_path}") from exc
    records = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayIntegrityError(f"corrupted record: invalid JSON on line {line_no}") from exc
        if not isinstance(record, dict) or record.get("record_type") not in ("ROOM_EVENT", "PERCEPTION_INGESTED"):
            raise ReplayIntegrityError(f"corrupted record: unrecognized record on line {line_no}")
        records.append(record)
    if not records:
        raise ReplayIntegrityError("event stream contains no records")
    return records


def _check_contiguous(numbers: list[int], *, what: str) -> None:
    if len(set(numbers)) != len(numbers):
        raise ReplayIntegrityError(f"duplicate {what} detected")
    if numbers != sorted(numbers):
        raise ReplayIntegrityError(f"reordered {what} detected")
    if sorted(numbers) != list(range(1, len(numbers) + 1)):
        raise ReplayIntegrityError(f"missing {what} detected (non-contiguous sequence)")


def _validate_records(records: list[dict]) -> None:
    for record in records:
        required = _ROOM_EVENT_KEYS if record["record_type"] == "ROOM_EVENT" else _PERCEPTION_KEYS
        if not required <= record.keys():
            raise ReplayIntegrityError("corrupted record: missing required fields")
    _check_contiguous([r["record_no"] for r in records], what="record")

    room_events = [r for r in records if r["record_type"] == "ROOM_EVENT"]
    _check_contiguous([r["sequence_no"] for r in room_events], what="event sequence")
    _check_contiguous([r["state_version"] for r in room_events], what="event state version")
    keys = [r["idempotency_key"] for r in room_events]
    if len(set(keys)) != len(keys):
        raise ReplayIntegrityError("duplicate idempotency key detected")

    for record in room_events:
        payload_json = _canonical_payload(record["payload"])
        digest = _event_sha256(
            session_id=record["session_id"], event_type=record["event_type"], payload_json=payload_json,
            correlation_id=record["correlation_id"], causation_id=record["causation_id"],
        )
        if digest != record["event_sha256"]:
            raise ReplayIntegrityError(
                f"corrupted record: event_sha256 mismatch for sequence_no={record['sequence_no']}")
        # ``append_version`` (added by ``RoomStore.append_events_current``) is
        # the live session version immediately before that event was
        # appended. A record claiming one that does not match its own
        # position is authorizing itself against a state it never actually
        # followed -- a stale-authorizing record, not merely reordered.
        append_version = record["payload"].get("append_version")
        if append_version is not None and append_version != record["state_version"] - 1:
            raise ReplayIntegrityError(
                f"stale-authorizing record: append_version mismatch for sequence_no={record['sequence_no']}")


def _insert_room_event(store: RoomStore, record: dict) -> None:
    payload_json = _canonical_payload(record["payload"])
    with store._lock:
        store._require_open()
        connection = store.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT state_version FROM room_sessions WHERE session_id = ?", (record["session_id"],),
            ).fetchone()
            if current is None or int(current["state_version"]) != record["state_version"] - 1:
                raise ReplayIntegrityError(
                    "stale-authorizing record: event at sequence_no="
                    f"{record['sequence_no']} does not follow the replayed session version")
            connection.execute(
                "INSERT INTO room_events (sequence_no, event_id, session_id, state_version, event_type, "
                "correlation_id, causation_id, idempotency_key, event_sha256, payload_json, wall_time, "
                "monotonic_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record["sequence_no"], record["event_id"], record["session_id"], record["state_version"],
                 record["event_type"], record["correlation_id"], record["causation_id"],
                 record["idempotency_key"], record["event_sha256"], payload_json,
                 record["wall_time"], record["monotonic_ms"]),
            )
            updated = connection.execute(
                "UPDATE room_sessions SET state_version = ? WHERE session_id = ? AND state_version = ?",
                (record["state_version"], record["session_id"], record["state_version"] - 1),
            )
            if updated.rowcount != 1:
                raise ReplayIntegrityError("replay could not advance the session version consistently")
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _ingest_perception(store: RoomStore, session_id: str, record: dict, base: Path) -> None:
    declared = record["current_snapshot_version"]
    actual = store.session_version(session_id)
    if declared != actual:
        raise ReplayIntegrityError(
            "stale-authorizing record: perception ingestion declares snapshot version "
            f"{declared}, but the replayed session is at {actual}")
    # The fixture stores its evidence frame as a plain, manifest-bound file
    # name beside the manifest (already hash-verified in ``_verify_file_hashes``);
    # resolve it to this replay's actual location before validating the result.
    try:
        document = json.loads(json.dumps(record["result"]))
        frame_name = document["request"]["frame_path"]
        if not isinstance(frame_name, str) or "/" in frame_name or "\\" in frame_name or not frame_name:
            raise ReplayIntegrityError("corrupted record: perception frame reference must be a bare file name")
        document["request"]["frame_path"] = str(base / frame_name)
        result = result_from_dict(document)
    except ReplayIntegrityError:
        raise
    except Exception as exc:
        raise ReplayIntegrityError("corrupted record: invalid perception result") from exc
    if result.request.session_id != session_id:
        raise ReplayIntegrityError("corrupted record: perception result targets another session")
    ingest_result(store, result, current_snapshot_version=declared)


def _decision_stream(records: list[dict]) -> list[dict]:
    return [
        {"event_type": record["event_type"], "payload": record["payload"]}
        for record in records
        if record["record_type"] == "ROOM_EVENT" and record["event_type"] in ("ROOM_CHOICE_ATTEMPT", "ROOM_TOOL_OUTCOME")
    ]


def _render_report(snapshot, records: list[dict]) -> str:
    if snapshot.facts:
        return render_answer(snapshot.facts)
    for record in reversed(records):
        if record["record_type"] == "ROOM_EVENT" and record["event_type"] == "ROOM_TOOL_OUTCOME":
            answer = record["payload"].get("answer")
            if isinstance(answer, str) and answer:
                return answer
            break
    return f"NO_ANSWER_TEXT; terminal_state={snapshot.terminal_state}"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_json(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
