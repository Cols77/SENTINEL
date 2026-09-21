-- src/sentinel/survey/sql/schema.sql
CREATE TABLE IF NOT EXISTS survey_runs (
    run_id TEXT PRIMARY KEY,
    request_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sweeps (
    sweep_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES survey_runs(run_id),
    option_id TEXT NOT NULL,
    stops INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'FLOWN', 'ABORTED')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS frames (
    frame_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES sweeps(sweep_id),
    stop_index INTEGER NOT NULL,
    look_deg REAL NOT NULL,
    x_cm REAL NOT NULL, y_cm REAL NOT NULL, z_cm REAL NOT NULL,
    heading_deg REAL NOT NULL,
    position_kind TEXT NOT NULL CHECK (position_kind IN ('COMMANDED', 'MEASURED')),
    artifact_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    image_path TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    command_completed_at TEXT,
    delta_z_cm REAL NOT NULL,
    analysis_status TEXT NOT NULL DEFAULT 'PENDING',
    person_regions_blurred INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sightings (
    sighting_id TEXT PRIMARY KEY,
    frame_id TEXT NOT NULL REFERENCES frames(frame_id),
    class TEXT NOT NULL,
    matched_prompt TEXT,
    raw_score REAL,
    score_kind TEXT NOT NULL,
    bbox_json TEXT,
    description TEXT,
    provider_item_id TEXT,
    raw_response_json TEXT,
    band TEXT NOT NULL,
    x_cm REAL,
    y_cm REAL,
    depth_cm REAL
);
CREATE TABLE IF NOT EXISTS objects (
    object_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES survey_runs(run_id),
    class TEXT NOT NULL,
    x_cm REAL, y_cm REAL,
    distinct_stops INTEGER NOT NULL,
    band TEXT NOT NULL CHECK (band IN ('CONFIRMED', 'UNCERTAIN')),
    appearance TEXT,
    sighting_ids_json TEXT NOT NULL,
    depth_spread_pct REAL
);
CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES survey_runs(run_id),
    body TEXT NOT NULL,
    class_counts_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS filming_consent (
    location TEXT NOT NULL,
    consent_date TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    PRIMARY KEY (location, consent_date)
);
CREATE TABLE IF NOT EXISTS survey_events (
    sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
