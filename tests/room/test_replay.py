"""Plan 03 Task 5: offline replay of a frozen room-agent event fixture.

``replay_fixture`` never constructs a provider or drone client -- it only
re-runs the already-recorded event/perception-ingestion stream through the
same store/query projectors live mode uses (``RoomStore``, ``ingest_result``,
``ReadQueries.project``, ``render_answer``). The golden fixture under
``fixtures/room_agent/v1`` is a synthetic count-and-return trace: one OBSERVE
turn ingests a single low-confidence ("uncertain") computer detection, a
READ_OBSERVATIONS turn reads it back, and a plain ANSWER reports it and
completes. Task 3 (closer inspection) is deferred; this fixture never
exercises a closer/validate-by-approach candidate (see the controller ruling
at the end of this plan's task-5 brief).
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from sentinel.room.models import TerminalState
from sentinel.room.replay import PROJECTOR_REVISION, ReplayIntegrityError, ReplayResult, replay_fixture

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "room_agent" / "v1"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _golden():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    events_name = next(name for name in manifest["files"] if name.endswith(".jsonl"))
    lines = (FIXTURE_DIR / events_name).read_text(encoding="utf-8").splitlines()
    return manifest, events_name, lines


def _write_mutated(tmp_path, *, manifest=None, events_name=None, lines):
    """Write a tampered copy of the golden fixture with a *consistent* file
    hash -- so the file-hash gate passes and the deeper structural/content
    integrity checks are what must catch the tamper."""
    base_manifest, base_name, _ = _golden()
    manifest = copy.deepcopy(manifest if manifest is not None else base_manifest)
    events_name = events_name or base_name
    body = "\n".join(lines) + ("\n" if lines else "")
    encoded = body.encode("utf-8")
    (tmp_path / events_name).write_bytes(encoded)  # write_text would translate \n -> \r\n on Windows
    manifest["files"] = {events_name: _sha256(encoded)}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def forbidden_factory(*args, **kwargs):
    pytest.fail("replay must never construct a provider or drone client")


# --- golden fixture reproduces the manifest-bound expectations -------------

def test_replay_reproduces_manifest_bound_hashes_terminal_state_and_report():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    result = replay_fixture(MANIFEST_PATH)
    assert isinstance(result, ReplayResult)
    expected = manifest["expected"]
    assert result.final_snapshot_sha256 == expected["final_snapshot_sha256"]
    assert result.decision_stream_sha256 == expected["decision_stream_sha256"]
    assert result.report_sha256 == expected["report_sha256"]
    assert result.terminal_state == TerminalState(expected["terminal_state"])
    assert manifest["label"] == "synthetic"
    assert manifest["projector_revision"] == PROJECTOR_REVISION


def test_replay_is_byte_stable_across_repeated_runs():
    first = replay_fixture(MANIFEST_PATH)
    second = replay_fixture(MANIFEST_PATH)
    assert first == second


def test_replay_never_constructs_provider_or_drone_factories():
    result = replay_fixture(MANIFEST_PATH, provider_factory=forbidden_factory, client_factory=forbidden_factory)
    assert result.terminal_state == TerminalState.COMPLETED


# --- file-hash gate runs before the event stream is ever opened ------------

def test_replay_rejects_a_file_hash_mismatch_before_parsing_events(tmp_path):
    _, events_name, lines = _golden()
    body = "not even json\n" + "\n".join(lines) + "\n"
    (tmp_path / events_name).write_bytes(body.encode("utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")  # stale (golden) file hash
    with pytest.raises(ReplayIntegrityError, match="hash"):
        replay_fixture(manifest_path)


def test_replay_rejects_room_schema_revision_mismatch(tmp_path):
    manifest, events_name, lines = _golden()
    manifest["room_schema_revision"] = manifest["room_schema_revision"] + 1
    manifest_path = _write_mutated(tmp_path, manifest=manifest, events_name=events_name, lines=lines)
    with pytest.raises(ReplayIntegrityError, match="schema revision"):
        replay_fixture(manifest_path)


def test_replay_rejects_projector_revision_mismatch(tmp_path):
    manifest, events_name, lines = _golden()
    manifest["projector_revision"] = "some-other-revision"
    manifest_path = _write_mutated(tmp_path, manifest=manifest, events_name=events_name, lines=lines)
    with pytest.raises(ReplayIntegrityError, match="projector revision"):
        replay_fixture(manifest_path)


# --- structural/content integrity: missing, duplicate, reordered, corrupted,
# --- and stale-authorizing records are all rejected -------------------------

def test_replay_rejects_a_missing_record(tmp_path):
    _, _, lines = _golden()
    # Drop the READ_OBSERVATIONS outcome line, opening a sequence gap.
    victim = next(i for i, line in enumerate(lines) if '"READ_OBSERVATIONS"' in line and "ROOM_TOOL_OUTCOME" in line)
    mutated = lines[:victim] + lines[victim + 1:]
    manifest_path = _write_mutated(tmp_path, lines=mutated)
    with pytest.raises(ReplayIntegrityError, match="missing"):
        replay_fixture(manifest_path)


def test_replay_rejects_a_duplicate_record(tmp_path):
    _, _, lines = _golden()
    mutated = lines[:1] + [lines[0]] + lines[1:]
    manifest_path = _write_mutated(tmp_path, lines=mutated)
    with pytest.raises(ReplayIntegrityError, match="duplicate"):
        replay_fixture(manifest_path)


def test_replay_rejects_a_reordered_record(tmp_path):
    _, _, lines = _golden()
    mutated = list(lines)
    mutated[0], mutated[1] = mutated[1], mutated[0]
    manifest_path = _write_mutated(tmp_path, lines=mutated)
    with pytest.raises(ReplayIntegrityError, match="reordered"):
        replay_fixture(manifest_path)


def test_replay_rejects_a_corrupted_record(tmp_path):
    _, _, lines = _golden()
    mutated = list(lines)
    victim = next(i for i, line in enumerate(lines) if '"DRONE_STATE_RECORDED"' in line)
    record = json.loads(mutated[victim])
    record["payload"]["state"]["battery_percent"] = 1
    mutated[victim] = json.dumps(record)  # event_sha256 left stale
    manifest_path = _write_mutated(tmp_path, lines=mutated)
    with pytest.raises(ReplayIntegrityError, match="corrupted"):
        replay_fixture(manifest_path)


def test_replay_rejects_a_stale_authorizing_perception_record(tmp_path):
    _, _, lines = _golden()
    mutated = list(lines)
    victim = next(i for i, line in enumerate(lines) if '"PERCEPTION_INGESTED"' in line)
    record = json.loads(mutated[victim])
    record["current_snapshot_version"] = record["current_snapshot_version"] + 5
    mutated[victim] = json.dumps(record)
    manifest_path = _write_mutated(tmp_path, lines=mutated)
    with pytest.raises(ReplayIntegrityError, match="stale-authorizing"):
        replay_fixture(manifest_path)


def test_replay_rejects_a_stale_authorizing_room_event(tmp_path):
    _, _, lines = _golden()
    mutated = list(lines)
    victim = next(i for i, line in enumerate(lines)
                  if '"DRONE_STATE_RECORDED"' in line and '"append_version"' in line)
    record = json.loads(mutated[victim])
    record["payload"]["append_version"] = record["payload"]["append_version"] + 3
    record["event_sha256"] = hashlib.sha256(
        json.dumps({
            "causation_id": record["causation_id"], "correlation_id": record["correlation_id"],
            "event_type": record["event_type"], "payload": record["payload"], "session_id": record["session_id"],
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()  # keep the digest internally consistent; only append_version is tampered
    mutated[victim] = json.dumps(record)
    manifest_path = _write_mutated(tmp_path, lines=mutated)
    with pytest.raises(ReplayIntegrityError, match="stale-authorizing"):
        replay_fixture(manifest_path)
