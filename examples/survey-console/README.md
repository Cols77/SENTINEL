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

A bare Live host now starts its localhost UI in an explicit `NOT_READY`, unarmed state. Opening,
refreshing, polling, asking for status, and reading evidence do not connect to a drone or provider.
The readiness panel names missing CLI flags and environment-variable names, but never displays
secret values. Missing or invalid Live configuration never selects a Rehearsal fallback.

Start the inert UI first if configuration is not yet assembled:

```sh
uv run sentinel mission-control --mode live --data-dir artifacts/live-session
```

The panel's launch template is guidance, not evidence of hardware readiness. Restart Mission
Control after changing CLI configuration. A fully configured launch still remains unarmed:

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
  --i-authorise-paid-calls \
  --location lecture-room --consent-date YYYY-MM-DD \
  --local-ip 192.168.10.2 --surface-z-cm 50
```

Supply actual measured configuration. `--pose off` also requires `--start-x-cm` and `--start-y-cm`; both coordinates must lie inside the selected envelope. Reanchor retains the existing `--sr063-amended` gate. The provider profile must conform to `provider-profile.v1`, be approved, and match the selected provider/model. Moondream uses `MOONDREAM_API_KEY` and `MOONDREAM_MODEL`. Consent must already be recorded for that exact location/date in the survey database; the host does not record it on the operator's behalf. Configuration readiness enables proposal planning only; it does not arm the aircraft.

The Mission Control browser projection is schema v3: route coverage and return-leg facts, commanded/measured freshness, capture detection progression, public proposer provenance, and explicit COMPLETE/ABORTED/RECOVERY_REQUIRED terminal states are rendered from validated backend facts. Live detection starts in a separate supervised process before capture, binds cached results to run/sweep/path/hash identity, and never repeats a provider call during survey ingestion. Detector failure is an explicit analysis gap and does not take over flight control. Paid calls remain refused without `--i-authorise-paid-calls`.

Live uses two phases. Host composition creates only the persistent session, public projection, local planning facts, and inert/deferred boundaries. After an immutable proposal is explicitly approved, dispatch separately checks `--confirm`, initializes the independent Emergency transport, creates the drone client, and runs the bounded no-takeoff preflight. Detection is admitted separately and cannot start until provider configuration, `--i-authorise-paid-calls`, and recorded consent all pass. Approval is therefore distinct from flight confirmation, preflight, provider authorization, and consent.

The Live adapter uses `CubeFlight`, the measured Tello camera model, real provider perception, and the existing F rules proposer only after those gates admit the corresponding capability. Land is unavailable without an active runner; Emergency is unavailable until its independent transport is initialized. The bounded chat supports computer-count requests and status; when planning is unavailable, count requests return actionable refusal text without starting the worker. Pose evidence comes from the runner's real localization bursts, so it can honestly age between bursts. Restarted interrupted Live sessions require recovery and cannot dispatch again automatically. Start a new session only after the operator has reconciled the aircraft and previous run.

All hardware operation, measured calibration and actual landing/emergency outcomes remain subject to physical verification. Automated acceptance uses injected clients only.

## Verify

```sh
uv run pytest -q --ignore=fixtures
node --test tests/survey_console/*.test.mjs
uv run python scripts/ui_assets/check_assets.py
node scripts/ui_assets/verify_mission_control.cjs
```

The browser verifier starts and stops four real local backends with separate temporary data directories. It requires Playwright/Chromium in the tooling environment; no frontend build is needed. Acceptance covers bare-Live `NOT_READY` guidance and refusal, clean count, approved uncertain follow-up, missing analysis, approval reload/reconciliation, production survey/report/evidence, stale-to-fresh localization, deterministic browser speech, independent safety controls, 1440×900, 1920×1080 and narrow layouts, reduced motion, and local-only traffic. Artifacts go under ignored `artifacts/mission-control/`.

## Retained fixture regression surface

The original eight-scenario fixture demo remains isolated at `/examples/survey-console/` on a static repository server:

```sh
python3 -m http.server 8770 --bind 127.0.0.1
node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:8770
```

That surface is labelled FIXTURE DEMO and cannot dispatch a flight. Its reviewer controls exercise snapshots, uncertain/lower-bound counts, evidence races, and approval state transitions. It does not prove runtime integration. The legacy asset-kit study remains `/assets/sentinel-ui/console.html`.

See [the contract](contract.md), [approved design](../../docs/superpowers/specs/2026-09-21-sentinel-mission-control-design.md), and [execution handoff](../../docs/handoffs/2026-09-21-mission-control.md).
## Browser voice input

When the browser exposes `SpeechRecognition`, the mission composer provides an explicit hold-to-talk control. Recognition is English using the browser's English regional tag where available. The browser speech service may process audio remotely; Sentinel does not upload or store audio and never listens passively. Only the exact spoken phrase `Sentinel, land now` enters the visible three-second controlled-Land countdown. Other voice phrases use the bounded chat path, including a safe refusal for unsupported safety and flight requests. Text remains available when speech recognition is unsupported or permission is denied.
