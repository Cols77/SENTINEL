from sentinel.contracts.ddl_manifest import load_manifest, manifest_sql, validate_manifest


def test_manifest_is_versioned_and_covers_wp0_invariants():
    manifest = load_manifest()
    assert manifest["manifest_version"] == "sentinel.v0"
    names = set(manifest["invariants"])
    for required in (
        "event_uniqueness_order",
        "scoped_idempotency_uniqueness",
        "state_version_concurrency",
        "append_only_events",
        "tool_operation_states",
        "envelope_choreography_registry_references",
        "approval_actions",
        "emergency_records",
    ):
        assert required in names
    assert validate_manifest(manifest) is manifest
    assert "CREATE TABLE events" in manifest_sql(manifest)
