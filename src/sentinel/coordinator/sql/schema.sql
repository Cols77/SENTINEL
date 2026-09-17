PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    status TEXT NOT NULL CHECK (status IN ('NEW','RUNNING','WAITING_FOR_APPROVAL','WAITING_FOR_OBSERVATION','NEEDS_REVIEW','COMPLETED','FAILED','EMERGENCY')),
    agent_phase TEXT NOT NULL DEFAULT 'RECEIVE_REQUEST',
    latest_event_sequence INTEGER NOT NULL DEFAULT 0 CHECK (latest_event_sequence >= 0),
    mountain_snapshot_version INTEGER NOT NULL DEFAULT 0 CHECK (mountain_snapshot_version >= 0),
    updated_at TEXT NOT NULL,
    emergency_latched INTEGER NOT NULL DEFAULT 0 CHECK (emergency_latched IN (0,1))
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    state_version INTEGER NOT NULL CHECK (state_version >= 0),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('LIVE','SIMULATED','REPLAYED')),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    UNIQUE (run_id, sequence_no)
);

CREATE TRIGGER IF NOT EXISTS events_append_only_update
BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_append_only_delete
BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

CREATE TABLE IF NOT EXISTS idempotency_keys (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    command_type TEXT NOT NULL,
    expected_state_version INTEGER CHECK (expected_state_version IS NULL OR expected_state_version >= 0),
    payload_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (run_id, actor_type, actor_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS operation_records (
    operation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    mission_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','DISPATCHED','SUCCEEDED','FAILED','IN_DOUBT')),
    dispatch_recorded_at TEXT,
    reconciled_at TEXT
);

CREATE TABLE IF NOT EXISTS envelope_registry (
    wire_type TEXT PRIMARY KEY,
    schema_id TEXT NOT NULL UNIQUE,
    contract_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS choreography_registry (
    choreography_id TEXT PRIMARY KEY,
    envelope_schema_id TEXT NOT NULL REFERENCES envelope_registry(schema_id),
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1))
);

CREATE TABLE IF NOT EXISTS approval_actions (
    action_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    plan_id TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('CONFIRM','REJECT')),
    expected_state_version INTEGER NOT NULL CHECK (expected_state_version >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emergency_records (
    signal_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(run_id),
    mission_id TEXT,
    worker_id TEXT NOT NULL,
    latched_at TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('LIVE','SIMULATED','REPLAYED')),
    reason_code TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS emergency_records_append_only_update
BEFORE UPDATE ON emergency_records BEGIN SELECT RAISE(ABORT, 'emergency records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS emergency_records_append_only_delete
BEFORE DELETE ON emergency_records BEGIN SELECT RAISE(ABORT, 'emergency records are immutable'); END;

CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(run_id),
    catalogue_id TEXT NOT NULL,
    catalogue_revision TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK (mode IN ('MARKER_VERIFIED','ROUTE_ONLY_2_ZONE')),
    station_ids_json TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS plans_immutable_update
BEFORE UPDATE ON plans BEGIN SELECT RAISE(ABORT, 'plans are immutable'); END;
CREATE TRIGGER IF NOT EXISTS plans_immutable_delete
BEFORE DELETE ON plans BEGIN SELECT RAISE(ABORT, 'plans are immutable'); END;

CREATE TABLE IF NOT EXISTS plan_approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    plan_id TEXT NOT NULL REFERENCES plans(plan_id),
    plan_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('CONFIRM','REJECT')),
    expected_state_version INTEGER NOT NULL CHECK (expected_state_version >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    station_id TEXT NOT NULL,
    artifact_id TEXT,
    observed_at TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('LIVE','SIMULATED','REPLAYED'))
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size > 0 AND byte_size <= 10485760),
    sha256 TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('LIVE','SIMULATED','REPLAYED'))
);
CREATE TABLE IF NOT EXISTS perception_references (
    result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    observation_id TEXT NOT NULL REFERENCES observations(observation_id),
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    station_id TEXT NOT NULL,
    status TEXT NOT NULL,
    perception_result_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_rankings (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    rank_no INTEGER NOT NULL CHECK (rank_no > 0),
    patroller_id TEXT NOT NULL,
    score REAL NOT NULL,
    PRIMARY KEY (task_id, rank_no)
);
