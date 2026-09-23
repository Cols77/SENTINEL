-- No untrusted envelope fields, raw lines, paths or exception text belong here.
-- Large records hash only a bounded prefix; hashed_byte_length makes that explicit.
CREATE TABLE IF NOT EXISTS room_inbox_rejections (
    rejection_id TEXT PRIMARY KEY CHECK (length(rejection_id) = 64 AND rejection_id NOT GLOB '*[^0-9a-f]*'),
    inbox_id TEXT NOT NULL CHECK (length(inbox_id) = 64 AND inbox_id NOT GLOB '*[^0-9a-f]*'),
    byte_offset INTEGER NOT NULL CHECK (byte_offset >= 0),
    byte_length INTEGER NOT NULL CHECK (byte_length > 0),
    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64 AND record_sha256 NOT GLOB '*[^0-9a-f]*'),
    hashed_byte_length INTEGER NOT NULL CHECK (hashed_byte_length > 0 AND hashed_byte_length <= byte_length AND hashed_byte_length <= 1048576),
    category TEXT NOT NULL CHECK (category IN ('JSON', 'SCHEMA', 'RECORD_TOO_LARGE', 'INVALID_EVIDENCE', 'IDENTITY_CONFLICT')),
    rejected_at TEXT NOT NULL,
    monotonic_ms INTEGER NOT NULL CHECK (monotonic_ms >= 0),
    UNIQUE (inbox_id, byte_offset, byte_length, record_sha256)
);

CREATE TRIGGER IF NOT EXISTS room_inbox_rejections_no_update
BEFORE UPDATE ON room_inbox_rejections
BEGIN
    SELECT RAISE(ABORT, 'room_inbox_rejections are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_inbox_rejections_no_delete
BEFORE DELETE ON room_inbox_rejections
BEGIN
    SELECT RAISE(ABORT, 'room_inbox_rejections are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_inbox_rejections_no_replace
BEFORE INSERT ON room_inbox_rejections
WHEN EXISTS (
    SELECT 1 FROM room_inbox_rejections WHERE rejection_id = NEW.rejection_id
    OR (inbox_id = NEW.inbox_id AND byte_offset = NEW.byte_offset
        AND byte_length = NEW.byte_length AND record_sha256 = NEW.record_sha256)
)
BEGIN
    SELECT RAISE(ABORT, 'room_inbox_rejections identity is immutable');
END;

-- Mutable recovery metadata only. A cursor never replaces an immutable receipt
-- and never contains a partial line, decoded values, path or exception text.
CREATE TABLE IF NOT EXISTS room_inbox_cursors (
    inbox_id TEXT PRIMARY KEY CHECK (length(inbox_id) = 64 AND inbox_id NOT GLOB '*[^0-9a-f]*'),
    byte_offset INTEGER NOT NULL CHECK (byte_offset >= 0),
    scan_offset INTEGER NOT NULL CHECK (scan_offset >= byte_offset AND scan_offset <= file_size),
    record_sha256 TEXT CHECK (length(record_sha256) = 64 AND record_sha256 NOT GLOB '*[^0-9a-f]*'),
    file_size INTEGER NOT NULL CHECK (file_size >= 0),
    prefix_length INTEGER NOT NULL CHECK (prefix_length BETWEEN 0 AND 4096),
    prefix_sha256 TEXT NOT NULL CHECK (length(prefix_sha256) = 64 AND prefix_sha256 NOT GLOB '*[^0-9a-f]*'),
    checkpoint_offset INTEGER NOT NULL CHECK (checkpoint_offset >= 0),
    checkpoint_length INTEGER NOT NULL CHECK (checkpoint_length BETWEEN 0 AND 4096),
    checkpoint_sha256 TEXT NOT NULL CHECK (length(checkpoint_sha256) = 64 AND checkpoint_sha256 NOT GLOB '*[^0-9a-f]*'),
    updated_at TEXT NOT NULL,
    CHECK ((record_sha256 IS NULL AND scan_offset = byte_offset)
        OR (record_sha256 IS NOT NULL AND scan_offset - byte_offset >= 1048576)),
    CHECK (prefix_length <= file_size AND checkpoint_offset + checkpoint_length <= scan_offset)
);
