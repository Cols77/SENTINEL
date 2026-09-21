# tests/pose/test_containment.py
"""SR-063 AC-2: advisory containment; it classifies, it never moves anything."""
import inspect

import pytest

from sentinel.pose import containment
from sentinel.pose.containment import Containment, CubeFootprint, check_containment

CUBE = CubeFootprint(30.0, 510.0, 200.0, 420.0)


@pytest.mark.parametrize("x, y, expected", [
    (270.0, 300.0, Containment.INSIDE),
    (60.0, 300.0, Containment.INSIDE),       # exactly on the margin line counts as inside
    (59.9, 300.0, Containment.MARGIN),
    (270.0, 400.0, Containment.MARGIN),
    (30.0, 200.0, Containment.MARGIN),       # on the boundary is still in the cube
    (29.9, 300.0, Containment.OUTSIDE),
    (270.0, 420.1, Containment.OUTSIDE),
])
def test_classification(x, y, expected):
    assert check_containment(x, y, CUBE) is expected


def test_custom_margin():
    assert check_containment(45.0, 300.0, CUBE, margin_cm=10.0) is Containment.INSIDE


def test_module_cannot_command_motion():
    source = inspect.getsource(containment)
    for forbidden in ("client", ".move(", ".rotate(", "takeoff", "sentinel.runner"):
        assert forbidden not in source
