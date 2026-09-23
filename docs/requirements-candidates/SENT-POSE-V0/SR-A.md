---
id: SR-A
title: "Host-side AprilTag 36h11 pose detection per station"
statement: "The system shall detect AprilTag 36h11 markers host-side in Tello 960x720 frames using cv2.aruco with solvePnP (SOLVEPNP_IPPE_SQUARE) against a versioned tag-map-v1 room fixture, and shall produce a room-frame pose per station without using any EDU-only Tello capability (no Mission Pads, no 'mid' state fields)."
domain: behavioral
upstream: []
source: "docs/superpowers/specs/2026-09-16-sentinel-v0-coherence-spec.md#9"
---

## Scope

- New `sentinel.pose` package: tag-map loader (schema-validated `tag-map-v1`), detector, PnP pose solver.
- Detection runs on received frames only; camera intrinsics come from the tag-map fixture (`calibration_source: "nominal"` for V0). A chessboard calibration is an explicit follow-up.
- Unknown tag IDs are skipped, not trusted; zero mapped detections fails closed (no report emitted).
- Acceptance evidence at planning level: synthetic rendered tag frames (deterministic ground truth); the yaw-scan H264 replay may be used as a manual smoke pre-check.

## Non-goals

- No flight-control consumption of detection output (see SR-D).
- No native apriltag3 bindings; no new dependencies beyond the already-pinned OpenCV.
