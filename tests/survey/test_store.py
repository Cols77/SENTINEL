from sentinel.survey.store import SurveyStore


def test_a_frame_records_whether_its_position_was_commanded_or_measured(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count the computers")
    sweep_id = store.add_sweep("RUN1", "RAIL_MID_HEIGHT", stops=4)
    frame_id = store.add_frame(sweep_id, stop_index=0, look_deg=0, x_cm=0, y_cm=0,
                               z_cm=120, heading_deg=0.0, position_kind="COMMANDED",
                               artifact_id="A1", sha256="0" * 64)
    rows = store.frames("RUN1")
    assert [r["position_kind"] for r in rows] == ["COMMANDED"]
    assert rows[0]["frame_id"] == frame_id
    assert rows[0]["analysis_status"] == "PENDING"


def test_flights_used_counts_sweeps_not_frames(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count the computers")
    first = store.add_sweep("RUN1", "RAIL_MID_HEIGHT", stops=4)
    for index in range(4):
        store.add_frame(first, stop_index=index, look_deg=0, x_cm=index * 75, y_cm=0,
                        z_cm=120, heading_deg=0.0, position_kind="COMMANDED",
                        artifact_id=f"A{index}", sha256="0" * 64)
    assert store.flights_used("RUN1") == 1
    store.add_sweep("RUN1", "CLOSE_LOOK_LEFT", stops=2)
    assert store.flights_used("RUN1") == 2


def test_an_unanalysed_frame_is_visible_as_such(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "r")
    sweep_id = store.add_sweep("RUN1", "RAIL_MID_HEIGHT", stops=1)
    frame_id = store.add_frame(sweep_id, stop_index=0, look_deg=0, x_cm=0, y_cm=0,
                               z_cm=120, heading_deg=0.0, position_kind="COMMANDED",
                               artifact_id="A1", sha256="0" * 64)
    store.set_frame_analysis(frame_id, "PROVIDER_ERROR")
    assert store.unanalysed_count("RUN1") == 1


def test_an_aborted_sweep_does_not_consume_a_flight(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count the computers")
    aborted = store.add_sweep("RUN1", "RAIL_MID_HEIGHT", stops=4)
    store.set_sweep_status(aborted, "ABORTED")
    assert store.flights_used("RUN1") == 0
    store.add_sweep("RUN1", "CLOSE_LOOK_LEFT", stops=2)
    assert store.flights_used("RUN1") == 1


def test_a_frame_records_its_capture_and_command_timing_fields(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count the computers")
    sweep_id = store.add_sweep("RUN1", "RAIL_MID_HEIGHT", stops=1)
    frame_id = store.add_frame(sweep_id, stop_index=0, look_deg=0, x_cm=0, y_cm=0,
                               z_cm=120, heading_deg=0.0, position_kind="COMMANDED",
                               artifact_id="A1", sha256="0" * 64,
                               image_path="frames/f0.png", captured_at="2026-09-20T00:00:01Z",
                               command_completed_at="2026-09-20T00:00:00Z", delta_z_cm=55.0)
    rows = store.frames("RUN1")
    assert rows[0]["frame_id"] == frame_id
    assert rows[0]["image_path"] == "frames/f0.png"
    assert rows[0]["captured_at"] == "2026-09-20T00:00:01Z"
    assert rows[0]["command_completed_at"] == "2026-09-20T00:00:00Z"
    assert rows[0]["delta_z_cm"] == 55.0


def test_a_sighting_round_trips_its_band_and_ground_plane_position(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count the computers")
    sweep_id = store.add_sweep("RUN1", "RAIL_MID_HEIGHT", stops=1)
    frame_id = store.add_frame(sweep_id, stop_index=0, look_deg=0, x_cm=0, y_cm=0,
                               z_cm=120, heading_deg=0.0, position_kind="COMMANDED",
                               artifact_id="A1", sha256="0" * 64)
    detection = {"class": "computer", "matched_prompt": "laptop", "raw_score": None,
                 "score_kind": "VLM_CONFIDENCE_BAND", "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                 "description": "d", "provider_item_id": None}
    sighting_id = store.add_sighting(frame_id, detection, band="high",
                                     x_cm=42.0, y_cm=-17.5, depth_cm=300.0)
    rows = store.sightings("RUN1")
    assert len(rows) == 1
    assert rows[0]["sighting_id"] == sighting_id
    assert rows[0]["band"] == "high"
    assert rows[0]["x_cm"] == 42.0
    assert rows[0]["y_cm"] == -17.5
    assert rows[0]["depth_cm"] == 300.0
    assert rows[0]["class"] == "computer"


def test_an_object_round_trips_its_depth_spread_through_counts(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count the computers")
    store.set_objects("RUN1", [
        {"class": "computer", "x_cm": 10.0, "y_cm": 20.0, "distinct_stops": 3,
         "band": "CONFIRMED", "appearance": "silver laptop", "sighting_ids": ["s1", "s2"],
         "depth_spread_pct": 12.5},
    ])
    row = store.connection.execute(
        "SELECT depth_spread_pct FROM objects WHERE run_id = ?", ("RUN1",)).fetchone()
    assert row["depth_spread_pct"] == 12.5
    assert store.counts("RUN1") == {"computer": {"confirmed": 1, "uncertain": 0}}
