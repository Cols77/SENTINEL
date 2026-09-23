-- Widen the result identity contract without rewriting observation/evidence rows.
-- RoomStore disables FK enforcement before BEGIN, validates every FK before
-- commit, and restores enforcement even when this rebuild rolls back.
DROP TRIGGER IF EXISTS room_detection_evidence_verified;
DROP TRIGGER IF EXISTS room_hypothesis_revisions_verified;
CREATE TABLE room_perception_results_v5 (
    result_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    payload_id TEXT NOT NULL REFERENCES room_perception_payloads(payload_id),
    status TEXT NOT NULL CHECK (status IN ('SUCCEEDED', 'FAILED')),
    provider TEXT NOT NULL,
    resolved_model TEXT,
    identity_status TEXT NOT NULL CHECK (identity_status IN ('VERIFIED', 'UNVERIFIED', 'MISMATCH')),
    completed_at TEXT NOT NULL,
    error_category TEXT,
    authorizes_current_state INTEGER NOT NULL CHECK (authorizes_current_state IN (0, 1)),
    result_json TEXT NOT NULL,
    UNIQUE (session_id, observation_id),
    UNIQUE (session_id, observation_id, result_id),
    FOREIGN KEY (session_id, observation_id) REFERENCES room_observations(session_id, observation_id),
    CHECK (status != 'SUCCEEDED' OR (identity_status = 'VERIFIED' AND resolved_model IS NOT NULL AND error_category IS NULL)),
    CHECK (status != 'FAILED' OR (error_category IS NOT NULL AND authorizes_current_state = 0)),
    CHECK (identity_status != 'MISMATCH' OR
        (status = 'FAILED' AND error_category = 'IDENTITY_MISMATCH' AND resolved_model IS NOT NULL)),
    CHECK (identity_status != 'UNVERIFIED' OR resolved_model IS NULL),
    CHECK (identity_status != 'VERIFIED' OR resolved_model IS NOT NULL),
    CHECK (length(trim(provider)) BETWEEN 1 AND 4096),
    CHECK (resolved_model IS NULL OR length(trim(resolved_model)) BETWEEN 1 AND 4096)
);

INSERT INTO room_perception_results_v5 SELECT * FROM room_perception_results;
DROP TABLE room_perception_results;
ALTER TABLE room_perception_results_v5 RENAME TO room_perception_results;

CREATE TRIGGER room_perception_results_no_update
BEFORE UPDATE ON room_perception_results
BEGIN
    SELECT RAISE(ABORT, 'room_perception_results are append-only');
END;
CREATE TRIGGER room_perception_results_no_delete
BEFORE DELETE ON room_perception_results
BEGIN
    SELECT RAISE(ABORT, 'room_perception_results are append-only');
END;
CREATE TRIGGER room_perception_results_no_replace
BEFORE INSERT ON room_perception_results
WHEN EXISTS (
    SELECT 1 FROM room_perception_results WHERE result_id = NEW.result_id
       OR (session_id = NEW.session_id AND observation_id = NEW.observation_id)
)
BEGIN
    SELECT RAISE(ABORT, 'room_perception_results identity is immutable');
END;
CREATE TRIGGER IF NOT EXISTS room_perception_results_identity
BEFORE INSERT ON room_perception_results
WHEN NEW.identity_status IN ('VERIFIED', 'MISMATCH') AND NOT EXISTS (
    SELECT 1 FROM room_observations AS observation
    WHERE observation.session_id = NEW.session_id AND observation.observation_id = NEW.observation_id
      AND ((NEW.identity_status = 'VERIFIED' AND NEW.resolved_model = observation.requested_model)
        OR (NEW.identity_status = 'MISMATCH' AND NEW.resolved_model != observation.requested_model))
)
BEGIN
    SELECT RAISE(ABORT, 'inconsistent perception model identity');
END;

CREATE TRIGGER room_detection_evidence_verified
BEFORE INSERT ON room_detection_evidence
WHEN NOT EXISTS (
    SELECT 1 FROM room_perception_results WHERE result_id = NEW.result_id
    AND session_id = NEW.session_id AND observation_id = NEW.observation_id
    AND status = 'SUCCEEDED' AND identity_status = 'VERIFIED'
)
BEGIN
    SELECT RAISE(ABORT, 'detection evidence requires verified success');
END;
CREATE TRIGGER room_hypothesis_revisions_verified
BEFORE INSERT ON room_hypothesis_revisions
WHEN NOT EXISTS (
    SELECT 1 FROM room_perception_results AS result
    JOIN room_observations AS observation
      ON observation.session_id = result.session_id AND observation.observation_id = result.observation_id
    WHERE result.result_id = NEW.result_id AND result.session_id = NEW.session_id
    AND result.status = 'SUCCEEDED' AND result.identity_status = 'VERIFIED'
    AND observation.snapshot_version = NEW.snapshot_version
    AND result.authorizes_current_state = NEW.authorizes_current_state
)
BEGIN
    SELECT RAISE(ABORT, 'hypothesis revision requires verified session evidence');
END;
