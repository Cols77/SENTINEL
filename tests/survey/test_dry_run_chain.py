"""The whole survey chain in a dry run: site file -> offered sweeps -> cube
mission -> frames -> replayed detections -> merge -> report. The WP5 session's
first end-to-end dry run (2026-09-22) found three defects this file pins:
a crash on frames with no image, a first sweep that could never confirm
anything, and a report that dropped what it could not confirm."""
import json
import math

from scripts.survey_run import _DefaultJudge, main
from sentinel.camera import TELLO_MEASURED
from sentinel.survey.candidates import propose_candidates, review_candidates
from sentinel.survey.cube_bridge import envelope_from_site
from sentinel.survey.merge import CONFIRM_MIN_STOPS, merge_sightings
from sentinel.survey.options import generate_options
from sentinel.survey.report import unconfirmed_caveat
from sentinel.cube.site import load_site

SITE = "docs/wp5-flight2-site.json"
SURFACE_Z_CM = 75.0


def _options():
    return generate_options(envelope_from_site(load_site(SITE), standoff_cm=300), TELLO_MEASURED)


def _camera_z_cm(rail):
    """Where the camera really is: the dry-run client reports the commanded height
    as height?, and the drone flies the site's takeoff height and altimeter bias
    above that (WP5, 2026-09-22: flight2 site bias 18 cm). The scene is rendered
    from the real camera, as a real flight's photographs would be."""
    site = load_site(SITE)
    return (rail.z_cm + getattr(site, "takeoff_z_cm", 0.0)
            + getattr(site, "altitude_bias_cm", 0.0))


def _laptop_as_seen_from(stop_x, stop_y, z_cm, target=(250.0, 425.0)):
    """A provider item for a laptop on the surface, projected with the measured camera."""
    camera = TELLO_MEASURED
    lateral, forward = target[0] - stop_x, target[1] - stop_y
    delta_z = z_cm - SURFACE_Z_CM
    pitch = math.radians(camera.pitch_down_deg)
    depth = forward * math.cos(pitch) + delta_z * math.sin(pitch)
    below = -forward * math.sin(pitch) + delta_z * math.cos(pitch)
    u = camera.cx + camera.fx * lateral / depth
    v = camera.cy + camera.fy * below / depth
    return {"label": "laptop", "confidence": "high", "description": "a laptop",
            "x": (u - 40) / 960, "y": (v - 25) / 720, "width": 80 / 960, "height": 50 / 720}


def test_the_whole_chain_dry_runs_on_the_flight_site_and_confirms_one_laptop(tmp_path, capsys):
    rail = _options()[0]
    responses = tmp_path / "responses"
    responses.mkdir()
    for index, (x_cm, y_cm) in enumerate(rail.stops):
        item = _laptop_as_seen_from(x_cm, y_cm, _camera_z_cm(rail))
        (responses / f"{rail.option_id}_{index}-0.json").write_text(
            json.dumps({"objects": [item]}), encoding="utf-8")

    code = main(["--mode", "judge", "--site", SITE, "--surface-z-cm", str(SURFACE_Z_CM),
                 "--responses", str(responses), "--db", str(tmp_path / "s.sqlite3"),
                 "--request", "count the computers", "--auto-approve"])
    summary = json.loads(capsys.readouterr().out)

    assert code == 0 and summary["outcome"]["status"] == "REPORTED"
    assert summary["frame_counts"]["commanded"] == len(rail.stops)   # a dry run measures nothing
    assert summary["outcome"]["counts"]["computer"]["confirmed"] == 1


def _run_scene(tmp_path, capsys, targets, visible=lambda target_index, stop_index: True):
    rail = _options()[0]
    responses = tmp_path / "responses"
    responses.mkdir()
    for index, (x_cm, y_cm) in enumerate(rail.stops):
        items = [_laptop_as_seen_from(x_cm, y_cm, _camera_z_cm(rail), target=target)
                 for target_index, target in enumerate(targets) if visible(target_index, index)]
        (responses / f"{rail.option_id}_{index}-0.json").write_text(
            json.dumps({"objects": items}), encoding="utf-8")
    main(["--mode", "judge", "--site", SITE, "--surface-z-cm", str(SURFACE_Z_CM),
          "--responses", str(responses), "--db", str(tmp_path / "s.sqlite3"),
          "--request", "count the computers", "--auto-approve"])
    return json.loads(capsys.readouterr().out)["outcome"]


def test_two_laptops_half_a_metre_apart_are_two_laptops(tmp_path, capsys):
    """Found by the WP5 session: both in every frame, 52 cm apart, and the merge
    fused all eight sightings into one object at their midpoint -- "1 computers".
    Two sightings of one class in one frame are two objects by definition."""
    outcome = _run_scene(tmp_path, capsys, [(250.0, 425.0), (200.0, 440.0)])
    assert outcome["counts"]["computer"] == {"confirmed": 2, "uncertain": 0}


def test_two_laptops_stay_two_even_when_no_frame_shows_both(tmp_path, capsys):
    """The same-frame rule cannot help when each laptop is seen from different stops.
    The across-sight tolerance (12% of range, not 25%) is what keeps them apart; with
    two stops each neither can be confirmed, and the report has to say two, not one."""
    outcome = _run_scene(tmp_path, capsys, [(250.0, 425.0), (190.0, 430.0)],
                         visible=lambda target_index, stop_index: (stop_index < 2) == (target_index == 0))
    counts = outcome["counts"]["computer"]
    assert counts["confirmed"] + counts["uncertain"] == 2


def test_a_first_sweep_is_only_offered_if_it_can_confirm_something():
    offered = [c.label for c in propose_candidates(_options()).candidates]
    assert "CLOSE_LOOK_CENTRE" not in offered            # two stops; three are needed
    assert all(len(o.stops) >= CONFIRM_MIN_STOPS for o in _options() if o.option_id in offered)


def test_the_offline_judge_takes_the_first_sweep_offered_not_the_alphabetical_one():
    criteria = {c.label: c.description for c in propose_candidates(_options()).candidates
                if c.label != "escalate"}
    assert _DefaultJudge().choice({}, "", criteria=criteria, name="x")["value"] == "RAIL_MID_HEIGHT"


def test_a_follow_up_never_offers_the_sweep_already_flown():
    results = {"flights_used": 1, "uncertain_objects": [], "flown_option_ids": ["RAIL_MID_HEIGHT"]}
    labels = [c.label for c in review_candidates(results, _options()).candidates]
    assert "follow_up:RAIL_MID_HEIGHT" not in labels
    assert "follow_up:CLOSE_LOOK_CENTRE" in labels


def test_a_second_sweeps_stop_is_a_new_viewpoint_not_the_first_sweeps_stop_again():
    """Two rail stops plus one close-look stop are three viewpoints. Keyed on
    the sweep-local stop index, the close look's stop 0 was the rail's stop 0."""
    def sighting(sid, sweep, stop, cam_x, cam_y, z_cm):
        item = _laptop_as_seen_from(cam_x, cam_y, z_cm)
        bbox = {"x": item["x"] * 960, "y": item["y"] * 720,
                "width": item["width"] * 960, "height": item["height"] * 720}
        return {"sighting_id": sid, "class": "computer", "band": "high", "bbox": bbox,
                "sweep_id": sweep, "stop_index": stop, "frame_x_cm": cam_x, "frame_y_cm": cam_y,
                "heading_deg": 0.0, "look_deg": 0.0, "delta_z_cm": z_cm - SURFACE_Z_CM}

    sightings = [sighting("a", "S1", 0, 175.0, 125.0, 120), sighting("b", "S1", 1, 225.0, 125.0, 120),
                 sighting("c", "S2", 0, 210.0, 275.0, 120)]
    merged = merge_sightings(sightings, camera=TELLO_MEASURED)
    assert len(merged) == 1
    assert merged[0]["distinct_stops"] == 3 and merged[0]["band"] == "CONFIRMED"


def test_code_states_what_a_report_left_unconfirmed():
    counts = [{"class": "computer", "confirmed": 0, "uncertain": 2, "method": "MERGED_MULTI_VIEW"}]
    assert unconfirmed_caveat("I found 0 computers.", counts) == \
        "Not confirmed: 2 more possible computers."
    assert unconfirmed_caveat("I found 0 computers confirmed and up to 2 computers.", counts) is None
    assert unconfirmed_caveat("I found 0 computers.", [{**counts[0], "uncertain": 0}]) is None


def test_cube_angles_are_converted_to_the_surveys_convention():
    """Cube: 0 along +x, counter-clockwise, looks positive to the left.
    Survey: 0 along +y (the tables), clockwise, looks positive to the right."""
    from sentinel.survey.cube_bridge import survey_heading_deg
    from sentinel.survey.geometry import position_of

    assert survey_heading_deg(90.0) == 0.0          # facing the tables
    assert survey_heading_deg(0.0) == 90.0          # facing +x is a quarter turn clockwise
    assert survey_heading_deg(180.0) == -90.0

    # A laptop dead ahead of a drone that has turned 30 degrees to its LEFT (cube look +30,
    # survey look -30) is up and to the left of it on the room plan, not to the right.
    item = _laptop_as_seen_from(0.0, 0.0, 120, target=(0.0, 300.0))
    bbox = {"x": item["x"] * 960, "y": item["y"] * 720,
            "width": item["width"] * 960, "height": item["height"] * 720}
    sighting = {"bbox": bbox, "frame_x_cm": 250.0, "frame_y_cm": 125.0,
                "heading_deg": survey_heading_deg(90.0), "look_deg": -30.0}
    x_cm, y_cm = position_of(sighting, 120 - SURFACE_Z_CM, TELLO_MEASURED)
    assert x_cm < 250.0 - 100.0 and y_cm > 125.0 + 200.0
