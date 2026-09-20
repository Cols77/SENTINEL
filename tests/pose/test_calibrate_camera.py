import importlib.util
import json
import pathlib

import cv2
import numpy as np
import pytest

from sentinel.pose.tagmap import TagMap, load_tag_map

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "docs/superpowers/specs/contracts/fixtures/tag-map-v1.valid.json"

_spec = importlib.util.spec_from_file_location("calibrate_camera", ROOT / "scripts/calibrate_camera.py")
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)

W, H = 960, 720
K_TRUE = np.array([[600.0, 0, 480.0], [0, 600.0, 360.0], [0, 0, 1]])
PATTERN = (7, 5)  # inner corners
SQUARE_M = 0.03


def _render(rvec, tvec):
    """Render a checkerboard (8x6 squares) seen from a pinhole camera K_TRUE."""
    sq = 40
    board = np.full(((PATTERN[1] + 3) * sq, (PATTERN[0] + 3) * sq), 255, np.uint8)
    for r in range(PATTERN[1] + 1):
        for c in range(PATTERN[0] + 1):
            if (r + c) % 2 == 0:
                y0, x0 = (r + 1) * sq, (c + 1) * sq
                board[y0:y0 + sq, x0:x0 + sq] = 0
    # board pixel (u,v) -> metric plane point, origin at first inner corner
    s = SQUARE_M / sq
    R, _ = cv2.Rodrigues(np.array(rvec, float))
    t = np.array(tvec, float).reshape(3, 1)
    # inner corner (0,0) is at board pixel (2*sq, 2*sq)
    A = np.array([[s, 0, -2 * sq * s], [0, s, -2 * sq * s], [0, 0, 1]])
    Hm = K_TRUE @ np.column_stack([R[:, 0], R[:, 1], t[:, 0]]) @ A
    return cv2.warpPerspective(board, Hm, (W, H), borderValue=255)


POSES = [
    ((0.0, 0.0, 0.0), (-0.09, -0.06, 0.6)),
    ((0.35, 0.1, 0.05), (-0.09, -0.05, 0.55)),
    ((-0.3, 0.2, -0.1), (-0.10, -0.06, 0.6)),
    ((0.2, -0.35, 0.15), (-0.08, -0.06, 0.55)),
    ((-0.25, -0.25, 0.3), (-0.09, -0.05, 0.6)),
    ((0.4, 0.3, -0.2), (-0.10, -0.05, 0.6)),
    ((0.0, 0.4, 0.4), (-0.09, -0.06, 0.55)),
    ((-0.4, 0.0, -0.3), (-0.09, -0.05, 0.6)),
]


@pytest.fixture
def board_dir(tmp_path):
    d = tmp_path / "imgs"
    d.mkdir()
    for i, (r, t) in enumerate(POSES):
        cv2.imwrite(str(d / f"cb_{i:02d}.png"), _render(r, t))
    return d


def test_calibrate_recovers_intrinsics(board_dir):
    K, dist, rms, size = cal.calibrate(board_dir, PATTERN, SQUARE_M)
    assert size == (W, H)
    assert K.shape == (3, 3)
    assert K[0, 0] == pytest.approx(600.0, rel=0.03)
    assert K[1, 1] == pytest.approx(600.0, rel=0.03)
    assert K[0, 2] == pytest.approx(480.0, abs=10)
    assert K[1, 2] == pytest.approx(360.0, abs=10)
    assert rms < 1.0


def test_too_few_usable_images_fails_closed(tmp_path):
    d = tmp_path / "few"
    d.mkdir()
    cv2.imwrite(str(d / "a.png"), _render(*POSES[0]))
    with pytest.raises(cal.CalibrationError):
        cal.calibrate(d, PATTERN, SQUARE_M)


def test_blank_images_fail_closed(tmp_path):
    d = tmp_path / "blank"
    d.mkdir()
    for i in range(5):
        cv2.imwrite(str(d / f"b{i}.png"), np.full((H, W), 200, np.uint8))
    with pytest.raises(cal.CalibrationError):
        cal.calibrate(d, PATTERN, SQUARE_M)


def test_main_writes_calibrated_tag_map_and_leaves_fixture_alone(board_dir, tmp_path):
    before = FIXTURE.read_bytes()
    out = tmp_path / "out" / "tag-map.calibrated.json"
    rc = cal.main([str(board_dir), "--tag-map", str(FIXTURE), "--out", str(out),
                   "--pattern", "7x5", "--square-m", str(SQUARE_M)])
    assert rc == 0
    assert FIXTURE.read_bytes() == before
    m = load_tag_map(str(out))
    assert m.calibration_source == "calibrated"
    assert m.K[0, 0] == pytest.approx(600.0, rel=0.03)
    doc = json.loads(out.read_text())
    assert doc["camera_calibration"]["width"] == W and doc["camera_calibration"]["height"] == H
    assert len(doc["camera_calibration"]["dist_coeffs"]) >= 4
    assert doc["tags"] == json.loads(FIXTURE.read_text())["tags"]


def test_main_refuses_to_overwrite_input(board_dir, tmp_path):
    with pytest.raises(SystemExit):
        cal.main([str(board_dir), "--tag-map", str(FIXTURE), "--out", str(FIXTURE)])


def test_main_returns_nonzero_when_calibration_fails(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    out = tmp_path / "o.json"
    assert cal.main([str(d), "--tag-map", str(FIXTURE), "--out", str(out), "--square-m", "0.024"]) != 0
    assert not out.exists()


def test_tagmap_calibration_source_defaults_to_nominal():
    doc = json.loads(FIXTURE.read_text())
    assert TagMap(doc).calibration_source == "nominal"
    del doc["camera_calibration"]["calibration_source"]
    assert TagMap(doc).calibration_source == "nominal"
    doc["camera_calibration"]["calibration_source"] = "calibrated"
    assert TagMap(doc).calibration_source == "calibrated"


def test_main_rejects_calibration_above_max_rms_and_writes_nothing(board_dir, tmp_path, capsys):
    out = tmp_path / "out" / "tag-map.calibrated.json"
    rc = cal.main([str(board_dir), "--tag-map", str(FIXTURE), "--out", str(out),
                   "--pattern", "7x5", "--square-m", str(SQUARE_M), "--max-rms", "1e-9"])
    assert rc == 1
    assert not out.exists()
    assert "rms" in capsys.readouterr().err.lower()


def test_main_default_max_rms_is_half_pixel():
    assert cal.DEFAULT_MAX_RMS_PX == 0.5


def test_square_size_is_required(board_dir, tmp_path):
    # a silently wrong default square size would scale every distance
    with pytest.raises(SystemExit):
        cal.main([str(board_dir), "--tag-map", str(FIXTURE), "--out", str(tmp_path / "o.json")])
