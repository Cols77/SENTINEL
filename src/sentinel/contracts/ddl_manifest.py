"""Checked-in declarative SQLite contract manifest for WP0."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


MANIFEST_PATH = Path(__file__).with_name("ddl_manifest.json")
_REQUIRED_INVARIANTS = {
    "event_uniqueness_order",
    "scoped_idempotency_uniqueness",
    "state_version_concurrency",
    "append_only_events",
    "tool_operation_states",
    "envelope_choreography_registry_references",
    "approval_actions",
    "emergency_records",
}


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def validate_manifest(manifest: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    value = load_manifest() if manifest is None else manifest
    if value.get("manifest_version") != "sentinel.v0" or value.get("dialect") != "sqlite3":
        raise ValueError("unsupported WP0 manifest version or dialect")
    invariants = value.get("invariants", {})
    missing = _REQUIRED_INVARIANTS - set(invariants)
    if missing:
        raise ValueError(f"manifest missing invariants: {sorted(missing)}")
    names = [table.get("name") for table in value.get("tables", [])]
    required_tables = {"runs", "events", "idempotency_keys", "operation_records", "envelope_registry", "choreography_registry", "approval_actions", "emergency_records"}
    if not required_tables <= set(names):
        raise ValueError(f"manifest missing tables: {sorted(required_tables - set(names))}")
    if not value.get("triggers"):
        raise ValueError("manifest must declare append-only triggers")
    return value


def manifest_sql(manifest: Mapping[str, Any] | None = None) -> str:
    value = validate_manifest(manifest)
    statements = [table["sql"] for table in value["tables"]]
    statements.extend(value["triggers"])
    return "\n".join(statements)
