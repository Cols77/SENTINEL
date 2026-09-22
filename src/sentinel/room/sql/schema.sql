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

CREATE TRIGGER IF NOT EXISTS room_events_no_replace
BEFORE INSERT ON room_events
WHEN EXISTS (
    SELECT 1 FROM room_events AS existing
    WHERE existing.event_id = NEW.event_id
       OR (existing.session_id = NEW.session_id AND existing.sequence_no = NEW.sequence_no)
       OR (existing.session_id = NEW.session_id AND existing.idempotency_key = NEW.idempotency_key)
)
BEGIN
    SELECT RAISE(ABORT, 'room_events identity is immutable');
END;
