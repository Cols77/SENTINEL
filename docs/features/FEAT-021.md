---
id: FEAT-021
title: "SENTINEL-WP5-DRONE-POSE"
description: Host-side AprilTag 36h11 drone pose estimation for the SENTINEL six-zone survey demo.
requirements:
  - SR-060
  - SR-061
  - SR-062
  - SR-063
---

# FEAT-021 — SENTINEL-WP5-DRONE-POSE

Status: proposed feature dossier (requirements authored 2026-09-18; consent pending).

The drone pose work package for the SENTINEL six-zone survey demo (V0). A standard
Tello (non-EDU) streams 960x720 video; a host-side detector (OpenCV ArUco,
AprilTag 36h11 dictionary, solvePnP) estimates room-local x/y position and heading
against a versioned tag-map fixture. Reports flow as `DronePoseReport` wire objects
(`DRONE_POSE_REPORTED` events) into the run-scoped SQLite store and project to a
`drone_pose` read model with WP0 §3.4 read-time staleness.

Safety boundary: pose is evidence and an advisory containment input only — it never
commands or modifies drone motion in V0 (SR-063). Acceptance is a user-approved
supervised physical test run validating the tools, the DB position update, and
real-time x/y/z terminal logging, behind the G4 operator-consent gate.

## Related requirements

- [[SR-060]] — Host-side AprilTag pose detection
- [[SR-061]] — Pose wire contract amendment
- [[SR-062]] — Pose persistence and read model
- [[SR-063]] — Pose safety boundary (advisory containment, never motion control)

Source plan: `.hermes/plans/2026-09-18_105500-sentinel-wp5-drone-pose-apriltag.md`
(SENT-POSE-V0).
