"""Calibrate the drone camera from checkerboard images and write a tag-map v1 file.

Usage:
    uv run python scripts/calibrate_camera.py IMAGE_DIR --tag-map IN.json --out OUT.json \
        --square-m 0.025 [--pattern 7x6] [--max-rms 0.5]

--pattern is the number of INNER corners (columns x rows). The input tag map is
never modified; K and dist_coeffs are copied into a new file with
calibration_source "calibrated". A calibration whose RMS reprojection error
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


class CalibrationError(Exception):
    pass


def calibrate(image_dir, pattern, square_m):
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
    rms, K, dist, _, _ = cv2.calibrateCamera(obj_pts, img_pts, size, None, None)
    return K, dist.reshape(-1), float(rms), size


def build_calibrated_doc(doc, K, dist, size):
    out = copy.deepcopy(doc)
    out["camera_calibration"] = {
        "width": int(size[0]), "height": int(size[1]),
        "fx": float(K[0, 0]), "fy": float(K[1, 1]),
        "cx": float(K[0, 2]), "cy": float(K[1, 2]),
        "dist_coeffs": [float(x) for x in dist],
        "calibration_source": "calibrated",
    }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image_dir")
    ap.add_argument("--tag-map", required=True, help="input tag-map v1 JSON (read only)")
    ap.add_argument("--out", required=True, help="new output tag-map path")
    ap.add_argument("--pattern", default="7x6", help="inner corners COLSxROWS")
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
        K, dist, rms, size = calibrate(args.image_dir, (cols, rows), args.square_m)
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
