"""Calibrate the drone camera from checkerboard images and write a tag-map v1 file.

Usage:
    uv run python scripts/calibrate_camera.py IMAGE_DIR --tag-map IN.json --out OUT.json \
        --square-m 0.025 [--pattern 7x6] [--max-rms 0.5]

--pattern is the number of INNER corners (columns x rows). The input tag map is
never modified; K and dist_coeffs are copied into a new file with
calibration_source "chessboard". A calibration whose RMS reprojection error
exceeds --max-rms (pixels) is rejected and nothing is written.
"""

import argparse
import copy
import json
import pathlib
import sys

import cv2
import numpy as np

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}
MIN_IMAGES = 5
DEFAULT_MAX_RMS_PX = 0.5
DEFAULT_MIN_TILT_DEG = 25.0        # steepest board tilt needed to pin down the focal length
DEFAULT_MAX_HALF_DISAGREEMENT = 0.05  # two random halves must agree on fx within 5%


class CalibrationError(Exception):
    pass


def calibrate(image_dir, pattern, square_m, *, min_tilt_deg=DEFAULT_MIN_TILT_DEG,
              max_half_disagreement=DEFAULT_MAX_HALF_DISAGREEMENT):
    """Return (K, dist, rms, (width, height)). Raises CalibrationError if unusable."""
    image_dir = pathlib.Path(image_dir)
    paths = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS) if image_dir.is_dir() else []
    objp = np.zeros((pattern[0] * pattern[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:pattern[0], 0:pattern[1]].T.reshape(-1, 2) * square_m
    obj_pts, img_pts, size = [], [], None
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    for p in paths:
        gray = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        this_size = (gray.shape[1], gray.shape[0])
        if size is not None and this_size != size:
            raise CalibrationError(f"mixed image sizes: {p.name} is {this_size}, expected {size}")
        size = this_size
        found, corners = cv2.findChessboardCorners(
            gray, pattern,
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_FAST_CHECK)
        if not found:
            continue
        corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), crit)
        obj_pts.append(objp)
        img_pts.append(corners)
    if len(obj_pts) < MIN_IMAGES:
        raise CalibrationError(
            f"only {len(obj_pts)} usable checkerboard image(s) of {len(paths)}; need >= {MIN_IMAGES}")
    rms, K, dist, rvecs, _ = cv2.calibrateCamera(obj_pts, img_pts, size, None, None)
    tilts = [float(np.degrees(np.arccos(min(1.0, abs(cv2.Rodrigues(r)[0][2, 2]))))) for r in rvecs]
    if max(tilts) < min_tilt_deg:
        raise CalibrationError(
            f"board tilt too small (max {max(tilts):.0f} deg, need >= {min_tilt_deg:.0f}): "
            "the focal length is not determined by near-frontal views. Re-record with the "
            "board tilted 30-45 deg both ways.")
    order = np.random.RandomState(0).permutation(len(obj_pts))
    half = len(order) // 2
    fxs = []
    for part in (order[:half], order[half:]):
        _, Kh, _, _, _ = cv2.calibrateCamera(
            [obj_pts[k] for k in part], [img_pts[k] for k in part], size, None, None)
        fxs.append(Kh[0, 0])
    disagreement = abs(fxs[0] - fxs[1]) / ((fxs[0] + fxs[1]) / 2)
    if disagreement > max_half_disagreement:
        raise CalibrationError(
            f"unstable calibration: two halves of the frames disagree on focal length "
            f"({fxs[0]:.0f} vs {fxs[1]:.0f} px, {disagreement:.0%} > {max_half_disagreement:.0%}). "
            "Record more varied views (tilt, distance, whole image area).")
    xs = np.concatenate([p.reshape(-1, 2) for p in img_pts])
    print(f"views: {len(obj_pts)}, max tilt {max(tilts):.0f} deg, corners span "
          f"x {xs[:, 0].min():.0f}-{xs[:, 0].max():.0f} of {size[0]}, "
          f"y {xs[:, 1].min():.0f}-{xs[:, 1].max():.0f} of {size[1]}, "
          f"half-split fx agreement {disagreement:.1%}")
    return K, dist.reshape(-1), float(rms), size


def build_calibrated_doc(doc, K, dist, size):
    out = copy.deepcopy(doc)
    out["camera_calibration"] = {
        "width": int(size[0]), "height": int(size[1]),
        "fx": float(K[0, 0]), "fy": float(K[1, 1]),
        "cx": float(K[0, 2]), "cy": float(K[1, 2]),
        "dist_coeffs": [float(x) for x in dist],
        "calibration_source": "chessboard",
    }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image_dir")
    ap.add_argument("--tag-map", required=True, help="input tag-map v1 JSON (read only)")
    ap.add_argument("--out", required=True, help="new output tag-map path")
    ap.add_argument("--pattern", default="7x6", help="inner corners COLSxROWS")
    ap.add_argument("--min-tilt-deg", type=float, default=DEFAULT_MIN_TILT_DEG,
                    help="reject if the steepest board tilt is below this (degrees)")
    ap.add_argument("--square-m", type=float, required=True,
                    help="measured side of one printed square, in metres (sets the scale)")
    ap.add_argument("--max-rms", type=float, default=DEFAULT_MAX_RMS_PX,
                    help="reject the calibration if RMS reprojection error (px) exceeds this")
    args = ap.parse_args(argv)

    src, dst = pathlib.Path(args.tag_map), pathlib.Path(args.out)
    if src.resolve() == dst.resolve():
        ap.error("--out must differ from --tag-map (input is never overwritten)")
    try:
        cols, rows = (int(v) for v in args.pattern.lower().split("x"))
    except ValueError:
        ap.error("--pattern must look like 7x6")
    doc = json.loads(src.read_text(encoding="utf-8"))
    if doc.get("schema_version") != "tag-map-v1":
        print(f"unsupported tag map: {doc.get('schema_version')!r}", file=sys.stderr)
        return 2
    try:
        K, dist, rms, size = calibrate(args.image_dir, (cols, rows), args.square_m,
                                   min_tilt_deg=args.min_tilt_deg)
    except CalibrationError as e:
        print(f"calibration failed: {e}", file=sys.stderr)
        return 1
    if rms > args.max_rms:
        print(f"calibration rejected: rms reprojection error {rms:.3f} px exceeds "
              f"--max-rms {args.max_rms} px; nothing written. Retake sharper, more varied "
              "board images.", file=sys.stderr)
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(build_calibrated_doc(doc, K, dist, size), indent=2), encoding="utf-8")
    print(f"wrote {dst} (rms reprojection error {rms:.3f} px, {size[0]}x{size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
