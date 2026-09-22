# SENTINEL Mission Control

The local Mission Control host connects this vanilla browser UI to the production survey loop. Rehearsal and Live use the same application service, approval broker, snapshot contract and survey/report validation. Rehearsal injects synthetic camera, flight and perception boundaries; its results are computed by the runtime.

## Start a rehearsal

From the repository root, install the project dependencies (`uv sync`), then:

```sh
uv run python -m sentinel.ui --mode rehearsal --data-dir artifacts/my-rehearsal --port 8771
# Equivalent entry point:
uv run sentinel mission-control --mode rehearsal --data-dir artifacts/my-rehearsal
```

Open http://127.0.0.1:8771/console/, ask **count the computers**, inspect the proposal and explicitly approve or reject it. Text cannot authorize flight. Status questions remain available while approval waits. One host owns one session/run; use a fresh data directory for a new rehearsal. Do not run two hosts against the same session database.

The clean scenario yields one confirmed synthetic computer. Pass `--scenario fixtures/mission-control/uncertain-followup.json` for a separately approved follow-up, or `--scenario fixtures/mission-control/missing-analysis.json` for a provider gap. Missing analysis is visibly incomplete, never interpreted as a zero count. Rehearsal localization runs the real AprilTag estimator over scripted image inputs, including no-tag intervals that age and recover the last measured fix.

Land interrupts normal sequencing and requests controlled landing. Hold Emergency for 1.2 seconds to latch the session and all active runners, then use the independent transport. Both are simulated in Rehearsal. Acknowledgement is not proof of physical landing or motor stop.

## Live startup

Live refuses startup on a missing prerequisite; it cannot select a Rehearsal fallback. The following example describes configuration, not evidence of hardware readiness:

```sh
export GEMINI_API_KEY='...'
export GEMINI_MODEL='your-approved-model'
uv run sentinel mission-control --mode live --confirm \
  --data-dir artifacts/live-session \
  --survey-db /absolute/path/to/consented-survey.sqlite \
  --site /absolute/path/to/site.json \
  --tag-map /absolute/path/to/tag-map.json \
  --pose-db /absolute/path/to/pose.sqlite --pose passive \
  --detector gemini --provider-profile /absolute/path/to/approved-profile.json \
  --location lecture-room --consent-date YYYY-MM-DD \
  --local-ip 192.168.10.2 --surface-z-cm 50
```

Supply actual measured configuration. `--pose off` also requires `--start-x-cm` and `--start-y-cm`; both coordinates must lie inside the selected envelope. Reanchor retains the existing `--sr063-amended` gate. The provider profile must conform to `provider-profile.v1`, be approved, and match the selected provider/model. Moondream uses `MOONDREAM_API_KEY` and `MOONDREAM_MODEL`. Consent must already be recorded for that exact location/date in the survey database; the host does not record it on the operator's behalf.

The Mission Control browser projection is schema v3: route coverage and return-leg facts, commanded/measured freshness, capture detection progression, public proposer provenance, and explicit COMPLETE/ABORTED/RECOVERY_REQUIRED terminal states are rendered from validated backend facts.

Startup checks the selected independent UDP interface and existing no-takeoff preflight. Actual flight still needs an explicit proposal approval. The Live adapter uses `CubeFlight`, the measured Tello camera model, real provider perception, and the existing F rules proposer. The bounded chat currently supports computer-count requests and status; it clarifies unsupported intent. Pose evidence comes from the runner's real localization bursts, so it can honestly age between bursts. Restarted Live sessions require recovery and cannot dispatch again automatically. Start a new session only after the operator has reconciled the aircraft and previous run.

All hardware operation, measured calibration and actual landing/emergency outcomes remain subject to physical verification. Automated acceptance uses injected clients only.

## Verify

```sh
uv run pytest -q --ignore=fixtures
node --test tests/survey_console/*.test.mjs
uv run python scripts/ui_assets/check_assets.py
node scripts/ui_assets/verify_mission_control.cjs
```

The browser verifier starts and stops its own real local backend and temporary data directory. It requires Playwright/Chromium in the tooling environment; no frontend build is needed. Acceptance covers approval reload/reconciliation, production survey/report/evidence, stale-to-fresh localization, independent safety controls, three viewports, reduced motion, and local-only traffic. Artifacts go under ignored `artifacts/mission-control/`.

## Retained fixture regression surface

The original eight-scenario fixture demo remains isolated at `/examples/survey-console/` on a static repository server:

```sh
python3 -m http.server 8770 --bind 127.0.0.1
node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:8770
```

That surface is labelled FIXTURE DEMO and cannot dispatch a flight. Its reviewer controls exercise snapshots, uncertain/lower-bound counts, evidence races, and approval state transitions. It does not prove runtime integration. The legacy asset-kit study remains `/assets/sentinel-ui/console.html`.

See [the contract](contract.md), [approved design](../../docs/superpowers/specs/2026-09-21-sentinel-mission-control-design.md), and [execution handoff](../../docs/handoffs/2026-09-21-mission-control.md).
### Browser voice input

When the browser exposes `SpeechRecognition`, the mission composer provides an explicit hold-to-talk control. Recognition is English using the browser's English regional tag where available. The browser speech service may process audio remotely; Sentinel does not upload or store audio and never listens passively. Only the exact spoken phrase `Sentinel, land now` enters the visible three-second controlled-Land countdown. Other voice phrases use the bounded chat path, including a safe refusal for unsupported safety and flight requests. Text remains available when speech recognition is unsupported or permission is denied.
