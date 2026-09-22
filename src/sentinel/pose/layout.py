"""Design-time prediction of pose accuracy for a tag layout (Monte Carlo).

For a camera facing +y, pitched like the Tello, it projects every tag that is
fully in frame, adds corner noise, re-solves the joint PnP, and reports the p95
horizontal error scaled by a simulation-to-reality factor. It predicts; the
static stand test measures.
"""
import math

import cv2
import numpy as np

from sentinel.pose.detector import _room_corners
from sentinel.pose.tagmap import TagMap

CAMERA_PITCH_DOWN_DEG = 8.6
FRAME_W, FRAME_H, BORDER_PX = 960, 720, 15
MAX_OBLIQUITY_DEG = 65.0


def _camera(pos, yaw_err_deg: float = 0.0, pitch_err_deg: float = 0.0):
    """(R_cam_room, tvec) for a camera at ``pos`` facing +y, pitched down
    (``pitch_err_deg`` > 0 looks further down, as a drone tilting forward does)."""
    a = math.radians(90.0 + yaw_err_deg)
    pitch = math.radians(CAMERA_PITCH_DOWN_DEG + pitch_err_deg)
    fwd = np.array([math.cos(a), math.sin(a), 0.0]) * math.cos(pitch) \
        + np.array([0.0, 0.0, -1.0]) * math.sin(pitch)
    right = np.cross(fwd, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    R = np.vstack([right, np.cross(fwd, right), fwd])
    return R, -R @ np.asarray(pos, dtype=float)


def _visible(tag_map: TagMap, pos, R, t):
    scale = tag_map.room_scale_m_per_unit
    out = []
    for raw in tag_map.doc["tags"]:
        tag = tag_map.tag(raw["tag_id"])
        corners = _room_corners(tag, scale)
        to_cam = np.asarray(pos, dtype=float) - corners.mean(axis=0)
        to_cam[2] = 0.0
        normal = np.array([math.cos(math.radians(tag.yaw_deg)), math.sin(math.radians(tag.yaw_deg)), 0.0])
        cosine = float(normal @ to_cam) / (np.linalg.norm(to_cam) or 1.0)
        if cosine <= math.cos(math.radians(MAX_OBLIQUITY_DEG)):
            continue
        if ((R @ corners.T).T[:, 2] + t[2] <= 0.05).any():
            continue
        px = cv2.projectPoints(corners, cv2.Rodrigues(R)[0], t, tag_map.K, tag_map.dist)[0].reshape(-1, 2)
        if ((px[:, 0] < BORDER_PX).any() or (px[:, 0] > FRAME_W - BORDER_PX).any()
                or (px[:, 1] < BORDER_PX).any() or (px[:, 1] > FRAME_H - BORDER_PX).any()):
            continue
        out.append((corners, px))
    return out


def predict_cell(tag_map: TagMap, x_m: float, y_m: float, alt_m: float, *, rng,
                 trials: int = 60, sigma_px: float = 0.35, reality: float = 2.5) -> dict:
    pos = np.array([x_m, y_m, alt_m], dtype=float)
    R, t = _camera(pos)
    seen = _visible(tag_map, pos, R, t)
    cell = {"x_m": x_m, "y_m": y_m, "alt_m": alt_m, "tags_in_frame": len(seen), "p95_cm": None}
    if not seen:
        return cell
    obj = np.vstack([c for c, _ in seen])
    clean = np.vstack([p for _, p in seen])
    errors = []
    for _ in range(trials):
        image = clean + rng.normal(0.0, sigma_px, clean.shape)
        R0, t0 = _camera(pos + rng.normal(0.0, 0.10, 3), rng.normal(0.0, 4.0))
        ok, rvec, tvec = cv2.solvePnP(
            obj, image, tag_map.K, tag_map.dist, rvec=cv2.Rodrigues(R0)[0],
            tvec=t0.reshape(3, 1), useExtrinsicGuess=True, flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            continue
        est = (-cv2.Rodrigues(rvec)[0].T @ tvec).flatten()
        errors.append(math.hypot(est[0] - pos[0], est[1] - pos[1]))
    if errors:
        cell["p95_cm"] = round(float(np.percentile(errors, 95)) * 100.0 * reality, 1)
    return cell


def predict_grid(tag_map: TagMap, xs_m, ys_m, alts_m, *, trials: int = 60, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    return [predict_cell(tag_map, x, y, alt, rng=rng, trials=trials)
            for alt in alts_m for y in ys_m for x in xs_m]
