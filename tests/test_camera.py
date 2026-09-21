"""sentinel.camera holds the camera model; survey.observability re-exports it.

sentinel.cube must not import sentinel.survey (the dependency runs the
other way by design), which is the whole reason camera.py exists.
"""
import sentinel.camera as camera
import sentinel.survey.observability as observability


def test_the_camera_module_is_importable_standalone():
    assert camera.CameraModel is not None
    assert camera.TELLO_MEASURED.name == "tello-measured"
    assert camera.SYNTHETIC_LEVEL.name == "synthetic-level"
    assert set(camera.CAMERAS) == {"synthetic-level", "tello-measured"}


def test_observability_reexports_the_same_objects_not_copies():
    assert observability.CameraModel is camera.CameraModel
    assert observability.SYNTHETIC_LEVEL is camera.SYNTHETIC_LEVEL
    assert observability.TELLO_MEASURED is camera.TELLO_MEASURED
    assert observability.CAMERAS is camera.CAMERAS
