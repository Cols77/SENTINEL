# SENTINEL handoff

## State

- Branch: `docs/indoor-alley-v0-spec-rework` at `10bbe73` (`docs: close round-2 review residuals on the V0 contract set`), on top of `6aa8f84` (`docs: close the V0 wire-contract gaps found by peer review`), `8a90c5f` (`fix: bind V0 wire contract to six-zone snapshot and force-run discriminator`) and the base spec-rework commit `8ffa751`.
- This branch defines the reworked V0 specification set under `docs/superpowers/specs/` for the six-zone indoor-alley survey: the canonical V0 coherence spec, an approved indoor-alley survey design (the V0 decision record), WP0 shared contracts, and WP1–WP4 component specs, plus `docs/safety.md`.
- No push to `main` was performed. No real-drone or USB-adapter validation was claimed.

## V0 profile

- Fixed six-zone alley route (`LEFT_ALLEY_FRONT/MIDDLE/BACK`, `RIGHT_ALLEY_FRONT/MIDDLE/BACK`) in an indoor room safety envelope, with an operator-selected layout mode (marker mode default; `ROUTE_ONLY_2_ZONE` explicit fallback). A missing expected marker in marker mode is a failure.
- Immutable `SurveyPlanReference` + `approval_reference` + exact `PreflightReport` bound to dispatch; operator approves the exact plan version before `DRONE_DISPATCH_REQUESTED`.
- Provider gate: named joint Colin+Dzivha approval over a versioned, reproducible test-image-set hash (approval/review shared; execution of provider work is Dzivha's). No candidate list, no fixed 0.90 threshold.
- Terminal run outcomes: `COMPLETED`, `FAILED`, `EMERGENCY`; `NEEDS_REVIEW` is a review-hold escaped only by an approved new/revisit plan; `REJECTED` is event-only (reject → refine → re-propose).
- Preflight is exactly the 8 named checks (`WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`, `LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, `EMERGENCY_LATCH`); operator authorization is the plan's `approval_reference` evaluated at dispatch, not a preflight check.
- Real execution is DJITelloPy-only, fresh-process with fresh preflight and `--real --confirm`. The `EMERGENCY` latch is terminal and non-overridable; force-run after a failed preflight is operator-only `FORCE_RUN_CREATE` naming a new run/plan, never a resume, never under an active latch, always with fresh preflight and `--real --confirm`.

## Gate status (G0–G4)

- **G0 Contract** (Colin): pending — schema/fixture validation, DDL constraint manifest, contract tests.
- **G1 Coordinator simulation** (Colin + Alan): pending — deterministic fake-agent replay, duplicate and `IN_DOUBT` tests.
- **G2 Human surface** (Alan): pending — NiceGUI smoke fixture, click-through evidence, Confirm/Reject stale-version test.
- **G3 Perception** (execution Dzivha; approved/reviewed Colin + Dzivha): **BLOCKED** — only the `UNAPPROVED` template is checked in; requires a named joint approval over a versioned test-image-set hash plus reproducible evaluation-manifest evidence.
- **G4 Physical** (Colin): pending — supervised one-Tello rehearsal using WP0 operation/evidence contracts; cannot be substituted by dry-run success.

## Verification

From this branch:

```text
uv run python -m pytest -q
103 passed in 1.60s
```

The V0 contract root is `docs/superpowers/specs/contracts/sentinel-v0-contract.schema.json`, with the `oneOf` port registry at `sentinel-v0-wire.schema.json`. The live yaw-scan fixture is `fixtures/perception/live/yaw-scan-20260916.h264` (plus `.manifest.json`/`.mp4`).

## Hardware next step

Install the package on the target machine, connect the USB Wi-Fi adapter to the Tello SSID, verify the main adapter still owns the internet route, then run one supervised real test with `--confirm`. Use an explicit `--local-ip` for the USB adapter if automatic selection is ambiguous. Keep a second terminal ready with `python -m sentinel emergency --local-ip <usb-ip>`; pressing `E` in the run terminal sends the same terminal motor-stop command.

Each real run must be started as a fresh process because the pinned DJITelloPy receiver threads are process-global. Full flight details, force-run semantics and the terminal emergency latch are in `docs/safety.md`.

## Open follow-up (not done in this branch)

- Archiving superseded flight material under `docs/superpowers/specs/superseded/` remains an open task; no `superseded/` directory or files were created here.