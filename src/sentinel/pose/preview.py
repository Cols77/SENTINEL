"""Live preview of what the drone sees, with checkerboard/AprilTag overlays.

Purely a display aid for pose-stream: it never touches the drone. The overlay
is drawn on a copy, so recorded frames stay clean. A small coverage map shows
which parts of the image the checkerboard has been seen in, since a good lens
calibration needs the board in every region (edges and corners included).
"""
import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

WINDOW = "sentinel preview (q / Esc to stop)"
GRID_COLS, GRID_ROWS = 4, 3
_FLAGS = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_FAST_CHECK
GREEN, RED, GREY = (0, 200, 0), (0, 0, 220), (110, 110, 110)


class Preview:
    def __init__(self, board_pattern: tuple[int, int] | None = (7, 6), *, show: bool = True):
        self.board_pattern = board_pattern
        self.show = show
        self.frames = 0
        self.board_frames = 0
        self.board_visible = False
        self.coverage = [[0] * GRID_COLS for _ in range(GRID_ROWS)]
        self._detector = cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11),
            cv2.aruco.DetectorParameters(),
        )

    def annotate(self, bgr: np.ndarray) -> np.ndarray:
        """Return an annotated copy of ``bgr`` and update the counters."""
        self.frames += 1
        out = bgr.copy()
        h, w = out.shape[:2]
        corners, ids, _ = self._detector.detectMarkers(bgr)
        if ids is not None and len(ids):
            cv2.aruco.drawDetectedMarkers(out, corners, ids)
        self.board_visible = False
        if self.board_pattern is not None:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            found, pts = cv2.findChessboardCorners(gray, self.board_pattern, _FLAGS)
            if found:
                self.board_visible = True
                self.board_frames += 1
                cv2.drawChessboardCorners(out, self.board_pattern, pts, found)
                cx, cy = pts.reshape(-1, 2).mean(axis=0)
                col = min(GRID_COLS - 1, max(0, int(cx / w * GRID_COLS)))
                row = min(GRID_ROWS - 1, max(0, int(cy / h * GRID_ROWS)))
                self.coverage[row][col] += 1
            label = "BOARD OK" if self.board_visible else "BOARD NOT FOUND - reposition / show whole board"
            cv2.putText(out, f"{label}   seen in {self.board_frames}/{self.frames} frames",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        GREEN if self.board_visible else RED, 2)
            self._draw_coverage(out)
        return out

    def _draw_coverage(self, out: np.ndarray) -> None:
        h, w = out.shape[:2]
        cw, ch = 36, 27
        x0, y0 = w - GRID_COLS * cw - 10, 10
        for r in range(GRID_ROWS):
            for c in range(GRID_COLS):
                p1 = (x0 + c * cw, y0 + r * ch)
                p2 = (p1[0] + cw - 2, p1[1] + ch - 2)
                if self.coverage[r][c]:
                    cv2.rectangle(out, p1, p2, GREEN, -1)
                cv2.rectangle(out, p1, p2, GREY, 1)
        cv2.putText(out, "coverage", (x0, y0 + GRID_ROWS * ch + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREY, 1)

    def __call__(self, bgr: np.ndarray) -> bool:
        """Annotate and display; return False when the user asks to stop."""
        out = self.annotate(bgr)
        if not self.show:
            return True
        cv2.imshow(WINDOW, out)
        key = cv2.waitKey(1) & 0xFF
        return key not in (27, ord("q"))

    def close(self) -> None:
        if self.show:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass
