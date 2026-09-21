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
`estimate_all` and `flag_tag_disagreement` implement this. Result below.

## Cross-tag disagreement (2026-09-20, truth-free)

Tags 4 and 1 mounted coplanar on the same wall, tag 1 exactly 30.1 cm above
tag 4 (centre to centre, measured on the wall to ~2 mm). Camera at 31 cm,
80 cm from the wall, square-on. 25 of 44 frames saw both tags. Each tag's
pose was solved independently from the same frame, so lighting, motion,
calibration and timing are identical between them - only viewing geometry
differs.

| tag | centre height | obliquity | x spread | corr(x, heading) |
|---|---|---|---|---|
| 1 (upper) | 40.6 cm | 6.8 deg | 8.66 cm | +0.9963 |
| 4 (lower) | 10.5 cm | 14.4 deg | 1.45 cm | +0.8553 |

Disagreement over the 25 shared frames: **median 2.31 cm**, p95 5.62 cm;
heading median 1.54 deg. Decomposed: **lateral +2.42 cm, depth +0.37 cm.**

**Depth geometry is validated without a tape measure.** Two tags at different
heights, solved independently, agree on camera depth to 3.7 mm. The tag map's
relative geometry and the PnP depth solution are both sound.

**The lateral disagreement is a conditioning artifact, not map error.** It
tracks the degenerate tag: tag 1, viewed at 6.8 deg obliquity, carries
corr(x, heading) = +0.9963 and six times the scatter of tag 4 at 14.4 deg.
Obliquity is the control variable, and this is a controlled measurement of it
- the two tags share everything except viewing angle.

Combined with the 111 cm run (4.9 deg obliquity, corr +0.9999, 24 cm spread),
the relationship is monotonic: **lateral precision collapses as the view
approaches fronto-parallel.**

### Deployment consequence

**Mount tags so the drone views them at >= 15 deg obliquity across the whole
operating envelope.** A tag mounted at flight altitude is viewed nearly
fronto-parallel and is the worst case - precisely the intuitive placement.
Offsetting tags vertically from the flight path is the cheapest fix, and it
costs nothing at deployment time.

### What this test cannot see

A constant lens-to-reference offset shifts both tags identically and so
produces no disagreement. The residual 1.5-2 cm depth bias measured against
tape therefore remains either tape error or a common-mode lens offset; this
method rules out map error and per-tag geometry error, which is what it was
designed to do.

## Cube navigation — Stage 1 "stand test" (2026-09-20 design, rev 3)

Design: `docs/superpowers/specs/2026-09-20-wp5-cube-positioning-design.md`. Motors off
throughout; `pose-stream` never takes off, so this stage needs no G3/G4 approval. It fits a small
room.

**Setup.** Two vertical supports ("stands") about 0.5 m apart on one straight line, one or two
tags each, tag faces square to the line. Lay one tape along the line: each stand's `x_m` is a
tape reading; tag-centre heights by ruler; tag edge length by ruler (printers scale ~96%). Write
`artifacts/site.json` (`cube-site-v1`) and generate the map:

    uv run sentinel cube-site-map --site artifacts/site.json --calibration-from calibration/tag-map-calibrated.json --out artifacts/cube-map.json

**Capture.** Drone on a support about 1.1 m back from the line, lens facing it: on the centre
line and about 0.5 m to one side, each at two lens heights (for example 1.0 and 1.4 m). Truth is
measured to the lens. Per point:

    uv run sentinel pose-stream --real --confirm --tag-map artifacts/cube-map.json --db artifacts/cube_s1.db --duration 20 --record-dir artifacts/frames_s1_<point> --preview

**Score.** `pose-score` against a truth file, ladder p95 <= 15 cm / 10 deg, then 10 / 5, then
5 / 3, with `--min-samples 15`. Then per recording, the truth-free comparison:

    uv run sentinel pose-map-check --frames artifacts/frames_s1_<point> --tag-map artifacts/cube-map.json

The design claim holds if the joint spread is no worse than the single-tag spread at every point
and clearly better at the off-centre points. A stand whose joint reprojection median exceeds 1 px
has been mis-measured: fix the site file, not the detector. Compare the measured spreads with
`cube-layout-check` for the same site and note the ratio; it recalibrates the 2.5x factor.

## Stage 1 stand-test results — 2026-09-21 (first real horizontal baseline)

Setup: two 36h11 tags on a flat door, 192 mm printed edge, centres 688 mm apart on one
horizontal line, 55 cm above the ground, coplanar, facing the drone. Drone on a support,
motors off, lens 76 cm above the ground, held square to the wall (never aimed at the tags).
Site file: `docs/wp5-stand-test-site.json`; map generated by `cube-site-map` from the
committed chessboard calibration. Truth measured by tape to the point under the lens.

| point | viewpoint | position error (median / p95) | heading | single-tag spread p95 | joint spread p95 |
|---|---|---|---|---|---|
| p1 | 1.1 m back, centred | 5.6 / 6.0 cm | +2.1° | 0.63 cm | 0.45 cm |
| p2 | 1.5 m back, centred | 2.6 / 2.9 cm | +3.4° | 0.92 cm | 0.51 cm |
| p3 | 1.5 m back, 30 cm off | 3.7 / 4.1 cm | +2.3° | **10.42 cm** | **0.71 cm** |
| p5 | 2.0 m back, 30 cm off | 4.3 / 4.3 cm | +2.3° | **12.49 cm** | **2.63 cm** |

`pose-score` PASS at 15 cm / 10° for all four. Every frame that saw both tags was used;
p2 and p5 saw both tags in 100% of frames. Not yet recorded: 2.0 m centred, and 2.0 m at
60 cm off (drone battery reached 5%).

**Findings**

1. **Joint PnP is validated on real frames with a horizontal baseline**, which no previous
   recording had. Off-axis it is 15x steadier at 1.5 m and 4.7x at 2.0 m. The single-tag
   solve wanders 10-12 cm off-axis and is not usable there; the design depends on the joint
   solve, and now has measured support for it.
2. **Simulation is pessimistic, not optimistic.** Measured error runs 1.5-2.2x the
   prediction for the same viewpoints, against the 2.5x reality factor the spec applies to
   every prediction (E7). The spec's accuracy figures therefore carry margin. One session,
   four points; the factor should be re-checked in the real room.
3. **Tag size 192 mm is confirmed.** A scale error would grow with range; the distance
   error instead fell from 5.5 cm at 1.1 m to 1.1 cm at 1.5 m. p1's offset was tape
   placement, which the operator had flagged as approximate.
4. **Map geometry fit (truth-free):** fitting the map to p1's frames puts tag 0 about 8 mm
   lower than tag 2 (reprojection 1.31 px -> 0.60 px), and best-fits the separation at
   690 mm against the 688 mm measured. Correcting the height changes the reprojection but
   not the position. Tag heights are worth measuring to a few mm; the separation was fine.
5. **Heading reads +2 to +3.4° high at every point**, consistently. Either the drone was
   not quite square or the tag plane is not parallel to the line the tape was laid along.
   Unresolved; it bounds what a yaw trim could achieve.
6. **The Wi-Fi link dropped three times**, all at low battery. A mission runner must
   survive a dropout. Where the laptop holds both the home network and the drone's at
   once, `--interface-name` is needed to select the drone's adapter.

## Bench test B1 — does a Tello honour `land` while a `move` is executing?

**Why.** SR-SENTINEL-V0-W08 requires containment to be checked *during moves* when tags are in
view, and to end the flight with a controlled landing. The in-move watchdog
(`src/sentinel/pose/watchdog.py`) can only act inside a move if the drone accepts a command while a
blocking `move` is in flight. Nobody has established that it does. Until this test passes, the
watchdog's send-during-move flag stays **off** and an abort takes effect when the move returns.

**Gate.** Motors on, so **G4 live-rehearsal consent** and the operator present at the controller
with the emergency cut to hand. Not a pose test: no tags needed.

**Preconditions.** Battery >= 50% (it drops fast and a dying Tello ignores commands — see the
2026-09-21 stand test). At least 3 m of clear run in the move direction plus 1 m either side.
Soft floor or a mat. Nobody within the run. Propellers guarded if available.

**Harness.** A throwaway script, not committed to the flight path: take off, hover 3 s, send
`forward 200` on the main thread, and from a second thread send the abort command after a set
delay. Record, with timestamps: when each command was sent, the exact response string and arrival
time for each, `height?` every 100 ms, and the wall-clock moment the motors stop.

**Matrix (revised 2026-09-21 after the first runs).** `stop` is not a command on this firmware, so variant B is variant A delayed by 0.3 s and is dropped. **Pass = 9 runs of variant A**: `forward 100` (the clear run available), `land` at 0.5 s, 1.2 s and 2.0 s after the move (early, mid, near the end of a ~2.5 s move), 3 repeats each, every run launched with `--clear-ahead-cm` measured. Original matrix kept below for the record:

**Original matrix.** Two abort commands x three delays x 3 repeats = 18 runs, in this order:

| variant | abort command sent during the move | delay after the move was sent |
|---|---|---|
| A | `land` | 0.5 s, 2.0 s, and ~0.5 s before the move would finish |
| B | `stop` then `land` 0.3 s later | same three delays |

Start with variant B at the 2.0 s delay: `stop` is the gentler command and the mid-move delay is
the least marginal case. Abandon the matrix and report if any run ends with the drone unresponsive,
still moving after the motors should have stopped, or requiring the emergency cut.

**Record per run.** Response to the abort command (`ok`, `error`, or nothing); whether the original
`move` response still arrived and what it said; latency from abort sent to descent starting;
**ground distance travelled after the abort was sent**, by tape, from a floor mark under the drone
at the moment of sending; time to motors off; the drone's state afterwards (does it still answer
`battery?`).

**The number that matters most is the post-abort travel distance.** It sizes the containment
margin: a watchdog that fires at the envelope edge is useless if the drone carries on another 40 cm.
Feed it to the watchdog's margin and to `Site.commanded` inset (currently 30 cm).

**Pass, all 18 runs.** The abort is acknowledged or acted on; the drone descends under control
without the emergency cut; no motion command follows the landing; the drone still answers telemetry
afterwards. Then the send-during-move flag may be enabled, with the margin set from the measured
travel distance.

**Fail, or any inconsistency.** Leave the flag off. The watchdog still satisfies W08's
stop-time obligation, and an in-move exit is acted on when the move returns. Say so plainly in the
W08 implementation status rather than reporting the requirement satisfied.

### B1 results so far — 2026-09-21 (outdoors, same Tello as the stand test)

| run | move | abort | reply to the move | abort to horizontal stop | abort to landed | travel after the abort (integrated vgx) |
|---|---|---|---|---|---|---|
| N | forward 200 | none (failsafe land at 12 s) | `ok` after 10.5 s | - | - | - |
| B@5s | forward 200 | stop + land | `error Motor stop` | void | void | **void: flew into the tag door** (started ~195 cm from it; no clearance check) |
| B@1.2s | forward 100 | stop, land at 1.5 s | `error Auto land`, 0.29 s after `land` | 1.4 s | 2.6 s | ~40 cm after `land` |
| A@1.2s | forward 100 | land | `error Auto land`, 0.16 s after `land` | 1.0 s | 2.3 s | ~32 cm |

- **The Tello honours `land` during a move.** The move ends with `error Auto land` within 0.3 s, the drone brakes and descends; no emergency cut needed; telemetry answered afterwards.
- **`stop` does not exist on this firmware** (`unknown command: stop`). Variant B is variant A with a 0.3 s delay; drop it.
- **Post-abort travel is ~30-40 cm at the ~45 cm/s cruise speed** (velocity integrated from the state stream, 10 cm/s resolution). That is at or above the 30 cm `Site.commanded` inset. Operator tape figures pending.
- `speed 50` does not shorten a 200 cm move (10.5 s).
- The state stream's `h` field is not the height (reads 0-10 at 90 cm hover); use `tof`.
- The Tello only answers the UDP source port that sent its first `command` after boot; the harness now uses a fixed port 8889.
- The B@5s video gave the first in-motion pose data: 278 of 414 frames localised, smooth joint fixes to ~2.3 m from the tag line and single-tag fixes to ~55 cm.

### Note on the simulations in this document

Every predicted figure here and in `cube-layout-check` is computed from this repository's chessboard
calibration matrix (fx 892.6, 960x720), so it already uses the true ~56.5° x 43.9° field of view,
not the datasheet-derived 70.2° x 55.6° that E4 corrects. The narrow field is why 688 mm of tag
separation outperformed 1 m at close range, and why the p1-p6 viewpoints were chosen where they
were. Any hand-calculation in older notes using "1.406 x range" for horizontal coverage is
optimistic; the calibrated figure is about 1.08 x range.
