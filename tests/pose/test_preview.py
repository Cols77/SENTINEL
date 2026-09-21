import cv2
import numpy as np

from sentinel.pose.preview import Preview


def _board(cols=8, rows=7, sq=60):
    img = np.full((720, 960), 255, np.uint8)
    x0, y0 = (960 - cols * sq) // 2, (720 - rows * sq) // 2
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                img[y0 + r * sq:y0 + (r + 1) * sq, x0 + c * sq:x0 + (c + 1) * sq] = 0
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def test_board_found_is_counted_and_frame_is_annotated_copy():
    p = Preview(board_pattern=(7, 6), show=False)
    frame = _board()
    before = frame.copy()
    out = p.annotate(frame)
    assert p.frames == 1 and p.board_frames == 1 and p.board_visible is True
    assert out.shape == frame.shape and not np.array_equal(out, frame)
    assert np.array_equal(frame, before)  # raw frame untouched (recorded frames stay clean)


def test_missing_board_is_reported():
    p = Preview(board_pattern=(7, 6), show=False)
    p.annotate(np.full((720, 960, 3), 128, np.uint8))
    assert p.board_visible is False and p.board_frames == 0 and p.frames == 1


def test_coverage_grid_tracks_where_the_board_has_been_seen():
    p = Preview(board_pattern=(7, 6), show=False)
    p.annotate(_board())
    assert sum(1 for row in p.coverage for n in row if n) == 1
    p.annotate(_board())
    assert sum(1 for row in p.coverage for n in row if n) == 1  # same cell
    assert sum(map(sum, p.coverage)) == 2


def test_call_returns_true_when_not_shown():
    assert Preview(board_pattern=(7, 6), show=False)(_board()) is True
