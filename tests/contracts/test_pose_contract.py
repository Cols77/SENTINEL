import pytest
from datetime import datetime, timezone
from sentinel.contracts.pose import DronePoseReport, LocalPoint, validate_drone_pose_report


def _report(**over):
    base = dict(
        report_id="3f6b0a1e-7c2d-4e8a-9b1f-0a2c3d4e5f60",
        run_id="4f6b0a1e-7c2d-4e8a-9b1f-0a2c3d4e5f61",
        observed_at=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
        source_kind="LIVE",
        point=(12.5, 40.0),
        heading_deg=91.0,
        altitude_m=1.2,
        method="APRILTAG_36H11_PNP",
        tag_observations=[{"tag_id": 0, "zone_id": "LEFT_ALLEY_FRONT",
                           "distance_m": 2.0, "reprojection_error_px": 0.8}],
        reprojection_error_px=0.8,
    )
    base.update(over)
    return DronePoseReport.from_dict(base)


def test_a_valid_report_validates():
    r = _report()
    validate_drone_pose_report(r)  # must not raise


def test_point_outside_the_room_fails_closed():
    with pytest.raises(ValueError):
        _report(point=(-1.0, 40.0))


def test_simulated_source_is_rejected_on_a_judged_path():
    with pytest.raises(ValueError):
        validate_drone_pose_report(_report(source_kind="SIMULATED"), judged_path=True)


def test_high_reprojection_error_fails_closed():
    with pytest.raises(ValueError):
        _report(reprojection_error_px=5.0,
                tag_observations=[{"tag_id": 0, "zone_id": "LEFT_ALLEY_FRONT",
                                   "distance_m": 2.0, "reprojection_error_px": 5.0}])


def test_no_tag_observations_is_invalid():
    with pytest.raises(ValueError):
        _report(tag_observations=[])


def test_naive_observed_at_is_rejected():
    with pytest.raises(ValueError):
        _report(observed_at=datetime(2026, 9, 18, 10, 0))


def test_negative_altitude_fails_closed():
    # wire schema: altitude_m minimum 0
    with pytest.raises(ValueError):
        _report(altitude_m=-0.5)
    _report(altitude_m=0.0)
    _report(altitude_m=None)
