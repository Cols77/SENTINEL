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
   the tag map with `calibration_source: "chessboard"`, and rejects (writes nothing) if the RMS reprojection error exceeds `--max-rms` (default 0.5 px). `pose-stream` refuses
   a `nominal` map unless `--allow-nominal` is passed (synthetic/replay only).
2. **Tape 6-10 floor points** across the alley, measured in the tag-map room
   frame (record x, y in room units, and the camera heading in degrees). Vary
   position and heading; include points seeing one tag and points seeing two.
   **Stratify by tag distance** (see "Static accuracy characterization" below):
   distance dominates every other factor, so the point set must span the
   working range rather than clustering at one radius. Points beyond ~1 m from
   the nearest tag will not meet the 10 cm gate on lateral error.
   **Truth must be measured to the camera lens**, not to the drone body, and
   from the same reference at every point; a body-vs-lens mix-up is worth
   several centimetres and is indistinguishable from a pipeline error.
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

## Static accuracy characterization (2026-09-20)

Single tag (id 4, 36h11, `size_m` 0.192, chessboard-calibrated map), drone
stationary with motors off, tag square-on, scored with `pose-score`. Camera at
12 cm for the 40 and 80 cm runs, on the floor for 111 cm.

| tag distance | n | bias dx (lateral) | bias dy (depth) | spread p95 | heading bias |
|---|---|---|---|---|---|
| 40 cm | 27 | +0.4 cm | +0.1 cm | 1.0 cm | -0.9 deg |
| 80 cm | 42 | +0.2 cm | +2.0 cm | 2.1 cm | -0.1 deg |
| 111 cm | 100 | +2.0 cm | +1.6 cm | 11.1 cm | +2.6 deg |

**Precision degrades faster than distance squared.** 2.8x the distance gives
12.8x the scatter, where D^2 predicts 7.7x. Spread is measured against each
run's own median, so this result does not depend on ground truth and is not
affected by any tape-measurement error.

**The working envelope is roughly 40-80 cm per tag** for centimetre accuracy.
At 111 cm lateral error reaches +2.0 cm with 11.1 cm of spread, which passes
the 10 cm p95 gate only marginally. Six tags covering a full alley at
metre-plus ranges will not deliver what a closed-loop position target would
need; that is a coverage/density question, not a calibration one.

**At 111 cm the lateral solve is degenerate, not noisy.** corr(x, heading) =
+0.9999 across 100 poses, which collapsed to 10 distinct solutions: the solver
trades lateral offset against yaw along a single ill-conditioned direction.
A nearly fronto-parallel planar target is the classic ill-posed case for PnP.

**The Tello camera is pitched about 8.6 deg downward.** Measured from the
horizon position in a recorded frame (wall/floor junction at y~215 against
cy 353.8, camera 1 cm off the floor). This sets the visibility envelope: a tag
above camera height climbs in the frame as the drone approaches and clips out
of view. With the camera on the floor and the tag centre at 10.5 cm, the tag
leaves the frame closer than ~85 cm. Nothing in the tag map or the planner
models this today.

**Reprojection error is not a proxy for pose accuracy.** It was ~0.5 px in both
the most accurate run (40 cm, 1-4 mm) and the least (111 cm, 11 cm spread).
Do not use it as a quality gate for positioning.

### Retraction: the Test A "systematic bias"

Test A (one point, one tag, 108 cm, 25 poses) reported median == p95 == 4.4 cm
and was read as a systematic bias of unknown origin. It is not. Candidates
tested and rejected:

- **Tag size / `size_m`** - predicted the tag would measure ~19.5 cm; it
  measures 19.2 cm. Rejected by direct measurement.
- **Focal-length bias** - would scale with distance; the 40 cm residual was
  -0.10 cm where a proportional error predicted -0.62 cm. Rejected.
- **Constant lens-to-body offset** - predicted -1.74 cm at every distance; the
  40 cm residual was -0.10 cm. Rejected as stated, though a ~1.7 cm offset
  remains consistent with the 80 and 111 cm runs alone.

The 4.4 cm is better explained by distance-dependent degradation plus
ground-truth measurement error. Test A's apparent precision (median == p95)
was a stable *wrong* solution in the ill-conditioned regime, not evidence of
accuracy - which is why `pose-score` now reports bias and spread separately.

### Open: residual depth bias

A ~1.5-2 cm depth (range) bias persists at 80 and 111 cm but not at 40 cm. It
cannot be resolved by this method: hand-measured truth in these runs carried
several centimetres of uncertainty (one run intended as 70 cm was actually
80 cm), which is larger than the effect. Settling it requires either truth
measured to a few mm, or a truth-free method.

**The truth-free method is cross-tag disagreement.** With two or more tags in
view, each tag independently estimates the camera position and they must
agree; disagreement isolates map/geometry error with no tape measure involved.
`estimate_all` and `flag_tag_disagreement` already implement this and are not
yet exercised outside unit tests.
