CREATE TABLE room_event_batches (
    batch_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES room_sessions(session_id),
    batch_sha256 TEXT NOT NULL,
    event_count INTEGER NOT NULL CHECK (event_count > 0),
    legacy_singleton INTEGER NOT NULL DEFAULT 0 CHECK (legacy_singleton IN (0, 1))
);

CREATE TABLE room_event_batch_members (
    batch_id TEXT NOT NULL REFERENCES room_event_batches(batch_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    event_id TEXT NOT NULL UNIQUE REFERENCES room_events(event_id),
    PRIMARY KEY (batch_id, ordinal)
);

CREATE TRIGGER room_event_batches_no_update
BEFORE UPDATE ON room_event_batches
BEGIN
    SELECT RAISE(ABORT, 'room_event_batches are append-only');
END;

CREATE TRIGGER room_event_batches_no_delete
BEFORE DELETE ON room_event_batches
BEGIN
    SELECT RAISE(ABORT, 'room_event_batches are append-only');
END;

CREATE TRIGGER room_event_batches_no_replace
BEFORE INSERT ON room_event_batches
WHEN EXISTS (SELECT 1 FROM room_event_batches WHERE batch_id = NEW.batch_id)
BEGIN
    SELECT RAISE(ABORT, 'room_event_batches identity is immutable');
END;

CREATE TRIGGER room_event_batch_members_no_update
BEFORE UPDATE ON room_event_batch_members
BEGIN
    SELECT RAISE(ABORT, 'room_event_batch_members are append-only');
END;

CREATE TRIGGER room_event_batch_members_no_delete
BEFORE DELETE ON room_event_batch_members
BEGIN
    SELECT RAISE(ABORT, 'room_event_batch_members are append-only');
END;

CREATE TRIGGER room_event_batch_members_no_replace
BEFORE INSERT ON room_event_batch_members
WHEN EXISTS (
    SELECT 1 FROM room_event_batch_members
    WHERE event_id = NEW.event_id
       OR (batch_id = NEW.batch_id AND ordinal = NEW.ordinal)
)
BEGIN
    SELECT RAISE(ABORT, 'room_event_batch_members identity is immutable');
END;

CREATE TRIGGER room_event_batch_members_validate
BEFORE INSERT ON room_event_batch_members
WHEN NOT EXISTS (
    SELECT 1 FROM room_event_batches AS batch
    JOIN room_events AS event ON event.session_id = batch.session_id
    WHERE batch.batch_id = NEW.batch_id AND event.event_id = NEW.event_id
      AND NEW.ordinal < batch.event_count
)
BEGIN
    SELECT RAISE(ABORT, 'invalid room event batch membership');
END;
