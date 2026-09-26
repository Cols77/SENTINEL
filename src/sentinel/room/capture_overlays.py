"""VLM overlays baked onto copies of a run's saved captures.

The console's Captures tab draws the same boxes live, but only for the run the
UI currently holds; this writes them into ``captures_overlay/`` beside the
untouched originals so any past run can be looked at or shared.

Boxes come only from a verified perception result -- the same predicate as
``sentinel.ui.room_camera.capture_record`` -- so an overlay never shows a
detection the session did not accept. The original frames are evidence
(their hashes are recorded) and are never modified.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3

OVERLAY_DIR = "captures_overlay"

_VERIFIED = ("r.status = 'SUCCEEDED' AND r.identity_status = 'VERIFIED' "
             "AND r.authorizes_current_state = 1")

#: Per-class colours: a frame often holds a person, a phone and a mug at once.
_CLASS_COLOURS = {
    "mug": (242, 186, 85),
    "phone": (71, 215, 200),
    "computer": (140, 170, 255),
    "person": (235, 110, 150),
}
_DEFAULT_COLOUR = (242, 186, 85)


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default font
        return ImageFont.load_default()


def _dashed_rectangle(draw, box, colour, width: int, dash: int = 12, gap: int = 7) -> None:
    x0, y0, x1, y1 = box
    for start, end in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                       ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        length = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
        if not length:
            continue
        dx, dy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        position = 0.0
        while position < length:
            stop = min(position + dash, length)
            draw.line((start[0] + dx * position, start[1] + dy * position,
                       start[0] + dx * stop, start[1] + dy * stop), fill=colour, width=width)
            position = stop + gap


def _frame_path(run_dir: Path, recorded: str) -> Path | None:
    """The capture inside this run directory: a run that was moved or archived
    keeps its frames under ``captures/`` even though the recorded path is the
    machine it flew on."""
    by_name = run_dir / "captures" / Path(recorded).name
    if by_name.is_file():
        return by_name
    path = Path(recorded)
    try:
        inside = path.resolve().is_relative_to(run_dir.resolve())
    except OSError:
        return None
    return path if inside and path.is_file() else None


def _pose_name(action_id: str | None) -> str:
    return (action_id or "").rsplit(":", 1)[-1] or "unknown pose"


def render_overlay(frame: Path, destination: Path, detections: list[dict], *, caption: str,
                   frame_size: tuple[int, int] | None = None) -> Path:
    from PIL import Image, ImageDraw

    with Image.open(frame) as source:
        image = source.convert("RGB")
    width, height = image.size
    scale_x = width / frame_size[0] if frame_size and frame_size[0] else 1.0
    scale_y = height / frame_size[1] if frame_size and frame_size[1] else 1.0
    draw = ImageDraw.Draw(image, "RGBA")
    label_font, caption_font = _font(18), _font(16)
    for item in detections:
        x, y, w, h = item["bbox"]
        box = (x * scale_x, y * scale_y, (x + w) * scale_x, (y + h) * scale_y)
        colour = _CLASS_COLOURS.get(item["label"], _DEFAULT_COLOUR)
        draw.rectangle(box, fill=(*colour, 26))
        if item["confidence"] == "low":
            _dashed_rectangle(draw, box, colour, 3)
        else:
            draw.rectangle(box, outline=colour, width=3)
        label = f"{item['label']} · {item['confidence']}"
        left, top, right, bottom = draw.textbbox((0, 0), label, font=label_font)
        text_w, text_h = right - left, bottom - top
        tag_y = box[1] - text_h - 8 if box[1] - text_h - 8 >= 0 else box[1]
        tag_x = min(max(box[0], 0), max(width - text_w - 8, 0))
        draw.rectangle((tag_x, tag_y, tag_x + text_w + 8, tag_y + text_h + 8), fill=(*colour, 230))
        draw.text((tag_x + 4, tag_y + 4 - top), label, fill=(16, 20, 24), font=label_font)
    left, top, right, bottom = draw.textbbox((0, 0), caption, font=caption_font)
    strip = bottom - top + 12
    draw.rectangle((0, height - strip, width, height), fill=(10, 14, 18, 190))
    draw.text((8, height - strip + 6 - top), caption, fill=(235, 240, 245), font=caption_font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    return destination


def render_run_overlays(run_dir: str | Path) -> list[Path]:
    """Write one overlay per verified capture of the run; returns their paths.

    Frames without a verified result (a zone whose looks never reached the
    detector) get no overlay: there are no accepted boxes to draw."""
    run_dir = Path(run_dir)
    database = run_dir / "session.sqlite3"
    if not database.is_file():
        return []
    written = []
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as connection:
        observations = connection.execute(
            "SELECT o.session_id, o.observation_id, o.frame_path, o.captured_at, o.action_id, "
            "r.provider, r.resolved_model FROM room_observations o "
            "JOIN room_perception_results r ON r.session_id = o.session_id "
            f"AND r.observation_id = o.observation_id WHERE {_VERIFIED} "
            "ORDER BY o.captured_at, o.observation_id").fetchall()
        for session_id, observation_id, frame_path, captured_at, action_id, provider, model in observations:
            frame = _frame_path(run_dir, frame_path)
            if frame is None:
                continue
            rows = connection.execute(
                "SELECT class_name, confidence_band, full_frame_bbox_json, frame_width_px, frame_height_px "
                "FROM room_detection_evidence WHERE session_id = ? AND observation_id = ? "
                "ORDER BY detection_id", (session_id, observation_id)).fetchall()
            detections = [dict(label=name, confidence=band, bbox=json.loads(bbox))
                          for name, band, bbox, _, _ in rows]
            frame_size = (rows[0][3], rows[0][4]) if rows else None
            try:
                when = datetime.fromisoformat(captured_at).strftime("%H:%M:%S UTC")
            except ValueError:
                when = captured_at
            found = ", ".join(f"{d['label']} ({d['confidence']})" for d in detections) or "nothing detected"
            caption = f"{_pose_name(action_id)} · {when} · {model or provider or 'VLM'} · {found}"
            written.append(render_overlay(frame, run_dir / OVERLAY_DIR / frame.name, detections,
                                          caption=caption, frame_size=frame_size))
    return written
