from sentinel.motion_bounds import (
    TransportLimits,
    move_reply_upper_bound_s,
    rotate_reply_upper_bound_s,
    set_speed_upper_bound_s,
    vertical_move_reply_upper_bound_s,
)


def test_motion_reply_bounds_match_the_real_backend_calibration():
    assert move_reply_upper_bound_s(distance_cm=120, speed_cm_s=20, base_timeout_s=7.0) == 18
    assert move_reply_upper_bound_s(distance_cm=10, speed_cm_s=20, base_timeout_s=7.0) == 7
    assert move_reply_upper_bound_s(distance_cm=20, speed_cm_s=20, base_timeout_s=7.0) == 8
    assert rotate_reply_upper_bound_s(degrees=180, base_timeout_s=7.0) == 12
    assert rotate_reply_upper_bound_s(degrees=30, base_timeout_s=7.0) == 7


def test_a_vertical_move_gets_at_least_the_slow_climb_floor():
    # Live 2026-09-25: the 14:10 flight's ``up 40`` (bound 10 s at speed 20)
    # exceeded its deadline and the whole flight landed for it.
    assert vertical_move_reply_upper_bound_s(distance_cm=40, speed_cm_s=20, base_timeout_s=7.0) == 15
    assert vertical_move_reply_upper_bound_s(distance_cm=30, speed_cm_s=50, base_timeout_s=7.0) == 15
    # A genuinely long climb keeps the calibrated bound when it is larger.
    assert vertical_move_reply_upper_bound_s(distance_cm=200, speed_cm_s=20, base_timeout_s=7.0) == 26
    # Horizontal moves are untouched by the floor.
    assert move_reply_upper_bound_s(distance_cm=40, speed_cm_s=20, base_timeout_s=7.0) == 10


def test_transport_limits_cover_configured_control_retries_and_read_queries():
    limits = TransportLimits(reply_timeout_s=30.0, control_attempts=3, read_timeout_s=30.0)
    assert move_reply_upper_bound_s(distance_cm=120, speed_cm_s=20,
                                    base_timeout_s=limits.reply_timeout_s) == 30
    assert set_speed_upper_bound_s(limits) == 90
    assert limits.read_timeout_s == 30.0


def test_transport_limits_keep_sdk_default_control_and_read_waits_separate_from_motion():
    limits = TransportLimits(
        reply_timeout_s=1.0, control_attempts=3,
        control_timeout_s=7.0, read_timeout_s=7.0,
    )

    assert move_reply_upper_bound_s(distance_cm=20, speed_cm_s=20,
                                    base_timeout_s=limits.reply_timeout_s) == 8
    assert set_speed_upper_bound_s(limits) == 21
    assert limits.read_timeout_s == 7.0
