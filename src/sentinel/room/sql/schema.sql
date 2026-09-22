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

-- Perception evidence schema (also applied to existing databases by migration 004).
CREATE TABLE IF NOT EXISTS room_perception_payloads (
    payload_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    resolved_model TEXT,
    request_digest TEXT NOT NULL,
    frame_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS room_perception_payload_identity
ON room_perception_payloads (provider, COALESCE(resolved_model, ''), request_digest, frame_sha256);

CREATE TABLE IF NOT EXISTS room_observations (
    session_id TEXT NOT NULL REFERENCES room_sessions(session_id),
    observation_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    frame_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    snapshot_version INTEGER NOT NULL CHECK (snapshot_version >= 0),
    request_digest TEXT NOT NULL,
    frame_path TEXT NOT NULL,
    frame_sha256 TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    pose_revision_id TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    request_json TEXT NOT NULL,
    PRIMARY KEY (session_id, observation_id)
);

CREATE TABLE IF NOT EXISTS room_perception_results (
    result_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    payload_id TEXT NOT NULL REFERENCES room_perception_payloads(payload_id),
    status TEXT NOT NULL CHECK (status IN ('SUCCEEDED', 'FAILED')),
    provider TEXT NOT NULL,
    resolved_model TEXT,
    identity_status TEXT NOT NULL CHECK (identity_status IN ('VERIFIED', 'UNVERIFIED')),
    completed_at TEXT NOT NULL,
    error_category TEXT,
    authorizes_current_state INTEGER NOT NULL CHECK (authorizes_current_state IN (0, 1)),
    result_json TEXT NOT NULL,
    UNIQUE (session_id, observation_id),
    UNIQUE (session_id, observation_id, result_id),
    FOREIGN KEY (session_id, observation_id) REFERENCES room_observations(session_id, observation_id),
    CHECK (status != 'SUCCEEDED' OR (identity_status = 'VERIFIED' AND resolved_model IS NOT NULL AND error_category IS NULL)),
    CHECK (status != 'FAILED' OR (error_category IS NOT NULL AND authorizes_current_state = 0))
);

CREATE TABLE IF NOT EXISTS room_detection_evidence (
    session_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    detection_id TEXT NOT NULL,
    result_id TEXT NOT NULL,
    class_name TEXT NOT NULL,
    confidence_band TEXT NOT NULL CHECK (confidence_band IN ('high', 'medium', 'low')),
    raw_score REAL,
    score_kind TEXT,
    original_bbox_json TEXT NOT NULL,
    bbox_coordinate_space TEXT NOT NULL CHECK (bbox_coordinate_space IN ('FULL_FRAME_PIXELS', 'TILE_PIXELS')),
    frame_width_px INTEGER NOT NULL CHECK (frame_width_px > 0),
    frame_height_px INTEGER NOT NULL CHECK (frame_height_px > 0),
    tile_origin_json TEXT NOT NULL,
    full_frame_bbox_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    PRIMARY KEY (session_id, observation_id, detection_id),
    FOREIGN KEY (session_id, observation_id, result_id) REFERENCES room_perception_results(session_id, observation_id, result_id)
);

CREATE TABLE IF NOT EXISTS room_object_hypotheses (
    session_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    detection_id TEXT NOT NULL,
    class_name TEXT NOT NULL,
    PRIMARY KEY (session_id, hypothesis_id),
    FOREIGN KEY (session_id, observation_id, detection_id)
        REFERENCES room_detection_evidence(session_id, observation_id, detection_id)
);

CREATE TABLE IF NOT EXISTS room_hypothesis_revisions (
    session_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    revision_no INTEGER NOT NULL CHECK (revision_no > 0),
    result_id TEXT NOT NULL REFERENCES room_perception_results(result_id),
    snapshot_version INTEGER NOT NULL CHECK (snapshot_version >= 0),
    authorizes_current_state INTEGER NOT NULL CHECK (authorizes_current_state IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, hypothesis_id, revision_no),
    FOREIGN KEY (session_id, hypothesis_id) REFERENCES room_object_hypotheses(session_id, hypothesis_id)
);

CREATE TRIGGER IF NOT EXISTS room_perception_payloads_no_update
BEFORE UPDATE ON room_perception_payloads
BEGIN
    SELECT RAISE(ABORT, 'room_perception_payloads are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_perception_payloads_no_delete
BEFORE DELETE ON room_perception_payloads
BEGIN
    SELECT RAISE(ABORT, 'room_perception_payloads are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_perception_payloads_no_replace
BEFORE INSERT ON room_perception_payloads
WHEN EXISTS (SELECT 1 FROM room_perception_payloads WHERE payload_id = NEW.payload_id OR (provider = NEW.provider AND COALESCE(resolved_model, '') = COALESCE(NEW.resolved_model, '') AND request_digest = NEW.request_digest AND frame_sha256 = NEW.frame_sha256))
BEGIN
    SELECT RAISE(ABORT, 'room_perception_payloads identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS room_observations_no_update
BEFORE UPDATE ON room_observations
BEGIN
    SELECT RAISE(ABORT, 'room_observations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_observations_no_delete
BEFORE DELETE ON room_observations
BEGIN
    SELECT RAISE(ABORT, 'room_observations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_observations_no_replace
BEFORE INSERT ON room_observations
WHEN EXISTS (SELECT 1 FROM room_observations WHERE session_id = NEW.session_id AND observation_id = NEW.observation_id)
BEGIN
    SELECT RAISE(ABORT, 'room_observations identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS room_perception_results_no_update
BEFORE UPDATE ON room_perception_results
BEGIN
    SELECT RAISE(ABORT, 'room_perception_results are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_perception_results_no_delete
BEFORE DELETE ON room_perception_results
BEGIN
    SELECT RAISE(ABORT, 'room_perception_results are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_perception_results_no_replace
BEFORE INSERT ON room_perception_results
WHEN EXISTS (SELECT 1 FROM room_perception_results WHERE result_id = NEW.result_id OR (session_id = NEW.session_id AND observation_id = NEW.observation_id))
BEGIN
    SELECT RAISE(ABORT, 'room_perception_results identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS room_detection_evidence_no_update
BEFORE UPDATE ON room_detection_evidence
BEGIN
    SELECT RAISE(ABORT, 'room_detection_evidence are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_detection_evidence_no_delete
BEFORE DELETE ON room_detection_evidence
BEGIN
    SELECT RAISE(ABORT, 'room_detection_evidence are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_detection_evidence_no_replace
BEFORE INSERT ON room_detection_evidence
WHEN EXISTS (SELECT 1 FROM room_detection_evidence WHERE session_id = NEW.session_id AND observation_id = NEW.observation_id AND detection_id = NEW.detection_id)
BEGIN
    SELECT RAISE(ABORT, 'room_detection_evidence identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS room_object_hypotheses_no_update
BEFORE UPDATE ON room_object_hypotheses
BEGIN
    SELECT RAISE(ABORT, 'room_object_hypotheses are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_object_hypotheses_no_delete
BEFORE DELETE ON room_object_hypotheses
BEGIN
    SELECT RAISE(ABORT, 'room_object_hypotheses are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_object_hypotheses_no_replace
BEFORE INSERT ON room_object_hypotheses
WHEN EXISTS (SELECT 1 FROM room_object_hypotheses WHERE session_id = NEW.session_id AND hypothesis_id = NEW.hypothesis_id)
BEGIN
    SELECT RAISE(ABORT, 'room_object_hypotheses identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS room_hypothesis_revisions_no_update
BEFORE UPDATE ON room_hypothesis_revisions
BEGIN
    SELECT RAISE(ABORT, 'room_hypothesis_revisions are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_hypothesis_revisions_no_delete
BEFORE DELETE ON room_hypothesis_revisions
BEGIN
    SELECT RAISE(ABORT, 'room_hypothesis_revisions are append-only');
END;
CREATE TRIGGER IF NOT EXISTS room_hypothesis_revisions_no_replace
BEFORE INSERT ON room_hypothesis_revisions
WHEN EXISTS (SELECT 1 FROM room_hypothesis_revisions WHERE session_id = NEW.session_id AND hypothesis_id = NEW.hypothesis_id AND revision_no = NEW.revision_no)
BEGIN
    SELECT RAISE(ABORT, 'room_hypothesis_revisions identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS room_detection_evidence_verified
BEFORE INSERT ON room_detection_evidence
WHEN NOT EXISTS (
    SELECT 1 FROM room_perception_results WHERE result_id = NEW.result_id
    AND session_id = NEW.session_id AND observation_id = NEW.observation_id
    AND status = 'SUCCEEDED' AND identity_status = 'VERIFIED'
)
BEGIN
    SELECT RAISE(ABORT, 'detection evidence requires verified success');
END;
CREATE TRIGGER IF NOT EXISTS room_hypothesis_revisions_verified
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
