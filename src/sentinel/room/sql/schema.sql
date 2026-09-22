CREATE TABLE IF NOT EXISTS room_sessions (
    session_id TEXT PRIMARY KEY,
    request_text TEXT NOT NULL,
    capability_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS room_events (
    sequence_no INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES room_sessions(session_id),
    state_version INTEGER NOT NULL CHECK (state_version > 0),
    event_type TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    idempotency_key TEXT NOT NULL,
    event_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    wall_time TEXT NOT NULL,
    monotonic_ms INTEGER NOT NULL,
    PRIMARY KEY (session_id, sequence_no),
    UNIQUE (session_id, idempotency_key)
);

CREATE TRIGGER IF NOT EXISTS room_events_no_update
BEFORE UPDATE ON room_events
BEGIN
    SELECT RAISE(ABORT, 'room_events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS room_events_no_delete
BEFORE DELETE ON room_events
BEGIN
    SELECT RAISE(ABORT, 'room_events are append-only');
END;
