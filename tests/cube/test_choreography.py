"""A choreography from a prompt: the model writes the stops, code decides."""
import json
from pathlib import Path

import pytest

from sentinel.cube.choreography import (MAX_CALLS, TOOL_NAME, mission_document,
                                        mission_from_tool_call, request_choreography, tool_schema)
from sentinel.cube.coverage import CoverageModel
from sentinel.cube.site import load_site

ROOT = Path(__file__).resolve().parents[2]
SITE = load_site(ROOT / "docs/wp5-flight3-site.json")
COVERAGE = CoverageModel.from_files(SITE, ROOT / "calibration/tag-map-calibrated.json")
FIXTURE = json.loads((ROOT / "fixtures/choreo/square-diagonal.json").read_text())


def _reply(waypoints, reason="because"):
    arguments = json.dumps({"waypoints": waypoints, "reason": reason})
    return {"choices": [{"message": {"tool_calls": [
        {"function": {"name": TOOL_NAME, "arguments": arguments}}]}}]}


def _stop(name, x, y, z=90):
    return {"name": name, "x_cm": x, "y_cm": y, "z_cm": z}


def _asking(replies):
    sent = []

    def post(payload):
        sent.append(payload)
        return replies[len(sent) - 1]
    return post, sent


def test_the_recorded_prompt_gives_a_checked_mission_that_returns_home():
    post, sent = _asking(FIXTURE["responses"])
    result = request_choreography(FIXTURE["prompt"], SITE, COVERAGE, post, model="m", z_cm=90)
    mission = result["mission"]
    assert mission is not None and len(sent) == 1
    assert [w.name for w in mission.waypoints] == ["SQ_FL", "SQ_NL", "SQ_NR", "SQ_FR", "SQ_FL2", "DIAG"]
    assert mission.return_to_start and mission.start_x_cm is None and mission.leg_speed_cm_s == 50
    doc = mission_document(mission, prompt=FIXTURE["prompt"], reason="r")
    assert doc["schema_version"] == "cube-mission-v1" and doc["return_to_start"] is True


def test_the_model_is_offered_one_tool_bounded_by_the_commanded_area():
    post, sent = _asking(FIXTURE["responses"])
    request_choreography(FIXTURE["prompt"], SITE, COVERAGE, post, model="m", z_cm=90)
    payload = sent[0]
    assert payload["tool_choice"]["function"]["name"] == TOOL_NAME and len(payload["tools"]) == 1
    item = payload["tools"][0]["function"]["parameters"]["properties"]["waypoints"]["items"]
    env = SITE.envelope()
    assert (item["properties"]["x_cm"]["minimum"], item["properties"]["x_cm"]["maximum"]) == \
        (env.min_x_cm, env.max_x_cm)
    assert "Marker coverage" in payload["messages"][1]["content"]
    assert FIXTURE["prompt"] in payload["messages"][1]["content"]


def test_a_refused_plan_is_sent_back_once_with_the_reasons():
    bad = _reply([_stop("A", 250, 200), _stop("B", 500, 200)])
    good = _reply([_stop("A", 250, 200), _stop("B", 280, 160)])
    post, sent = _asking([bad, good])
    result = request_choreography("p", SITE, COVERAGE, post, model="m", z_cm=90)
    assert result["mission"] is not None and len(sent) == 2
    assert "B (500, 200, 90) is outside the bounds" in sent[1]["messages"][-1]["content"]


def test_two_refused_plans_fly_nothing():
    bad = _reply([_stop("A", 250, 200), _stop("A", 280, 160)])
    post, sent = _asking([bad, bad])
    result = request_choreography("p", SITE, COVERAGE, post, model="m", z_cm=90)
    assert result["mission"] is None and len(sent) == MAX_CALLS
    assert any("used twice" in e for e in result["errors"])


def test_a_stop_the_tags_cannot_be_seen_from_is_refused():
    blind = CoverageModel(SITE.__class__(**{**SITE.__dict__, "altitude_bias_cm": 60}),
                          json.loads((ROOT / "calibration/tag-map-calibrated.json").read_text())
                          ["camera_calibration"])
    mission, errors = mission_from_tool_call({"waypoints": [_stop("NEAR", 250, 240, 100)]},
                                             SITE, blind)
    assert mission is None and "markers can be lost" in errors[0]


@pytest.mark.parametrize("stops, message", [
    ([_stop("RETURN", 250, 200)], "not RETURN or START"),
    ([_stop("lower", 250, 200)], "capitals"),
    ([_stop("A", 250.5, 200)], "whole numbers"),
    ([_stop("A", 250, 200, 150)], "outside the bounds"),
    ([_stop("A", 250, 200), _stop("B", 255, 205)], "under the 20 cm minimum"),
    ([_stop(f"S{i}", 200 + 10 * i, 200) for i in range(8)], "1-7 stops"),
])
def test_impossible_plans_are_refused_with_a_reason(stops, message):
    mission, errors = mission_from_tool_call({"waypoints": stops}, SITE, COVERAGE)
    assert mission is None and any(message in e for e in errors), errors


def test_a_reply_without_the_tool_call_counts_as_refused():
    post, _ = _asking([{"choices": [{"message": {"content": "sure!"}}]}] * 2)
    result = request_choreography("p", SITE, COVERAGE, post, model="m", z_cm=90)
    assert result["mission"] is None and "did not call" in result["errors"][0]


def test_tool_schema_caps_the_stops_so_return_fits():
    items = tool_schema(SITE)["function"]["parameters"]["properties"]["waypoints"]
    assert items["maxItems"] == 7


def test_the_live_path_needs_the_paid_call_flag(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("choreo_run", ROOT / "scripts/choreo_run.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    with pytest.raises(SystemExit):
        script.main(["--live", "--prompt", "a square"])
