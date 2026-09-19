# WP5 pose physical rehearsal manifest (SENT-POSE-V0)

The user-approved supervised physical test is the acceptance gate for the WP5
pose package. Synthetic/simulated success does not substitute for it.

## What the physical test validates

1. **Tool implementations** — the AprilTag 36h11 detector + PnP pipeline
   (`sentinel.pose`) on the real Tello camera stream, with the tag map loaded
   from the checked-in fixture.
2. **Drone position update in the database** — every pose event is persisted
   through the run-scoped SQLite store (`ensure_schema` / `append_pose_event`
   / `latest_pose`) with canonical JSON payloads and monotonic `sequence_no`.
3. **Real-time x/y/z logged in the terminal** — pose reports are streamed to
   stdout at **>= 1 Hz** during the whole rehearsal so the operator watches the
   live estimate as the drone moves.

## Rehearsal scope

- Six-zone alley route as in the V0 survey design (marker mode default).
- A missing expected marker in marker mode remains a failure, unchanged from
  V0; the pose stream is advisory telemetry and never motion control
  (SR-063 containment).

## Preflight — the 8 named checks

`WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`,
`LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, `EMERGENCY_LATCH`.

Fresh process, `--real --confirm`; the `EMERGENCY` latch is terminal and
non-overridable. Standard Tello non-EDU hardware note: the non-EDU Tello
provides downward-camera-based height and IMU-derived velocity only; no
external odometry — the AprilTag PnP stream is the room-frame position
reference for this rehearsal.

## Bonus experiment — IMU drift logging

During the same 6-zone route, log the read-only Tello state fields
`vgx`/`vgy`/`vgz` (velocity) and `h` (height) alongside the pose stream for an
empirical IMU-drift measurement (integrate velocity vs. the tag-derived
position over time). Read-only telemetry; it does not affect the flight path
or any control decision.

## Package check (pre-rehearsal gate)

```text
uv run python -m pytest tests/contracts tests/pose tests/integration -q
```

All must pass before the physical run is scheduled.
