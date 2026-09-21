# Plan — live survey: WP5 fixes, flight adapter, provider adapters, Dzivha bridge

## Context

`integration/survey-demo` (`608ff7c`) has a survey/counting package that only runs on replayed,
synthetic data. Nothing connects it to a real flight or a real detector, and today's coherence
pass left five owed items. The video is due 2026-09-25, the live demo 2026-09-27.

What is missing, and why each matters:

1. **No real flight path into the survey** (plan Task 11). Amendments V12/V13 bind it: measured
   camera model, and every frame stamped with the freshest measured position *and heading*.
2. **W08 is only half built.** The requirement now says position is checked "at every localised
   stop and during moves whenever tags are in view"; only the stop-time check exists.
3. **`sentinel/cube/observe.py` uses the wrong field of view** (70.2° × 55.6°; measured 56.5° ×
   43.9°), so "back-and-wide" stops stand ~30 % too close.
4. **The Moondream and Gemini adapters cannot work**: neither sends the image, the Moondream URL
   is invented, response shapes are unverified. Nothing references them; the CLI only builds
   `ReplayDetector`.
5. **Dzivha's detector is not reachable from the survey.** `fixtures/perception/` is a separate
   uv project (torch/CUDA, Grounding DINO + depth, built around a yaw scan). Its
   `config.CameraConfig` carries the same wrong FOV.

Decisions already taken with the operator: Dzivha's detector plugs in through an **offline
bridge** (no change to her code, no torch in the main env); multiple detectors combine as
**primary + fallback** (no box fusion); execution is **this session via a Workflow**.

## Branch and ownership

- Worktree `.worktrees/survey-counting`, branch `feat/survey-counting`, fast-forwarded to
  `integration/survey-demo` (`608ff7c`) and re-synced with `uv sync --all-extras`. (A separate
  worktree was the first intent; the session's worktree isolation makes agents unable to write
  outside this one.)
- WP5 manual tests are in progress in `.worktrees/wp5-drone-pose` (operator, 2026-09-21). Phase 1
  edits `cube/mission.py` and `cube/observe.py` additively; re-check that branch for new commits
  before merging back.
- Never touched: `coordinator/*` files Codex owns, `src/sentinel/cli.py`, `command_gateway.py`,
  anything under `fixtures/perception/` (Dzivha's). No existing test is edited to make it pass.
- Merge back into `integration/survey-demo` and push only on the operator's word.

## Workstreams

### A. Shared camera model and WP5 fixes

- **A1. One camera module.** Move `CameraModel`, `SYNTHETIC_LEVEL`, `TELLO_MEASURED`, `CAMERAS`
  from `src/sentinel/survey/observability.py` to a new neutral `src/sentinel/camera.py`;
  `observability` re-exports them (no caller changes). Reason: `sentinel.cube` must not import
  `sentinel.survey` (the dependency runs the other way by design).
- **A2. `src/sentinel/cube/observe.py`**: derive `WIDTH_PER_RANGE` and `BELOW_HORIZON_DEG` from
  `camera.TELLO_MEASURED` (1.08 and ~31°) instead of the literals. Its tests pin the old numbers;
  they are updated with the premise, and listed in the commit. Extend
  `tests/survey/test_camera_model.py` (or a sibling under `tests/cube/`) so observe's constants
  are checked against the calibration artifact, same pattern as today.
- **A3. Mission log carries what the survey needs** (`src/sentinel/cube/mission.py`): `_look`
  records each captured frame (`path`, `sha256`, `look_deg`, `captured_at`) into the waypoint's
  log entry, and `_localise` records the measured `heading_deg` (today only `heading_error_deg`).
  Additive; existing log consumers unaffected.
- **A4. In-move watchdog (W08-b, level 2)** — new `src/sentinel/pose/watchdog.py`, wired in
  `CubeMissionRunner` by overriding `move_checked`: while a move executes (modes `passive` and
  `reanchor` only), a thread reads frames from the already-open stream, runs the existing
  detector (`sentinel.pose.detector`) and `check_containment` (`sentinel.pose.containment`).
  `OUTSIDE` on **3 consecutive fixes** (debounce: in-motion accuracy is unmeasured) sets an abort
  event; the runner raises `ContainmentAbort` as soon as the move returns, which ends in the
  existing controlled landing. No tag in view → no fix → no action.
  Sending `land` *during* the blocking `move` stays behind a constructor flag, default **off**,
  until a motors-on bench test shows the Tello honours it (precedent for a cross-thread command:
  `runner.trigger_emergency`). Tests: `DryRunClient` plus an injected frame/fix source, covering
  trip, debounce, no-tags, and modes `off`. W08 stays "not satisfied" in the requirement until the
  bench test; the requirement file is updated to say which half now exists.

### B. Survey flight adapter (plan Task 11, V12 + V13)

New `src/sentinel/survey/cube_bridge.py` — the only `sentinel.cube` importer in the package:

- `envelope_from_site(site) -> SweepEnvelope`, `mission_from_option(option, ...) -> Mission`
  (reuse `sentinel.cube.site`, `sentinel.cube.mission.Mission/Waypoint`).
- `CubeFlight(FlightPort)`: runs `CubeMissionRunner` (pose mode from the caller; `reanchor`
  requires the same explicit acknowledgement the cube CLI demands), then turns the mission log
  (A3) into `FlightFrame`s: measured `x_cm`/`y_cm`/`heading_deg` and
  `position_kind="MEASURED"` when the stop was `LOCALISED`, dead-reckoned values and
  `"COMMANDED"` otherwise; `z_cm` from the height reading.
- Replace the single `loop.DELTA_Z_CM` with a per-frame value: `frame.z_cm − surface_z_cm`
  (tape-measured, passed in). `FlightFrame` gains an optional `delta_z_cm`; replay frames without
  it keep 55.0, so Codex's harness (`scripts/survey_sweep.py`, which asserts on `DELTA_Z_CM`) is
  unaffected.
- The adapter fixes the camera: `CubeFlight` runs always use `TELLO_MEASURED` for both
  `generate_options` and `run_survey`.
- `scripts/survey_run.py`: `--site`, `--pose {off,passive,reanchor}`, `--surface-z-cm`,
  `--real --confirm` (mirrors the cube CLI's safety flags; default remains replay). The summary
  reports `MEASURED` vs `COMMANDED` frame counts.
- Tests run the whole path against `DryRunClient` with an injected burst function; no hardware.

### C. Provider adapters that actually work, and primary + fallback

- **C1. Verify the real APIs first** from the official Moondream and Gemini documentation
  (read-only web fetch, no API call): endpoint, auth header, image encoding, request fields,
  response shape, model identifier. Record what was found in the module docstrings with the date.
- **C2. `providers/moondream.py`, `providers/gemini.py`**: send the image (base64), Moondream one
  request per class (its detect takes one object), map the real response shapes into the existing
  `normalise` → `to_detection_batch` path in `providers/__init__.py`. Image bytes never logged.
  The missing test that would have caught the defect: the fake session asserts the request
  payload decodes back to the file's exact bytes.
- **C3. `FallbackDetector(primary, fallback)`** in `src/sentinel/survey/detector.py`: fallback is
  tried only on `ProviderError`/timeout or an `ANALYSIS_FAILED` batch; the batch records which
  provider answered (stored with the sighting's raw response, already persisted).
- **C4. CLI**: `--detector {replay,moondream,gemini}` and `--fallback {none,moondream,gemini}`;
  keys read through the existing `src/sentinel/env_file.py`. A provider detector refuses to start
  unless `--location/--date` filming consent is recorded (`survey/consent.py`, already enforced in
  `run_survey`) — no frame leaves the machine without it.

### D. Dzivha's detector through an offline bridge

- New `scripts/survey_detect_gdino.py`, run with
  `uv run --project fixtures/perception python scripts/survey_detect_gdino.py --frames <dir> --out <responses-dir>`.
  It imports, read-only, `sentinel_perception.detection` (`GroundingDinoDetector`,
  `detect_multipass`, `clean`, `deduplicate`) and `models.load_manifest`, runs the survey's
  class phrases over a frames directory, and writes one response JSON per frame in the format
  `ReplayDetector` already reads. The survey then runs on them with `--detector replay`.
- Scores are recorded as `score_kind: DETECTOR_RAW` — the first honest use of that kind in the
  survey. `normalise` gains a branch for numeric raw scores: band thresholds are **provisional**
  constants, named as such, to be pinned by the spike (E); until then a `DETECTOR_RAW` sighting
  below the upper threshold cannot confirm an object.
- Her depth model, tracker and yaw-scan choreography are **not** used: the survey has its own
  ground-plane geometry and non-rotating rail. Overlap is documented, not merged.
- Handoff `docs/handoffs/2026-09-21-HANDOFF-to-dzivha-camera-and-survey.md`: her
  `config.CameraConfig` (70.2° × 55.6°) and `depth.Intrinsics.from_fov` contradict the measured
  calibration (coherence spec §10.5); what the bridge expects from her package; the WP3 exception
  she must see before any provider profile is approved.
- Needs her model weights present (`scripts/probe_models.py` downloads them from Hugging Face —
  free, but a multi-GB network download; the workflow does **not** run it).

### E. Provider comparison spike — prepared, not run

`scripts/survey_provider_spike.py`: decodes the committed `fixtures/perception/live/yaw-scan-20260916.h264`
to stills, runs each available detector, writes raw responses and a comparison table. It prints
the exact call count and estimated cost and stops unless `--i-authorise-paid-calls` is passed.
**Gated:** paid calls (~125) and API keys are the operator's; garden stills need filming consent.

### F. Coherence

- Survey design rev 6 (§6 providers, fallback, DETECTOR_RAW bands, per-frame Δz) and plan
  amendments V14+; consent record C-20f… for the contract/schema edits (`FlightFrame.delta_z_cm`,
  provider field) under the standing C-20.
- `.factory/contracts.yaml` consumers updated; `coherence trace check`, `mirrors check`,
  `navigate freshness` run from the pinned upstream worktree and must still build.
- W08 note and coherence spec §10.3/§10.5 updated to what now exists (observe fixed; watchdog
  built, bench test pending).

## Execution — Workflow (Claude Code `Workflow` tool)

Five phases, each **dev → review → fix** on Sonnet, each gated on its own tests plus the full
suite before the next begins (≈ 15 agents in total, sequential because the phases share files):

| Phase | Content | Gate |
|---|---|---|
| 1 | A1–A4 | `tests/cube tests/pose tests/survey` green |
| 2 | B | `tests/survey` + `tests/test_survey_eval.py tests/test_survey_sweep.py` green, unmodified |
| 3 | C1–C4 | `tests/survey/test_providers.py`, CLI tests; payload-bytes test present |
| 4 | D, E | bridge unit-tested with a fake detector; spike script refuses without the flag |
| 5 | F | full suite `rtk proxy uv run pytest -q --ignore=fixtures`; coherence tooling builds |

Agents are told the environment traps: `rtk proxy` for pytest, `uv sync --all-extras` in a fresh
worktree, no heredocs with apostrophes, files they may not open.

## Not done by this plan (operator gates)

Any real flight including the watchdog bench test and the first `reanchor` flight (G4 on the
day); any paid provider call (spike); downloading Dzivha's model weights; recording filming
consent; approving a provider profile (joint Colin + Dzivha); merging to `master`.

## Verification

1. Full suite green in `.worktrees/survey-live`; Codex's survey-eval tests pass unmodified.
2. Dry-run end to end: `scripts/survey_run.py --site <site.json> --pose passive --camera
   tello-measured …` against `DryRunClient` produces a report whose frames are all `COMMANDED`
   (dry-run frames are undecodable by design) and whose summary says so.
3. Replay end to end with bridge-format responses: a responses dir written by the bridge's fake
   detector is consumed by `--detector replay` and yields `DETECTOR_RAW` sightings.
4. Provider adapters: with a fake session, the payload decodes to the image's bytes; with a
   failing primary, `FallbackDetector` answers from the fallback and the sighting says which.
5. `cube/observe.py` candidates for a 1.2 m alley mouth now stand at ≥ 1.11 m, not 0.85 m.
6. Coherence: `trace check` builds, `mirrors check` passes, `test_camera_model` still pins the
   calibration.
