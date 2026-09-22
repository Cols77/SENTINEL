from sentinel.motion_bounds import (
    TransportLimits,
    move_reply_upper_bound_s,
    rotate_reply_upper_bound_s,
    set_speed_upper_bound_s,
)


def test_motion_reply_bounds_match_the_real_backend_calibration():
    assert move_reply_upper_bound_s(distance_cm=120, speed_cm_s=20, base_timeout_s=7.0) == 18
    assert move_reply_upper_bound_s(distance_cm=10, speed_cm_s=20, base_timeout_s=7.0) == 7
    assert move_reply_upper_bound_s(distance_cm=20, speed_cm_s=20, base_timeout_s=7.0) == 8
    assert rotate_reply_upper_bound_s(degrees=180, base_timeout_s=7.0) == 12
    assert rotate_reply_upper_bound_s(degrees=30, base_timeout_s=7.0) == 7


def test_transport_limits_cover_configured_control_retries_and_read_queries():
    limits = TransportLimits(reply_timeout_s=30.0, control_attempts=3, read_timeout_s=30.0)
    assert move_reply_upper_bound_s(distance_cm=120, speed_cm_s=20,
                                    base_timeout_s=limits.reply_timeout_s) == 30
    assert set_speed_upper_bound_s(limits) == 90
    assert limits.read_timeout_s == 30.0
