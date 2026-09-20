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
