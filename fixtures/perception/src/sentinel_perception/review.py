"""Visual review of an arbitrary room recording.

This deliberately makes no yaw-scan, position, unique-object or count claim.
The detector runs on sampled frames and boxes appear only on those frames.
"""
from __future__ import annotations

import json
import math
import argparse
from fractions import Fraction
from pathlib import Path
from typing import Callable

import cv2
import av


def review_video(source: str | Path, outdir: str | Path,
                 detect: Callable[[object, int], list[dict]], *, stride: int = 5,
                 model: str) -> dict:
    if stride < 1:
        raise ValueError("stride must be positive")
    source, outdir = Path(source), Path(outdir)
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise ValueError(f"cannot decode recording: {source}")
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    raw_stream = source.suffix.lower() in {".h264", ".264"}
    if raw_stream or not math.isfinite(fps) or fps <= 0:
        fps = 30.0  # raw Tello H.264 has no container timestamps
        timestamp_basis = "estimated_30fps"
    else:
        timestamp_basis = "container_fps"
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width <= 0 or height <= 0:
        cap.release()
        raise ValueError("recording has no decodable image dimensions")
    outdir.mkdir(parents=True, exist_ok=True)
    video_path = outdir / "annotated.mp4"
    container = av.open(str(video_path), mode="w")
    try:
        stream = container.add_stream("libx264", rate=Fraction(fps).limit_denominator(1000))
        stream.width, stream.height = width, height
        stream.pix_fmt = "yuv420p"
    except Exception:
        cap.release()
        container.close()
        raise
    detections: list[dict] = []
    index = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % stride == 0:
                for item in detect(frame, index):
                    x1, y1, x2, y2 = [round(float(v)) for v in item["box_xyxy"]]
                    row = {"frame": index, "time_s": round(index / fps, 3),
                           "label": str(item["label"]), "score": float(item["score"]),
                           "box_xyxy": [x1, y1, x2, y2]}
                    detections.append(row)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 255), 2)
                    cv2.putText(frame, f"{row['label']} {row['score']:.2f}",
                                (x1, max(12, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                                0.45, (0, 220, 255), 1, cv2.LINE_AA)
            for packet in stream.encode(av.VideoFrame.from_ndarray(frame, format="bgr24")):
                container.mux(packet)
            index += 1
    finally:
        cap.release()
        for packet in stream.encode():
            container.mux(packet)
        container.close()
    if index == 0:
        video_path.unlink(missing_ok=True)
        raise ValueError("recording contains no decodable frames")
    summary = {"status": "COMPLETE", "source": str(source), "model": model,
               "fps": fps, "timestamp_basis": timestamp_basis,
               "width": width, "height": height, "frames": index,
               "stride": stride, "detections": detections}
    document = outdir / "review.json"
    pending = outdir / "review.json.tmp"
    pending.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    pending.replace(document)
    return summary


def run_model_review(source: str | Path, outdir: str | Path, *, stride: int = 5) -> dict:
    """Run Dzivha's Grounding DINO over room footage in its own environment."""
    from .detection import GroundingDinoDetector, clean, deduplicate
    from .models import load_manifest

    classes = ("computer", "person", "phone", "pen")
    spec = load_manifest()["detector_openvocab"]
    detector = GroundingDinoDetector(spec["id"], spec["sha"],
                                     box_threshold=0.15, text_threshold=0.25,
                                     revision=spec["sha"], local_files_only=True)

    def detect(frame, index):
        height, width = frame.shape[:2]
        raw = []
        for start in range(0, len(classes), 3):
            group = list(classes[start:start + 3])
            result = detector.detect_array(frame, group, index, 0)
            if result.error:
                raise RuntimeError(f"Grounding DINO frame {index}: {result.error}")
            raw.extend(result.detections)
        kept, _rejected = clean(raw, width, height, list(classes))
        return [dict(label=row.query, score=row.score, box_xyxy=row.box_xyxy)
                for row in deduplicate(kept)]

    return review_video(source, outdir, detect, stride=stride, model=spec["id"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Annotate room footage after flight")
    parser.add_argument("video", type=Path)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--stride", type=int, default=5)
    args = parser.parse_args(argv)
    run_model_review(args.video, args.outdir, stride=args.stride)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
