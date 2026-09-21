"""Annotated video output."""

from __future__ import annotations

import cv2
import numpy as np

PALETTE = [(66, 135, 245), (72, 201, 120), (245, 176, 66), (219, 84, 97),
           (166, 99, 232), (66, 214, 224), (232, 128, 66), (140, 180, 90)]

GREEN, GREY, AMBER, WHITE = (72, 201, 120), (120, 120, 130), (245, 176, 66), (255, 255, 255)


def colour(label: str):
    return PALETTE[hash(label) % len(PALETTE)]


def draw(frame, boxes, sector, phase, frame_idx, t_s, flags=None, selected=False):
    """One annotated frame.

    `boxes` entries are (label, score, box, bearing, observed). `observed`
    False means the box is held from an earlier detection because this frame
    was skipped by the stride, drawn dashed so the video never implies a
    detection that did not happen on that frame.
    """
    out = frame.copy()
    h, w = out.shape[:2]

    for label, score, box, bearing, observed in boxes:
        x1, y1, x2, y2 = (int(v) for v in box)
        col = colour(label)
        if observed:
            cv2.rectangle(out, (x1, y1), (x2, y2), col, 2)
        else:
            for x in range(x1, x2, 14):                 # dashed = interpolated
                cv2.line(out, (x, y1), (min(x + 7, x2), y1), col, 1)
                cv2.line(out, (x, y2), (min(x + 7, x2), y2), col, 1)
            for y in range(y1, y2, 14):
                cv2.line(out, (x1, y), (x1, min(y + 7, y2)), col, 1)
                cv2.line(out, (x2, y), (x2, min(y + 7, y2)), col, 1)

        tag = f"{label} {score:.2f}"
        if bearing is not None:
            tag += f" @{bearing:.0f}"
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(out, (x1, max(0, y1 - th - 8)), (x1 + tw + 6, y1), col, -1)
        cv2.putText(out, tag, (x1 + 3, max(th, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)

    cv2.rectangle(out, (0, 0), (w, 34), (24, 24, 28), -1)
    head = f"t={t_s:5.2f}s  f={frame_idx:4d}  {phase}"
    if sector is not None:
        head += f"  sector {sector}"
    cv2.putText(out, head, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                GREEN if phase == "DWELL" else GREY, 1, cv2.LINE_AA)

    if selected:
        cv2.rectangle(out, (2, 36), (w - 3, h - 3), GREEN, 3)
        cv2.putText(out, "KEYFRAME", (10, 60), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, GREEN, 2, cv2.LINE_AA)
    if flags:
        cv2.putText(out, " ".join(flags), (8, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, AMBER, 1, cv2.LINE_AA)

    _compass(out, sector)
    return out


def _compass(img, sector):
    h, w = img.shape[:2]
    cx, cy, r = w - 52, 74, 30
    cv2.circle(img, (cx, cy), r, (200, 200, 205), 1)
    for s in range(4):
        a = np.deg2rad(s * 90 - 90)
        cv2.circle(img, (int(cx + r * np.cos(a)), int(cy + r * np.sin(a))),
                   3, (140, 140, 145), -1)
    if sector is not None:
        a = np.deg2rad((sector % 4) * 90 - 90)
        cv2.line(img, (cx, cy),
                 (int(cx + (r - 4) * np.cos(a)), int(cy + (r - 4) * np.sin(a))), GREEN, 2)
    cv2.circle(img, (cx, cy), 3, WHITE, -1)
    cv2.putText(img, "0", (cx - 4, cy - r - 5), cv2.FONT_HERSHEY_SIMPLEX,
                0.4, (200, 200, 205), 1, cv2.LINE_AA)