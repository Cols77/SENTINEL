"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
import cv2
import numpy as np
from sentinel_perception.detection import clean, deduplicate, detect_multipass

from .config import DEFAULT
from .quality import profile
from pathlib import Path



from .choreography import check_choreography, loop_closure, segment
from .evidence import EvidenceWriter
from .selection import SELECTION_VERSION, flag_relative_texture, keyframes, select
from .video import probe, read_frames


def cmd_probe(args) -> int:
    info = probe(args.video)
    print(json.dumps(info.to_dict(), indent=2))
    return 0


def cmd_choreo(args) -> int:
    cfg = DEFAULT
    info = probe(args.video)
    print(f"decoding {info.path} ...")

    qualities = profile(args.video, cfg.quality, info.fps)
    phases = segment(qualities, cfg.choreography, info.fps)
    check = check_choreography(phases, cfg.choreography)

    n_motion = sum(1 for q in qualities if "MOTION" in q.flags)
    n_expo = sum(1 for q in qualities if "EXPOSURE" in q.flags)

    print(f"\ndecoded {len(qualities)} frames "
          f"(container claimed {info.frame_count})")
    print(f"rejected: motion {n_motion}, exposure {n_expo}\n")

    print(f"{'phase':>7} {'idx':>4} {'frames':>12} {'t0':>7} {'t1':>7} "
          f"{'dur':>6} {'medflow':>8} {'medtex':>9}")
    print("-" * 68)
    for p in phases:
        tex = [q.tenengrad for q in qualities[p.first:p.last + 1]]
        label = f"sector {p.index}" if p.kind == "DWELL" else f"turn {p.index}"
        print(f"{p.kind:>7} {p.index:>4} {f'{p.first}-{p.last}':>12} "
              f"{p.t0_s:>7.2f} {p.t1_s:>7.2f} {p.n_frames / info.fps:>6.2f} "
              f"{p.median_motion:>8.2f} {float(sum(tex) / len(tex)):>9.0f}"
              f"   {label}")

    print(f"\nchoreography: {check['verdict']}")
    print(f"closes loop : {check['closes_loop']}")

    if args.json:
        payload = {
            "config_version": cfg.version,
            "video": info.to_dict(),
            "phases": [p.to_dict() for p in phases],
            "check": check,
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0

def cmd_select(args) -> int:
    cfg = DEFAULT
    outdir = Path(args.outdir)
    frames_dir = outdir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    ev = EvidenceWriter(outdir / "perception.jsonl")

    info = probe(args.video)
    print(f"decoding {info.path} ...")

    qualities = profile(args.video, cfg.quality, info.fps)
    phases = segment(qualities, cfg.choreography, info.fps)
    check = check_choreography(phases, cfg.choreography)

    ev.emit("run_started", "LIVE", {
        "video": info.to_dict(), "config": cfg.to_dict(),
        "selection_version": SELECTION_VERSION,
    })
    ev.emit("scan_segmented", "INFERRED",
            {"phases": [p.to_dict() for p in phases], "check": check})

    selected = select(qualities, phases, cfg.choreography, alternates=args.alternates)
    texture = flag_relative_texture(selected, cfg.quality)

    usable = [s for s in selected if s.usable]
    images = read_frames(args.video, {s.frame_index for s in usable})

    for s in usable:
        # PNG not JPEG: JPEG blocking artifacts are edge energy the detector
        # did not see in the original, and this image is evidence.
        p = frames_dir / f"sector{s.sector}_{s.role}{s.rank}_f{s.frame_index:04d}.png"
        cv2.imwrite(str(p), images[s.frame_index])
        s.image_path = str(p)

    for s in selected:
        ev.emit("frame_selected" if s.usable else "sector_unobservable",
                "LIVE", s.to_dict())

    keys = keyframes(selected)
    loop, verdict = None, "not applicable"
    usable_keys = [s for s in keys if s.usable]
    if len(usable_keys) >= 2 and check["closes_loop"]:
        loop = loop_closure(images[usable_keys[0].frame_index],
                            images[usable_keys[-1].frame_index])
        verdict = ("yaw consistent with commanded"
                   if loop >= cfg.choreography.loop_closure_min
                   else "INCONSISTENT -- bearings unreliable")
        ev.emit("loop_closure_checked", "INFERRED", {
            "similarity": loop, "minimum": cfg.choreography.loop_closure_min,
            "verdict": verdict,
        })

    print(f"\n{'sector':>6} {'role':>10} {'frame':>6} {'t':>6} {'bearing':>8} "
          f"{'texture':>9}  flags")
    print("-" * 78)
    for s in selected:
        fr = s.frame_index if s.usable else "--"
        print(f"{s.sector:>6} {s.role:>10} {fr:>6} {s.t_s:>6.2f} "
              f"{s.bearing_centre_deg:>7.0f}  {s.quality.tenengrad:>9.0f}  "
              f"{' '.join(s.flags)}")

    print(f"\nrun_id      : {ev.run_id}")
    print(f"keyframes   : {len(usable_keys)} of {check['dwells']} sectors")
    print(f"total frames: {len(usable)} (incl. alternates)")
    print(f"texture     : median {texture.get('run_median_tenengrad')}, "
          f"rel floor {texture.get('relative_floor')}")
    if loop is not None:
        print(f"loop closure: {loop:.3f} -- {verdict}")
    print(f"frames      : {frames_dir}")
    return 0

def cmd_detect(args) -> int:
    import numpy as np

    from .detection import (DETECTION_VERSION, GroundingDinoDetector,
                            sweep_thresholds)
    from .models import load_manifest, stage

    outdir = Path(args.outdir)
    frames_dir = outdir / "frames"
    if not frames_dir.exists():
        print(f"no frames in {frames_dir} -- run `select` first")
        return 1

    # Keyframes only by default. Alternates exist as fallbacks, not as extra
    # evidence: detecting on all three would triple-count every object.
    pattern = "sector*_keyframe*.png" if not args.all_frames else "sector*.png"
    frames = sorted(frames_dir.glob(pattern))
    if not frames:
        print(f"no files matching {pattern}")
        return 1

    manifest = load_manifest()
    spec = manifest["detector_openvocab"]
    ev = EvidenceWriter(outdir / "perception.jsonl", run_id=args.run_id)
    queries = args.queries

    print(f"model   : {spec['id']} @{spec['sha'][:8]}")
    print(f"queries : {queries}")
    print(f"frames  : {len(frames)}\n")

    all_dets = []
    with stage("grounding-dino"):
        det = GroundingDinoDetector(spec["id"], spec["sha"],
                                    box_threshold=args.box_threshold,
                                    text_threshold=args.text_threshold)
        for path in frames:
            sector = int(path.stem.split("_")[0].replace("sector", ""))
            frame_index = int(path.stem.split("_f")[-1])

            # Short prompts, several passes. See detect_multipass.
            groups = [queries[i:i + args.group_size]
                      for i in range(0, len(queries), args.group_size)]
            raw = []
            for res in detect_multipass(det, path, groups, frame_index, sector):
                ev.emit("detector_called", "LIVE", {
                    "image": str(path), "sector": sector,
                    "detection_version": DETECTION_VERSION, "result": res.to_dict(),
                })
                if res.error:
                    print(f"  sector {sector}: ERROR {res.error}")
                    continue
                raw.extend(res.detections)

            kept, rejected = clean(raw, 960, 720, queries,
                                   drop_stuff=not args.keep_stuff)
            kept = deduplicate(kept)
            all_dets.extend(kept)

            rej = " ".join(f"{k}={v}" for k, v in sorted(rejected.items()))
            print(f"  sector {sector}: {len(raw):3d} raw -> {len(kept):2d} kept"
                  + (f"   [{rej}]" if rej else ""))
            for d in kept[:5]:
                print(f"      {d.score:.3f}  {d.query:<16} {d.box_xyxy}")

    if not all_dets:
        print("\nno detections at any score -- lower --box-threshold and retry")
        return 0

    scores = np.array([d.score for d in all_dets])
    print(f"\nscore distribution over {len(scores)} detections")
    print(f"  min {scores.min():.3f}  p25 {np.percentile(scores, 25):.3f}  "
          f"median {np.median(scores):.3f}  p75 {np.percentile(scores, 75):.3f}  "
          f"max {scores.max():.3f}")

    print("\nthreshold sweep")
    sweep = sweep_thresholds(all_dets, [0.90, 0.70, 0.60, 0.50, 0.45,
                                        0.40, 0.35, 0.30, 0.25, 0.20])
    print(f"  {'thresh':>7} {'dets':>5} {'sectors':>8}  by query")
    for row in sweep:
        by_q = " ".join(f"{k}={v}" for k, v in row["by_query"].items() if v)
        print(f"  {row['threshold']:>7.2f} {row['n_detections']:>5} "
              f"{row['n_sectors']:>8}  {by_q}")

    ev.emit("threshold_sweep", "INFERRED", {"sweep": sweep,
                                            "n_detections": len(all_dets)})
    return 0

def cmd_depth(args) -> int:
    import json

    from .depth import (DEPTH_VERSION, DepthEstimator, Intrinsics,
                        sample_box, to_world)
    from .models import load_manifest, stage

    outdir = Path(args.outdir)
    stream = outdir / "perception.jsonl"
    if not stream.exists():
        print("no perception.jsonl -- run `select` then `detect` first")
        return 1

    # Rebuild detections from the event stream rather than re-running the
    # detector. The stream is the record, anything derived from it stays
    # consistent with what was actually emitted.
    records = [json.loads(l) for l in stream.read_text().splitlines() if l.strip()]
    dets = []
    for rec in records:
        if rec["event_type"] != "detector_called":
            continue
        res = rec["payload"]["result"]
        if res.get("error"):
            continue
        for d in res["detections"]:
            d["image"] = rec["payload"]["image"]
            dets.append(d)

    dets = [d for d in dets if d["score"] >= args.min_score]
    if not dets:
        print(f"no detections at score >= {args.min_score}")
        return 1

    cfg = DEFAULT
    intr = Intrinsics.from_fov(cfg.camera.width, cfg.camera.height,
                               cfg.camera.hfov_deg, cfg.camera.vfov_deg)
    spec = load_manifest()["depth_metric"]
    ev = EvidenceWriter(stream, run_id=records[0]["run_id"])

    print(f"model      : {spec['id']} @{spec['sha'][:8]}")
    print(f"intrinsics : fx={intr.fx} fy={intr.fy} ({intr.source})")
    print(f"detections : {len(dets)} at score >= {args.min_score}\n")

    by_image: dict[str, list] = {}
    for d in dets:
        by_image.setdefault(d["image"], []).append(d)

    results = []
    with stage("depth-anything"):
        est = DepthEstimator(spec["id"], spec["sha"])
        for image, group in sorted(by_image.items()):
            depth = est.depth_map(image)
            sector = group[0]["sector"]
            print(f"  {Path(image).name}  depth {depth.min():.2f}-{depth.max():.2f} m")

            for d in group:
                rng = sample_box(depth, d["box_xyxy"])
                if not np.isfinite(rng.range_m):
                    print(f"      {d['query']:<14} NO DEPTH")
                    continue
                pos = to_world(d["box_xyxy"], rng.range_m, sector, intr,
                               cfg.choreography.turn_deg)
                row = {"detection": d, "range": rng.to_dict(),
                       "position": pos.to_dict()}
                results.append(row)
                print(f"      {d['query']:<14} {d['score']:.3f}  "
                      f"{rng.range_m:6.2f} m ({rng.p25_m:.1f}-{rng.p75_m:.1f}) "
                      f"{rng.confidence:<8} bearing {pos.bearing_deg:5.1f}  "
                      f"xy ({pos.x_m:6.2f}, {pos.y_m:6.2f})")

    ev.emit("depth_estimated", "INFERRED", {
        "depth_version": DEPTH_VERSION,
        "model": {"id": spec["id"], "sha": spec["sha"]},
        "intrinsics": intr.to_dict(),
        "min_score": args.min_score,
        "results": results,
    })

    out = outdir / "positions.json"
    out.write_text(json.dumps({
        "model": spec, "intrinsics": intr.to_dict(),
        "frame": "local_takeoff_enu", "provenance": "INFERRED",
        "caveat": ("range from a monocular metric checkpoint; validate against "
                   "measured ground truth before quoting absolute values"),
        "objects": results,
    }, indent=2))
    print(f"\nwrote {out}")
    return 0

def cmd_analyse(args) -> int:
    import json
    import time

    from .choreography import check_choreography, loop_closure, segment
    from .detection import (GroundingDinoDetector, clean, deduplicate,
                            detect_multipass)
    from .models import load_manifest, stage
    from .render import draw
    from .scene import (SCENE_VERSION, aggregate, bearing_deg, dedupe_loop_closure,
                        describe, narrate)
    from .selection import flag_relative_texture, keyframes, select
    from .tracking import SectorTracker, merge_tracks
    from .video import iter_frames, probe, read_frames

    cfg = DEFAULT
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ev = EvidenceWriter(outdir / "analysis.jsonl")

    info = probe(args.video)
    print(f"video : {info.width}x{info.height} @{info.fps:.0f}fps\n"
          f"pass 1: quality profile and choreography ...")

    qualities = profile(args.video, cfg.quality, info.fps)
    phases = segment(qualities, cfg.choreography, info.fps)
    check = check_choreography(phases, cfg.choreography)
    dwells = [p for p in phases if p.kind == "DWELL"]

    selected = select(qualities, phases, cfg.choreography, alternates=0)
    flag_relative_texture(selected, cfg.quality)
    keys = [s for s in keyframes(selected) if s.usable]
    key_idx = {s.frame_index for s in keys}

    # frame -> (phase, sector)
    frame_phase: dict[int, tuple[str, int | None]] = {}
    si = 0
    for p in phases:
        for f in range(p.first, p.last + 1):
            frame_phase[f] = (p.kind, si if p.kind == "DWELL" else None)
        if p.kind == "DWELL":
            si += 1

    loop = None
    if len(keys) >= 2 and check["closes_loop"]:
        imgs = read_frames(args.video, {keys[0].frame_index, keys[-1].frame_index})
        loop = loop_closure(imgs[keys[0].frame_index], imgs[keys[-1].frame_index])
    yaw_ok = bool(loop is not None and loop >= cfg.choreography.loop_closure_min)

    print(f"        {check['verdict']}, {len(dwells)} sectors"
          + (f", loop closure {loop:.3f}" if loop is not None else ""))

    # ---- pass 2: detect on a stride through the dwells --------------------
    # Rotation frames are motion-blurred by construction, detecting there
    # produces smeared boxes that pollute the track set and the counts.
    targets = sorted(f for f, (kind, _) in frame_phase.items()
                     if kind == "DWELL" and (f % args.stride == 0 or f in key_idx))
    print(f"pass 2: detecting on {len(targets)} of {len(qualities)} frames "
          f"(stride {args.stride}, dwells only)")

    spec = load_manifest()["detector_openvocab"]
    groups = [args.queries[i:i + args.group_size]
              for i in range(0, len(args.queries), args.group_size)]

    tracker = SectorTracker()
    per_frame: dict[int, list] = {}
    last_sector = None
    t0 = time.time()

    with stage("grounding-dino"):
        det = GroundingDinoDetector(spec["id"], spec["sha"],
                                    box_threshold=args.box_threshold,
                                    text_threshold=args.text_threshold)
        images = read_frames(args.video, set(targets))
        tmp = outdir / "_tmp.png"

        for n, f in enumerate(targets):
            kind, sector = frame_phase[f]
            if sector != last_sector:
                tracker.new_sector()
                last_sector = sector

            cv2.imwrite(str(tmp), images[f])
            raw = []
            for res in detect_multipass(det, tmp, groups, f, sector):
                if not res.error:
                    raw.extend(res.detections)

            kept, _ = clean(raw, info.width, info.height, args.queries,
                            drop_stuff=not args.keep_stuff)
            kept = deduplicate(kept)
            dicts = [d.to_dict() for d in kept]
            per_frame[f] = dicts
            tracker.update(dicts, f, sector)

            if n % 10 == 0:
                print(f"        {n:4d}/{len(targets)}  frame {f:4d}  "
                      f"sector {sector}  {len(kept)} kept")
        tmp.unlink(missing_ok=True)

    print(f"        {time.time() - t0:.0f}s, {len(tracker.tracks)} raw tracks")

    # ---- aggregate --------------------------------------------------------
    tracks = [t for t in tracker.tracks if t.n_obs >= args.min_observations]
    objects = [describe(t, info.width, info.height, cfg.choreography.turn_deg,
                        cfg.camera.hfov_deg, cfg.camera.vfov_deg, info.fps, yaw_ok)
               for t in tracks]
    dedup = dedupe_loop_closure(objects, len(dwells), cfg.choreography.turn_deg, yaw_ok)
    objects.sort(key=lambda o: (-o["peak_score"], o["sector"]))
    scene = aggregate(objects, len(dwells), args.confirm, cfg.choreography.turn_deg,
                      dedup.get("merged_sector"))

    # ---- pass 3: render ---------------------------------------------------
    print(f"pass 3: rendering ...")
    track_by_id = {t.track_id: t for t in tracks}
    out_path = outdir / "annotated.mp4"
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             info.fps, (info.width, info.height))

    for f, frame in iter_frames(args.video):
        kind, sector = frame_phase.get(f, ("ROTATE", None))
        boxes = []
        if kind == "DWELL":
            for t in tracks:
                if t.sector != sector:
                    continue
                box, observed = t.box_at(f)
                if box is None:
                    continue
                if f - t.observations[-1][0] > args.stride * 2:
                    continue                      # stale, stop drawing
                cx = (box[0] + box[2]) / 2 / info.width
                boxes.append((t.query, t.peak_score, box,
                              bearing_deg(cx, sector, cfg.choreography.turn_deg,
                                          cfg.camera.hfov_deg), observed))
        q = qualities[f] if f < len(qualities) else None
        writer.write(draw(frame, boxes, sector, kind, f, f / info.fps,
                          flags=[x for x in (q.flags if q else []) if x != "MOTION"],
                          selected=f in key_idx))
    writer.release()

    # ---- outputs ----------------------------------------------------------
    summary = {
        "run_id": ev.run_id,
        "source": str(args.video),
        "scene_version": SCENE_VERSION,
        "provenance": "LIVE",
        "video": info.to_dict(),
        "detector": {
            "model": spec["id"], "sha": spec["sha"],
            "queries": args.queries, "group_size": args.group_size,
            "box_threshold": args.box_threshold,
            "text_threshold": args.text_threshold,
            "confirm_threshold": args.confirm,
            "frames_processed": len(targets), "stride": args.stride,
            "tracks_raw": len(tracker.tracks), "tracks_kept": len(tracks),
            "min_observations": args.min_observations,
        },
        "camera": {"hfov_deg": cfg.camera.hfov_deg, "vfov_deg": cfg.camera.vfov_deg},
        "scan": {
            "sectors": len(dwells), "rotations": check["rotations"],
            "choreography": check["verdict"],
            "loop_closure": loop, "yaw_verified": yaw_ok,
            "dedup": dedup,
            "note": ("bearings assume the commanded yaw executed; "
                     "loop closure is the independent check"),
        },
        "scene": scene,
        "objects": objects,
        "narrative": narrate(scene),
    }
    ev.emit("analysis_complete", "LIVE", summary)
    (outdir / "scene.json").write_text(json.dumps(summary, indent=2, default=str))

    print(f"\n{summary['narrative']}\n")
    print(f"video : {out_path}")
    print(f"stats : {outdir / 'scene.json'}")
    print(f"events: {outdir / 'analysis.jsonl'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sentinel-perception")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("probe", help="video metadata only")
    p1.add_argument("video")
    p1.set_defaults(func=cmd_probe)

    p2 = sub.add_parser("choreo", help="recover the yaw-scan pattern")
    p2.add_argument("video")
    p2.add_argument("--json", default=None, help="write results here")
    p2.set_defaults(func=cmd_choreo)

    p3 = sub.add_parser("select", help="gate and extract sector frames")
    p3.add_argument("video")
    p3.add_argument("--outdir", default="artifacts")
    p3.add_argument("--alternates", type=int, default=2)
    p3.set_defaults(func=cmd_select)

    p4 = sub.add_parser("detect", help="open-vocabulary detection on sector frames")
    p4.add_argument("--outdir", default="artifacts")
    p4.add_argument("--queries", nargs="+",
                    default=["rock", "loose scree", "boulder", "person"])
    p4.add_argument("--box-threshold", type=float, default=0.15)
    p4.add_argument("--text-threshold", type=float, default=0.20)
    p4.add_argument("--all-frames", action="store_true",
                    help="include alternates, not just keyframes")
    p4.add_argument("--group-size", type=int, default=3,
                    help="queries per prompt; >4 degrades grounding")
    p4.add_argument("--keep-stuff", action="store_true",
                    help="keep material-region queries (normally segmented instead)")
    p4.add_argument("--run-id", default=None)
    p4.set_defaults(func=cmd_detect)

    p5 = sub.add_parser("depth", help="metric range and world position")
    p5.add_argument("--outdir", default="artifacts")
    p5.add_argument("--min-score", type=float, default=0.30)
    p5.set_defaults(func=cmd_depth)

    p6 = sub.add_parser("analyse", help="video in -> annotated video + scene JSON")
    p6.add_argument("video")
    p6.add_argument("--outdir", default="artifacts")
    p6.add_argument("--queries", nargs="+",
                    default=["rock", "boulder", "person", "brick paving",
                             "potted plant", "chair"])
    p6.add_argument("--stride", type=int, default=5,
                    help="detect every Nth dwell frame")
    p6.add_argument("--group-size", type=int, default=3)
    p6.add_argument("--box-threshold", type=float, default=0.15)
    p6.add_argument("--text-threshold", type=float, default=0.25)
    p6.add_argument("--confirm", type=float, default=0.45)
    p6.add_argument("--min-observations", type=int, default=2,
                    help="drop tracks seen fewer times than this as flicker")
    p6.add_argument("--keep-stuff", action="store_true")
    p6.add_argument("--merge-iou", type=float, default=0.45,
                    help="IoU above which two tracks in a sector are one object")
    p6.set_defaults(func=cmd_analyse)


    args = ap.parse_args(argv)
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())