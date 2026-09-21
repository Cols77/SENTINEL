from datetime import datetime, timezone

from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.pose.localise import LocalisationFix, LocalisationUnreliable, localise
from sentinel.pose.scoring import circular_median_deg


def _report(x, y, heading):
    return DronePoseReport(
        report_id="p", run_id="r", observed_at=datetime.now(timezone.utc),
        source_kind="SIMULATED", point=(x, y), heading_deg=heading, altitude_m=None,
        method="APRILTAG_36H11_PNP",
        tag_observations=(TagObservation(0, "STAND_0", 1.0, 0.5),), reprojection_error_px=0.5)


def test_circular_median_is_wrap_safe():
    assert abs(abs(circular_median_deg([179.0, -179.0, 178.5, -178.5])) - 180.0) <= 0.5
    assert circular_median_deg([10.0, 12.0, 11.0]) == 11.0


def test_fix_is_the_componentwise_median_with_spread():
    fix = localise([_report(15.0 + 0.01 * i, 12.0, 90.0) for i in range(9)])
    assert isinstance(fix, LocalisationFix)
    assert fix.n == 9 and abs(fix.x_units - 15.04) < 1e-9 and fix.y_units == 12.0
    assert fix.heading_deg == 90.0 and fix.spread_p95_cm < 1.0


def test_one_outlier_does_not_move_the_fix():
    reports = [_report(15.0, 12.0, 90.0) for _ in range(19)] + [_report(40.0, 12.0, 90.0)]
    fix = localise(reports)
    assert isinstance(fix, LocalisationFix) and fix.x_units == 15.0


def test_too_few_samples_is_unreliable():
    result = localise([_report(15.0, 12.0, 90.0)] * 7)
    assert isinstance(result, LocalisationUnreliable) and "samples" in result.reason


def test_scattered_burst_is_unreliable():
    result = localise([_report(15.0 + i, 12.0, 90.0) for i in range(10)])  # 10 cm steps
    assert isinstance(result, LocalisationUnreliable) and "spread" in result.reason


def test_hover_wobble_up_to_ten_cm_still_localises():
    """2026-09-21 trials: a hovering Tello's bursts spread 5-10 cm around a good median;
    the old 5 cm gate (set from motors-off data) threw 3 of 12 stops away."""
    wobble = [_report(15.0 + 0.7 * ((i % 3) - 1), 12.0, 90.0) for i in range(12)]   # +-7 cm
    assert isinstance(localise(wobble), LocalisationFix)
