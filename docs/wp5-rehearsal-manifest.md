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

## Ground-truth accuracy protocol (static, before the flight rehearsal)

Accuracy is established offline against tape-measured floor points, not by
the flight itself.

1. **Calibrate the camera first.** Run `scripts/calibrate_camera.py` on
   checkerboard images from the Tello camera; it writes K + distortion into
   the tag map with `calibration_source: "calibrated"`, and rejects (writes nothing) if the RMS reprojection error exceeds `--max-rms` (default 0.5 px). `pose-stream` refuses
   a `nominal` map unless `--allow-nominal` is passed (synthetic/replay only).
2. **Tape 6-10 floor points** across the alley, measured in the tag-map room
   frame (record x, y in room units, and the camera heading in degrees). Vary
   position and heading; include points seeing one tag and points seeing two.
3. **One stationary run per point, 30 frames each**, with a distinct
   `--run-id` per point (e.g. `p01` .. `p10`), recording raw frames so the run
   can be re-scored: `pose-stream --real --confirm --max-frames 30
   --run-id p01 --record-dir artifacts/rec/p01 --tag-map <calibrated map>`.
4. **Write `truth.json`** mapping `run_id` -> `{"x": .., "y": .., "heading_deg": ..}`.
5. **Score:** `pose-score --db artifacts/pose.db --truth truth.json --tag-map artifacts/tag-map-calibrated.json`. Scoring fails if the tag map calibration is nominal (`--allow-nominal` overrides, for synthetic runs only).
   Default thresholds: **p95 position error <= 10 cm** and **p95 heading error
   <= 5 deg** for every point; a point with no stored poses fails
   (fail-closed). Override with `--p95-cm` / `--heading-deg` only with a
   recorded reason.
6. Recorded frames can be re-run offline with `pose-stream --replay <dir|mp4>`
   (reports are marked `REPLAYED`, no drone or `--confirm` needed).

Reports are tagged `LIVE` for `--real` runs and `REPLAYED` for replay. The
terminal prints `x`, `y`, `z` and `heading` per report; `z` is a best-effort,
throttled read-only height query and prints `n/a` when unavailable. Isolated
video read timeouts are tolerated; the stream aborts after 5 consecutive ones.

## Integration note

`pose.db` is **standalone** until WP1 wires `latest_pose` into the coordinator
read model: the pose stream writes and scores its own SQLite file, and nothing
in the coordinator reads it yet.

## Package check (pre-rehearsal gate)

```text
uv run python -m pytest tests/contracts tests/pose tests/integration tests/test_cli_pose_stream.py -q
```

All must pass before the physical run is scheduled.
