# Session Handoff — 2026-09-20 — WP5 drone pose: validation & next tests

## Project
SENTINEL: Tello drone surveying a six-zone indoor alley. WP5 = host-side AprilTag (36h11) pose estimation, persisted as `DRONE_POSE_REPORTED` rows in SQLite. Branch `feat/sentinel-wp5-drone-pose`, worktree `C:\coding\sentinel\.worktrees\wp5-drone-pose` (**run everything from that folder**; `master` has none of this). Nothing is pushed or merged. 210 tests pass. The root `HANDOFF.md` belongs to the coherence/spec work — do not overwrite it.

## Task in Progress
Prove positioning accuracy in stages, then test the whole system:
- **Test A (done, passed):** one static point, one tag.
- **Test B (next):** static accuracy at many ground-marked points, 6-tag map.
- **Test C (after B):** full system — planning + flight to a target x,y,z; measure how close the drone gets. Relaxed thresholds first, then strict.

## Current Status
- **Completed**
  - Pose DB feed fixed and tested: tz-aware UTC `observed_at`, working read-time staleness, wire-schema-valid payloads (uuid ids, heading in [-180,180], altitude >= 0), `source_kind` LIVE/REPLAYED.
  - `pose-stream` (video only, no flight commands): `--real --confirm` or `--replay PATH`, `--record-dir`, `--preview` (live window: board/tag overlay + coverage map), live x/y/z/heading per pose, timeout-tolerant, no-tag frames summarized in one line.
  - `pose-score` (offline): median/p95 position error (cm), heading p95, PASS/FAIL; fails if the tag map calibration is `nominal`.
  - `scripts/calibrate_camera.py` with gates: RMS <= 0.5 px, max tilt >= 25 deg, split-half fx agreement <= 5%.
  - `ReplayClient`/`FrameRecorder`, per-tag consistency helpers (`estimate_all`, `flag_tag_disagreement`).
  - Camera calibrated: `calibration/tag-map-calibrated.json` (7x6 board, 2.41 cm squares, 158 views, rms 0.311 px, fx 893, fy 894, cx 468, cy 354, `calibration_source: "chessboard"`).
- **In progress:** nothing running.
- **Blocked on:** nothing technical. Decisions needed: see "Open decisions".

## Test A result (the milestone so far)
Tag 4 placed at operator-frame (x=0, y=108 cm), drone at (x=-12 cm, y=0), motors off, 20 s, 25 poses. The pose contract only accepts room x,y in 0..100, so the test frame was shifted by (+50, +10) units (1 unit = 10 cm) in `artifacts/test-tag-map-108cm.json` (not committed). Truth in `artifacts/truth_108cm.json`.
```
uv run sentinel pose-score --db artifacts/check_108cm.db --truth artifacts/truth_108cm.json --tag-map artifacts/test-tag-map-108cm.json
-> PASS n=25 median=4.4cm p95=4.4cm heading_p95=4.2deg
```
Analysis of the raw rows: x 48.48 (truth 48.8, -3.2 cm), y 10.29 (truth 10.0, +2.9 cm), heading 85.8 (truth assumed 90; 83.7 if the drone was aimed at the tag). Spread is tiny (sd 0.03-0.12 mm-level, 25 samples, reprojection 0.23 px), so **median == p95 = a systematic bias, not noise**. Precision is excellent; ~4.4 cm accuracy bias is unexplained (candidates: tag/drone placement measurement, lens-to-drone-body offset, camera pitch, calibration).
**The heading PASS is not real evidence**: the true heading was not pinned down (90 vs 83.7).

## Assessment: is Test A a reliable gate?
It is a valid **milestone** ("pipeline + calibration + geometry work at one point"), not yet a **gate** for the positioning system. It covers one point, one tag, on-axis, image centre, static, 25 samples, 10 cm threshold. Recommended order: A -> **B (intermediate step; = the operator's ground-marker idea)** -> C. Do not go to C before B.

## Key Decisions Made This Session
| Decision | Choice | Rationale |
|----------|--------|-----------|
| Calibration source label | `chessboard` (not `calibrated`) | tag-map-v1 schema enum is `nominal`/`chessboard`; only `nominal` is refused |
| Tag size | `size_m` = 0.192 for all 6 tags | Printer scaled to ~96%; measured 19.2 cm. Distance scales linearly with it |
| Board | 7x6 inner corners (8x7 squares), 2.41 cm squares | Asymmetric pattern avoids orientation flips; `--square-m` measured (19.3 cm / 8) and required |
| Calibration acceptance | RMS gate is not enough; add tilt + split-half stability | A 0.349 px RMS calibration had fx 1222 vs 1163/1277 between halves (bad) |
| Test-frame origin | Shifted by (+50, +10) units | Contract forbids room x/y outside 0..100 (fail-closed) |
| Commits | Feature branch only, no push/merge | User request |

## Files Changed (branch vs master; key ones)
| File | Change Summary |
|------|---------------|
| `src/sentinel/pose/{detector,persistence,stream,tagmap,scoring,replay,preview}.py` | Detector/DB feed, stream harness, scoring, replay, live preview |
| `src/sentinel/contracts/pose.py` | `DronePoseReport`, rejects naive datetime, altitude < 0 |
| `src/sentinel/cli.py` | `pose-stream`, `pose-score` subcommands |
| `scripts/calibrate_camera.py` | Checkerboard calibration + quality gates |
| `calibration/tag-map-calibrated.json` | Committed calibrated tag map (tag positions still the original fixture) |
| `docs/superpowers/specs/contracts/fixtures/tag-map-v1.valid.json` | 6-tag fixture, `size_m` 0.192, `nominal` calibration |
| `docs/wp5-rehearsal-manifest.md` | Ground-truth protocol (update with Test B/C) |
| `tests/pose/*`, `tests/contracts/*`, `tests/integration/*`, `tests/test_cli_pose_stream.py` | TDD coverage |

## Critical Context
- **SR-063 (advisory containment): pose may inform containment checks and the existing abort policy, never command motion.** Test C measures the end position; using pose to *correct* flight (closed loop) would violate SR-063 and needs an explicit spec amendment/decision first.
- **Altitude:** `altitude_m` comes from the Tello `height?` reading (takeoff-relative, often 0.0, was briefly negative -> now dropped). The PnP camera z exists internally (`cam_pos[2]` in `detector._report_point`) but is **not** in the report; Test C's z target needs a decision (expose PnP z or use Tello height).
- **Tag placement error = pose error.** The 6-tag fixture positions were measured for the original alley; the physical test room must be re-measured (x, y, z, yaw, tag centre height) to +/-1 cm or Test B results are dominated by map error.
- **Calibration is trustworthy only where the board was seen:** corners spanned x 254-785 of 960; distortion terms (k3 = 2.8) are unconstrained near image left/right edges. Keep tags near the frame centre; check edge behaviour explicitly in Test B.
- **Printing:** printer scales to ~96% — always measure.
- `pose-score` heading truth is only meaningful if the drone yaw is set/measured; specify it per point.
- Live mode needs the Tello Wi-Fi + `--confirm`; `pose-stream` never takes off. Real flights (Test C) need the project's G3 owner approval and G4 live-rehearsal consent, supervised.
- Wire contract: `DRONE_POSE_REPORTED`, room x/y in [0,100] units (1 unit = 10 cm, `room_scale_m_per_unit` 0.1), heading [-180,180]. Pose is not yet consumed by any coordinator/read model (`latest_pose` unused outside tests) — WP1 boundary.
- Untracked, deliberately not committed: `.hermes/`, `tasks/`, `calib_frames/`, `calib_board*/`, `.factory` scratch files, `artifacts/` (git-ignored: `pose.db`, test maps, check DBs, `tag-map-calibrated.REJECTED.json` = the bad calibration, do not use).
- Recorded footage `calib_board/` (353 frames of tag 4) is useful for offline replay comparisons; `calib_board3/`+`calib_board4` are checkerboard sets.

## Open decisions (ask the operator)
1. Test B geometry: which room, how many floor markers (suggest 8-12), which distances (0.5/1/1.5/2 m) and view angles; is the physical layout the original 6-tag alley or a new one (needs re-measurement)?
2. Test C: open-loop (drone flies commanded moves, pose only measures error) vs closed-loop (needs SR-063 amendment). z source. Target set (start with 3-5 points).
3. Threshold ladder proposal: relaxed p95 <= 15 cm / 10 deg -> 10 cm / 5 deg -> 5 cm / 3 deg (agree or adjust). Minimum samples per point (suggest >= 30).

## Next Steps (in order)
1. **Improve `pose-score` output** (small, TDD): report signed bias (dx, dy in cm, heading) separately from spread; require a minimum sample count; per-run rows already exist. Reason: Test A showed median == p95 hides a systematic bias.
2. **Add guided multi-point capture** (new CLI, e.g. `pose-session`): reads a points file (name, x, y, heading_deg), for each point prompts the operator, records N seconds with a fresh uuid `run_id`, auto-writes `truth.json`, then scores and prints a per-point table (optionally a markdown report). Reuse `run_pose_stream`, `score_runs`, `Preview`.
3. **Prepare Test B physically:** re-measure tag map for the room; mark floor points; run with `calibration/tag-map-calibrated.json`-derived map; relaxed thresholds first; then analyze bias vs distance/angle/tag/image-position and cross-tag consistency; consider a systematic offset (lens-to-body) parameter.
4. **Before Test C:** brainstorm with the user (superpowers:brainstorming) the SR-063 question, z source, WP1 wiring of `latest_pose`, and the safety gates; then write a spec/plan and use the host-native `Workflow` tool (with the user's explicit opt-in) to implement.
5. Update `docs/wp5-rehearsal-manifest.md` with Test A result and the B/C protocols.

## Resume Command
```
cd C:\coding\sentinel\.worktrees\wp5-drone-pose
git log --oneline -5; uv run pytest -q          # expect 210 passed
```
Then say: "Resume from docs/handoffs/2026-09-20-wp5-pose-validation.md — start with next step 1."
