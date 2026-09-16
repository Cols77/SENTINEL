import json

from sentinel.contracts.fixtures import (
    canonical_json,
    fixture_path,
    load_events,
    load_fixture,
    mountain_fixture,
    fixture_provenance,
)
from sentinel.contracts.schemas import validate_document
from sentinel.contracts.validators import validate_event_stream, validate_mountain_fixture


def test_required_v0_fixture_files_exist_and_have_simulated_provenance():
    for name in ("events-success.jsonl", "events-reject.jsonl", "events-in-doubt.jsonl", "perception-results.json", "provider-profile.json"):
        assert fixture_path(name).exists()
    assert fixture_provenance() == "SIMULATED"


def test_event_fixture_files_are_schema_valid_and_restartable():
    events = load_events("events-success.jsonl")
    assert validate_event_stream(events) == events
    assert [event["sequence_no"] for event in load_events("events-in-doubt.jsonl")] == [1, 2, 3]


def test_mountain_fixture_is_deterministically_serialized():
    mountain = mountain_fixture()
    validate_mountain_fixture(mountain)
    assert canonical_json(mountain) == canonical_json(json.loads(canonical_json(mountain)))


def test_perception_fixture_bundle_contains_success_scoreless_and_provider_error():
    bundle = load_fixture("perception-results.json")
    assert {"success", "scoreless", "provider_error"} <= set(bundle)
    for result in bundle.values():
        validate_document(result)


def test_provider_profile_fixture_is_explicitly_simulated_and_valid():
    profile = load_fixture("provider-profile.json")
    assert profile["approval_status"] == "APPROVED"
    assert profile["replay_allowed_in_judged_path"] is False
