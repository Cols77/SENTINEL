import json

from sentinel.survey.read_model import SurveyReadModel
from sentinel.survey.store import SurveyStore


def _seed(store, run_id, *, artifact_id):
    store.create_run(run_id, f"request {run_id}")
    sweep_id = store.add_sweep(run_id, "RAIL", 1)
    frame_id = store.add_frame(
        sweep_id,
        stop_index=0,
        look_deg=0,
        x_cm=1,
        y_cm=2,
        z_cm=3,
        heading_deg=4,
        position_kind="MEASURED",
        artifact_id=artifact_id,
        sha256="a" * 64,
        image_path=f"/private/{run_id}.png",
    )
    store.add_sighting(
        frame_id,
        {
            "class": "computer",
            "matched_prompt": "secret provider prompt",
            "raw_score": 0.7,
            "score_kind": "DETECTOR_RAW",
            "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
            "description": "provider-private prose",
            "provider_item_id": "private-id",
        },
        "high",
        raw_response={"provider_secret": "must not leak"},
    )
    sighting_id = store.sightings(run_id)[0]["sighting_id"]
    store.set_objects(
        run_id,
        [dict(
            **{"class": "computer", "x_cm": 5, "y_cm": 6},
            distinct_stops=1,
            band="UNCERTAIN",
            appearance="private embedding label",
            sighting_ids=[sighting_id],
            depth_spread_pct=0.1,
        )],
    )
    store.add_report(run_id, "public report", [{"class": "computer"}])


def test_public_read_model_is_allowlisted_and_run_scoped(tmp_path):
    store = SurveyStore.open(tmp_path / "survey.sqlite")
    try:
        _seed(store, "run-a", artifact_id="artifact-a")
        _seed(store, "run-b", artifact_id="artifact-b")
        facts = SurveyReadModel(store).read("run-a")
    finally:
        store.connection.close()

    encoded = json.dumps(facts)
    assert "run-b" not in encoded and "artifact-b" not in encoded
    assert "/private/" not in encoded
    assert "provider_secret" not in encoded
    assert "secret provider prompt" not in encoded
    assert "provider-private prose" not in encoded
    assert "private-id" not in encoded
    assert "private embedding label" not in encoded
    assert facts["run"]["run_id"] == "run-a"
    assert facts["frames"][0]["artifact_id"] == "artifact-a"
    assert facts["sightings"][0]["class"] == "computer"
    assert facts["objects"][0]["sighting_ids"]
    assert facts["report"]["body"] == "public report"
    assert set(facts) == {"run", "sweeps", "frames", "sightings", "objects", "counts", "report"}


def test_public_read_model_returns_no_cross_run_facts_for_missing_run(tmp_path):
    store = SurveyStore.open(tmp_path / "survey.sqlite")
    try:
        _seed(store, "run-a", artifact_id="artifact-a")
        assert SurveyReadModel(store).read("missing") is None
    finally:
        store.connection.close()

