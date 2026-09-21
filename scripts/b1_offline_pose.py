"""Bench test B1, offline: position every recorded video frame from the tags and measure how far
the drone travelled after `land` was sent.

    uv run python scripts/b1_offline_pose.py artifacts/b1/b1-A-1.2s-141410.json --tag-map MAP

The run's .h264 is the newest recording written before its .json. The stream is re-fed to the
decoder in the UDP packets it arrived in (the harness logs [t, byte_offset] per packet), so each
decoded frame carries the arrival time of the packet that completed it, on the command log's clock.

Single frames jitter by several cm at 3 m, and a descending drone ends on single-tag fixes, so
every reported point is the median of the two-tag fixes in a short window.
"""
import argparse
import json
import math
import statistics
from pathlib import Path

import av

from sentinel.pose.detector import estimate_pose
from sentinel.pose.tagmap import load_tag_map

CM_PER_UNIT = 10.0


def recording_for(log_path: Path) -> Path:
    candidates = sorted(p for p in log_path.parent.glob(log_path.name.rsplit("-", 1)[0] + "-*.h264")
                        if p.stem < log_path.stem)
    if not candidates:
        raise SystemExit(f"no recording found for {log_path}")
    return candidates[-1]


def timed_frames(video: Path, packets: list):
    """Yield (t, bgr) per decoded frame, t = arrival of the UDP packet that completed it."""
    data = video.read_bytes()
    codec = av.CodecContext.create("h264", "r")
    bounds = [offset for _, offset in packets[1:]] + [len(data)]
    for (t, start), end in zip(packets, bounds):
        try:
            for packet in codec.parse(data[start:end]):
                for frame in codec.decode(packet):
                    yield t, frame.to_ndarray(format="bgr24")
        except av.error.FFmpegError:
            continue


def window_median(rows: list, t0: float, t1: float) -> dict | None:
    picked = [r for r in rows if t0 <= r["t"] <= t1 and r["tags"] >= 2]
    if not picked:
        return None
    return {"t": round(statistics.median(r["t"] for r in picked), 2), "n": len(picked),
            "x_cm": round(statistics.median(r["x_cm"] for r in picked), 1),
            "y_cm": round(statistics.median(r["y_cm"] for r in picked), 1),
            "heading_deg": round(statistics.median(r["heading_deg"] for r in picked), 1)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log")
    parser.add_argument("--tag-map", required=True)
    parser.add_argument("--video", default=None)
    parser.add_argument("--latency", type=float, default=0.2,
                        help="video lag behind the command clock, s (measured 0.21-0.22 s from motion onset "
                             "in video vs state-stream velocity, 2026-09-21)")
    args = parser.parse_args()
    log_path = Path(args.log)
    log = json.loads(log_path.read_text(encoding="utf-8"))
    video = Path(args.video) if args.video else recording_for(log_path)
    tag_map = load_tag_map(args.tag_map)

    rows, index = [], 0
    for t, bgr in timed_frames(video, log["video_packets"]):
        index += 1
        try:
            report = estimate_pose(bgr, tag_map, run_id="00000000-0000-4000-8000-000000000001",
                                   report_id=f"00000000-0000-4000-8000-{index:012d}")
        except Exception:  # noqa: BLE001 - no tag, or a fix the detector refused
            continue
        rows.append({"frame": index, "t": t, "x_cm": round(report.point[0] * CM_PER_UNIT, 1),
                     "y_cm": round(report.point[1] * CM_PER_UNIT, 1),
                     "heading_deg": round(report.heading_deg, 1),
                     "tags": len(report.tag_observations)})
    print(f"{video.name}: {index} frames, {len(rows)} localised")

    move_t = log["summary"].get("move_sent_t")
    land_t = next((e["t"] for e in log["events"] if e["kind"] == "send" and e["text"] == "land"
                   and move_t is not None and e["t"] > move_t), None)

    def dist(a, b):
        return round(math.dist((a["x_cm"], a["y_cm"]), (b["x_cm"], b["y_cm"])), 1)

    result = {"frames": index, "localised": len(rows)}
    two_tag = [r for r in rows if r["tags"] >= 2]
    if move_t is not None and two_tag:
        start = window_median(rows, move_t - 0.5 + args.latency, move_t + args.latency)
        last_t = two_tag[-1]["t"]
        final = window_median(rows, last_t - 0.4, last_t)
        result.update({"at_move_sent": start, "last_two_tag_fixes": final})
        if start and final:
            result["travel_total_cm"] = dist(start, final)
        if land_t is not None:
            seen_at = land_t + args.latency
            at_land = window_median(rows, seen_at - 0.15, seen_at + 0.15)
            result.update({"land_sent_t": land_t, "at_land_sent": at_land})
            if start and final and at_land:
                result.update({"travel_before_land_cm": dist(start, at_land),
                               "travel_after_land_cm": dist(at_land, final),
                               "last_two_tag_fix_after_land_s": round(last_t - land_t, 2)})
    for key, value in result.items():
        print(f"  {key}: {value}")
    out = log_path.with_name(log_path.stem + "-pose.json")
    out.write_text(json.dumps({"summary": result, "rows": rows}, indent=1), encoding="utf-8")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
