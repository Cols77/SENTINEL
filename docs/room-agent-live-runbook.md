# Room-agent live runbook (Plan 06 Task 4, count-and-return scope)

This is the operator runbook for `scripts/room_session.py` and
`scripts/room_verify_artifact.py`. It covers the count-and-return use case
only (Plan 05 tracker/following is deferred); the CLI's built-in help
(`--help` on any subcommand) is the source of truth for exact flag names.

## Scope of this build

`scripts/room_session.py live` runs one bounded count-and-return session
per invocation, with no per-action prompts. It checks manifest
authorization, capability admission, matching calibration profiles,
provider/filming consent, site assurance and the flight calibration
evidence. Then it runs a motors-off connectivity preflight and composes the
session (`sentinel.room.live_session.run_count_return`):

1. Jev and the detector are constructed, and their reported identities are
   recorded (`IDENTITY_RESOLVED`).
2. A `LocalizationTracker` and `ReserveGuard` are built from the measured
   `--drift-profile`/`--energy-profile` files, and a REAL-mode
   `SafetySupervisor` from those plus `--command-limits`.
3. The flight stack comes from `client_factory(tracker=, clock=, site=)`.
4. The verified site assurances are adopted into the supervisor's clock
   domain.
5. The goal ledger is compiled (`GoalCompiler`, recorded as
   `GOAL_LEDGER_ACCEPTED`).
6. Connectivity freshness is re-checked (`TAKEOFF_AUTHORIZED`), then
   `SafetySupervisor.start` takes off.
7. `RoomController.run_until_terminal` runs the session, which ends with the
   supervisor's safe return and landing.
8. `terminal.json`/`summary.json` are finalized.

The Python-level hooks `client_factory`/`jev_factory`/`detector_factory`
and the two connectivity probes are how real hardware and models are
supplied. All five default to the real adapters in `sentinel.room.live_stack`:
a motors-off telemetry probe, an OpenRouter reachability probe, a flight
stack that shares the probe's one Tello connection (Task A), the live Jev
judge (`JevJudge` over OpenRouter) and the `gemini-openrouter` detector
(Task B). They are built only after every gate passes. No test in this
repository reaches hardware or a provider: the tests replace the adapters'
transports with fakes.

**What a REAL-mode session can do.** `RoomController` offers OBSERVE
checkpoints in REAL mode only when the supervisor validated a `count_return`
grant that is PROMOTED, or TRIAL inside its acceptance run (Task B item 2).
Under such a grant a count flies the site's declared regions, and a
"find the phone" request searches them (item 2b). Jev sees the localization
health each turn and may choose an early, bounded `REANCHOR` (item 2c).
Every observation is still admitted, or refused, by the `SafetySupervisor`;
see "What L3 will do with the history profiles" below.

Every live invocation (including `--check-connectivity-only`) requires
`--run-root`: a `sentinel.room.artifacts.RunArtifacts` directory is opened
there under `manifest.run_id` before the connectivity preflight runs, and
the preflight's full result (both paths' readiness, identities,
`checked_at` timestamps and errors) is recorded as a durable
`CONNECTIVITY_CHECKED` event -- never only printed to stderr -- so it
survives the process and can be read back later (e.g. by
`room_verify_artifact.py --require-event CONNECTIVITY_CHECKED`, once a run
finalizes). Freshness is re-checked against that same recorded preflight
immediately before `SafetySupervisor.start`, after supervisor bootstrap and
goal compilation. A preflight that has since expired refuses takeoff with
`connectivity preflight expired before takeoff` (recorded as
`CONNECTIVITY_EXPIRED_BEFORE_TAKEOFF`). A fresh one is recorded as
`TAKEOFF_AUTHORIZED` with both identities and timestamps.

The run's store session is created with the standard request text and the
grant registry's flight-capable grants for this manifest's capability,
site and aircraft. `SafetySupervisor` reads its grants there. Reopening a
run with a different grant binding refuses, so a registry change after a
connectivity diagnostic needs a new `run_id`.

## The count-and-return request

Every example below uses the operator's standard count-and-return request
text:

```
count the computers and phones in the surveyed room, report uncertain
detections as uncertain, then return home
```

This is `room_session.py preview`'s `--request-text` default.

## Capabilities this build offers

`--capability` accepts:

- `connectivity` -- the motors-off dual-path diagnostic only.
- `adaptive_movement` -- checkpoint-level adaptive flight (Plan 06 Task 3's
  pose-envelope/battery-bound gating).
- `count_return` -- the count-and-return mission itself.

`closer_inspection` (Plan 03 Task 3), `certified_landing` and `following`
(Plan 05) are named but refused as unavailable in this build -- asking for
one gets an explicit "not available" refusal, not a generic argparse error.

## L0: motors-off connectivity check

L0 proves that the laptop can read fresh telemetry from the real Tello and
reach the perception provider, at the same time, with the motors off. It
sends the drone `command` (SDK mode), `height?` and `sdk?`, and reads the
battery from the Tello's state stream. It never sends `takeoff`, `motoron`
or an `rc` command, and it closes the connection before exiting. The
provider half makes no paid call and sends no frame.

L0 flies nothing, so it needs no capability grant, calibration profile, site
assurance or tag map. Its manifest is bound to the CanningSt site
`fixtures/room_agent/v1/canningst-site.json` (preview reads the site and tag
map hashes from it), the same site L3 flies.

### Prerequisites

1. **Drone:** the Tello is powered on and sitting on the pad, with at least
   one bar of battery. The motors stay off. Keep hands clear anyway.
2. **Network:** one laptop adapter joined to the Tello Wi-Fi (`TELLO-...`),
   and internet on a second adapter (Ethernet or USB tether) that holds the
   default route. `sentinel.network` refuses a Tello adapter that owns the
   default route. It picks the one reachable Tello adapter itself; with
   several, add `--local-ip <laptop address on the Tello Wi-Fi>` to the live
   command.
3. **Provider key:** `OPENROUTER_API_KEY` (the key) and
   `GEMINI_OPENROUTER_MODEL` (the Gemini model the live detector uses, the
   same pair `scripts/survey_run.py --detector gemini-openrouter` reads), set
   in the environment or in the nearest `.env` file. An explicit environment
   value wins over `.env`.
4. **Filming consent** for location `CanningSt` on today's date, recorded by
   you in `runs/consent.sqlite3` (step 1 below). Record it only when
   everyone who may appear in frame today has agreed.
5. **Shell:** PowerShell at this checkout's root, with the project venv
   active (`C:/coding/sentinel/.venv/Scripts/Activate.ps1`). The venv's
   installed `sentinel` is the main checkout (`C:/coding/sentinel/src`), so
   `PYTHONPATH` must put this checkout's `src` first.

**Provider reachability** means: an HTTPS `GET` to
`https://openrouter.ai/api/v1/key` with the key as a bearer token answers
HTTP 200. That proves the internet route works and OpenRouter accepts the
key. The call is free, and neither the key nor its reply is printed or
recorded. The reported identity `openrouter:<model>` names the configured
model. The call does not check that the model exists.

### Sequence

Session variables (a fresh run id each time you preview):

```powershell
$env:PYTHONPATH = "src;."
$RUN = "l0-$(Get-Date -Format yyyyMMdd-HHmmss)"
$TODAY = Get-Date -Format yyyy-MM-dd
New-Item -ItemType Directory -Force runs | Out-Null
```

1. Record today's filming consent (your own record):

```powershell
python -c "from sentinel.survey.store import SurveyStore; from sentinel.survey.consent import record_consent; s = SurveyStore.open('runs/consent.sqlite3'); record_consent(s, 'CanningSt', '$TODAY', 'operator'); s.connection.close()"
```

2. Create the grant registry (once; L0 admits no grant, but live binds the
   run to the registry):

```powershell
python scripts/room_capability.py init --registry runs/registry.json
```

   It prints the empty registry's digest. On a later day it refuses with
   `refused: runs\registry.json already exists`: that is fine, the existing
   registry is reused.

3. Preview the L0 manifest. L0 binds no grant, camera, aircraft or model, so
   those fields carry zero hashes and `UNVERIFIED`, and the bounds are the
   smallest legal ones:

```powershell
python scripts/room_session.py preview --target-mode live --run-id $RUN `
  --capability connectivity --grant-registry runs/registry.json `
  --capability-grant-sha256 0000000000000000000000000000000000000000000000000000000000000000 `
  --capability-grant-state DISABLED `
  --code-revision "$(git rev-parse HEAD)" --integration-revision "$(git rev-parse HEAD)" `
  --request-revision l0 --goal-parser-revision l0 `
  --site fixtures/room_agent/v1/canningst-site.json `
  --camera-sha256 0000000000000000000000000000000000000000000000000000000000000000 `
  --aircraft-id UNVERIFIED --firmware-version UNVERIFIED `
  --requested-jev-identity UNVERIFIED --requested-detector-identity UNVERIFIED `
  --max-duration-s 60 --max-distance-cm 1 --max-recovery-attempts 0 `
  --consent-reference "CanningSt/$TODAY" --manifest-out runs/$RUN.manifest.json
$DIGEST = (Get-Content runs/$RUN.manifest.json -Raw | ConvertFrom-Json).authorization_digest
```

   Expected: the canonical manifest JSON on stdout, and on stderr
   `missing gate: capability admission: ...` (L0 has no grant; live's
   connectivity-only check does not need one).

4. Run the motors-off connectivity check:

```powershell
python scripts/room_session.py live --execute `
  --manifest runs/$RUN.manifest.json --authorization-digest $DIGEST `
  --capability connectivity --grant-registry runs/registry.json `
  --consent-db runs/consent.sqlite3 --location CanningSt --date $TODAY --i-authorise-paid-calls `
  --run-root runs --connectivity-timeout-s 20 --connectivity-max-age-s 30 --check-connectivity-only
```

   `--i-authorise-paid-calls` is the consent gate's flag; L0 itself makes no
   paid call. The 20 s timeout covers adapter selection (a 1 s `command`
   probe per candidate adapter) and DJITelloPy's connect (up to 7 s per
   attempt). The 30 s maximum age keeps the quick provider answer fresh while
   the drone path finishes.

   Expected on success (exit code 0): on stderr

   ```
   drone_identity=tello 192.168.10.1 sdk=<reply> battery=<n>% height=0cm drone_checked_at=<t> provider_identity=openrouter:<model> provider_checked_at=<t>
   ```

   and on stdout exactly

   ```
   CONNECTIVITY_READY
   ```

   `<reply>` is normalized from the Tello's answer to `sdk?`: SDK 1.3 does
   not define that read, so an `unknown command: sdk?` reply -- which still
   proves a live SDK link -- is recorded as `sdk=unsupported`. A reply that
   never actually arrived (DJITelloPy's own `Aborting command ...` timeout
   text) is refused, not recorded as a link. The preflight is recorded as a
   `CONNECTIVITY_CHECKED` event in `runs/$RUN/session.sqlite3`.

5. Verify the run artifact:

```powershell
python scripts/room_verify_artifact.py --manifest runs/$RUN/manifest.json --connectivity-only --expect-capability connectivity
```

   Expected: the run id on stdout, exit code 0. `--connectivity-only`
   checks the manifest digest and run directory, re-hashes every durable
   event, and requires the run's latest `CONNECTIVITY_CHECKED` event to be a
   ready `--check-connectivity-only` preflight. An L0 run never writes
   `terminal.json`.

### When L0 refuses

Every refusal exits 2 and prints `refused: ...` on stderr.

| Message | Meaning | What to do |
|---|---|---|
| `live detection makes paid calls: pass --i-authorise-paid-calls` | The flag is missing. | Add it. |
| `live detection sends frames to a provider: give --consent-db, --location and --date ...` / `no consent database at ...` | The consent flags or database are missing. | Run step 1, then pass all three flags. |
| `no filming consent recorded for CanningSt on <date>; ...` | No consent record for this location and date. | Run step 1 today, only if everyone in frame agreed. |
| `authorization_digest does not match the manifest bound at preview time` | `$DIGEST` is not this manifest's digest (for example a new PowerShell window, or a re-run preview). | Re-run the `$DIGEST = ...` line for the same `$RUN`. |
| `--capability does not match the manifest's requested_capability` | The live command's `--capability` differs from the preview's. | Use `connectivity` in both. |
| `grant registry could not be loaded: ...` | `runs/registry.json` is missing or damaged. | Run step 2. |
| `run artifact could not be opened: ...` | `runs/$RUN` already holds a different manifest. | Preview again with a fresh `$RUN`. |
| `connectivity preflight not ready (drone_ready=False, ...)` | The drone path failed. `drone_error` says why (rows below). | Fix that cause, then re-run step 4. |
| `drone_error=NoTelloAdapter: ...` | No laptop adapter is on the Tello Wi-Fi without owning the default route. | Join the Tello Wi-Fi, keep internet on the other adapter, or pass `--local-ip`. |
| `drone_error=AmbiguousAdapter: ...` | More than one adapter reaches a Tello. | Pass `--local-ip <address on the Tello Wi-Fi>`. |
| `drone_error=RouteDiscoveryError: ...` | Windows reports no usable internet default route. | Connect the internet adapter. |
| `drone_error=TimeoutError: probe did not answer within timeout_s` | The drone did not answer in time: off, asleep, or out of range. | Power-cycle the Tello, rejoin its Wi-Fi, re-run. |
| `drone_error=LiveStackError: invalid battery telemetry: ...` (or height / SDK reply) | The Tello answered with a malformed reading. | Power-cycle the Tello and re-run. If it repeats, do not fly. |
| `drone_error=LiveStackError: the drone never answered sdk?: ...` | DJITelloPy's own timeout text ("Aborting command ..."), not an aircraft reply -- `sdk?` never actually arrived. | Power-cycle the Tello and re-run. If it repeats, do not fly. |
| `connectivity preflight not ready (..., provider_ready=False, ...)` | The provider path failed. `provider_error` says why (rows below). | Fix that cause, then re-run step 4. |
| `provider_error=LiveStackError: provider reachability needs OPENROUTER_API_KEY to be set` (or `GEMINI_OPENROUTER_MODEL`) | The key or model is not configured. | Set it in the environment or `.env`. |
| `provider_error=LiveStackError: OpenRouter did not accept OPENROUTER_API_KEY: HTTP <status>` | OpenRouter answered but refused the key (401: invalid key; 402: no credit). | Fix the key or the account. |
| `provider_error=ConnectError: ...` / `ConnectTimeout: ...` | No internet route to OpenRouter. | Check the internet adapter. |
| `drone_error=stale telemetry` / `provider_error=stale reachability check` | That path answered, but its answer was older than `--connectivity-max-age-s` when both paths settled. | Re-run. If it repeats, raise `--connectivity-max-age-s`. |

`room_verify_artifact.py --connectivity-only` refuses with
`the run's latest connectivity preflight was not ready (...)` when the last
L0 attempt on this run failed (re-run step 4 until it prints
`CONNECTIVITY_READY`), and with `run has no CONNECTIVITY_CHECKED event` when
step 4 never reached the preflight.

## L3 preparation: history profiles and one TRIAL grant

There are no L1 calibration flights. The flight profiles come from the
recorded CanningSt flights (`scripts/room_profiles_from_history.py`, Task B
item 6), and one `count_return` TRIAL grant binds all of them and the site.

### The profiles

`fixtures/room_agent/v1/profiles/` holds what the script derived from the
WP5 trials in `C:/coding/sentinel/.worktrees/integration/artifacts/trials/`
(read-only):

| File | What it is |
|---|---|
| `canningst-flight-profile.json` | `room-calibration-profile-v1`, kind `flight`, state `CALIBRATION` (legal only under a TRIAL grant), speeds 20/50 cm/s, the p90 whole-flight battery burn, the sampled pose-drift envelope |
| `canningst-energy.json` | `EnergyProfile`, `execution_scope: CALIBRATION` (the supervisor admits it in REAL mode only under a TRIAL acceptance grant), aircraft `tello-canningst`, **0.288 %/s** |
| `canningst-drift.json` | `pose-drift-v1`: `xy_drift_per_cm` **0.157** (the robust statistic, declared in the policy), measured `heading_drift_per_cm` 0.022 and `max_height_error_cm` 20; every other limit is declared in `canningst-policy.json` (hover drift `xy_drift_cm_s` **0.05 cm/s**, an operator judgment, see below) |
| `canningst-command-limits.json` | `trim_rounds: 0`, `localise: false` (the supervisor refuses conditional trims) |
| `canningst-policy.json` | the declared margins and limits the recordings cannot measure, the excluded run and its reason, and the robust-drift rule |

How the numbers are derived (Task B2):

- **Drift.** Per leg: the open-loop arrival error divided by the commanded
  path flown since the last accepted tag fix (|dx| + |dy| + |dz|, exactly
  what the tracker charges and what a fix resets). The run
  `20260923-122157-reanchor` is excluded, with its reason in the policy:
  it took off at 51 % (42 % after takeoff) and 76-77 C, below the 50 %
  floor, and ended on the Tello's own auto-land at 10 %; its first leg is
  the old 0.45 cm/cm figure. Over the 7 nominal legs of the two other
  flights the p90 is 0.131 cm/cm; with a 20 % margin **0.157**, declared as
  `declared_overrides.xy_drift_per_cm` (named in the drift profile's
  evidence, next to `excluded-run:20260923-122157-reanchor`).
- **Energy.** Whole flights: first post-takeoff battery reading to the
  last, over the time from the first command after takeoff to `land`, plus
  one 1 % reading step; windows under 30 s are dropped (a 1 % step over a
  few seconds is quantization, not a burn). p90 across the flights
  (0.273 and 0.222 %/s) is 0.268, + 0.02 margin = **0.288 %/s**. In flight
  the reserve guard no longer learns a rate from a single 1 % step between
  two samples less than 10 s apart.
- **Hover drift (judgment, Task B2 fix 1).** `drift_limits.xy_drift_cm_s`
  is **0.05 cm/s** (it was 0.1; neither is a measurement: the stop bursts
  last about 2 s, too short to measure a hover rate). The robust per-leg
  statistic already bounds every nominal leg's error, hover time inside the
  leg included, and the tracker charges the hover rate again over every
  command's worst-case bound. At 0.1 cm/s no lateral region is admissible;
  the reason is in the policy's `declared_notes`.

To re-derive them (read-only on the history; same bytes as committed):

```powershell
python scripts/room_profiles_from_history.py --trials-root C:/coding/sentinel/.worktrees/integration/artifacts/trials --site fixtures/room_agent/v1/canningst-site.json --policy fixtures/room_agent/v1/profiles/canningst-policy.json --out-dir fixtures/room_agent/v1/profiles
```

It prints the profile hash and the measurements; a missing measurement or
declaration refuses naming it.

### What L3 will do with the history profiles

Read this before flying. With the committed profiles and site, offline
(`tests/room/test_canningst_site.py` for the admission of each region,
`tests/room/test_canningst_mission.py` for the whole mission on the fake
stack):

- takeoff is admitted from about **45 %** battery (refused at 42 %);
- the three regions stand **20 cm apart at the takeoff height**: left
  (230, 183), center (250, 183), right (270, 183), z 100 cm. The site's
  semantic envelope is the `observation` volume (x 220-280, y 160-300,
  z 50-140) inside the cube; the runner also refuses any commanded position
  outside it;
- at 90 % the count mission observes **all three** (frame captured,
  detector run, detections grounded on the demo surface), answers the count,
  returns over the pad and lands. The legs are `left 20`, `right 40`,
  `left 20`;
- the center needs about **55 %**, left/right about **60 %** (the
  reservation: 35 % center, 40 % left/right).

Why not the WP5 inset corners (x 190/310, z 120) any more: over the whole
cube the left/right out-and-back ends at the pad, 30 cm from the cube's rear
face. 160 cm without a fix at 0.157 cm/cm is 25 cm of uncertainty, more than
the 18 cm the 12 cm rotation radius leaves, even with zero hover drift. The
supervisor forecasts the out-and-back without assuming a tag fix, so it
refuses them. The absurd drift (the excluded 0.45 cm/cm) still refuses left
and right; the center is a hover over the pad and stays admitted.

To widen the regions again, the operator can fly a site whose start pad is
further from the cube faces (the 2026-09-22 flights flew the larger
`wp5-flight3-site.json` cube, y from 100 cm; `canningst-site.json` binds the
Mission Control registry's `wp5-flight1-site.json`, y from 160 cm).

A tag loss mid-mission is recovered, not landed: the supervisor's recovery
first tries a stationary burst, then yaws to the mapped-tag heading (90 deg,
tags 0 and 2), accepts the fix there (`POSE_REANCHORED`) and the mission
resumes; a second loss in the same flight is recovered the same way.

### Issue the TRIAL grant (b)

Session variables for L3 (a fresh `$RUN` per trial; the grant is bound to it):

```powershell
$env:PYTHONPATH = "src;."
$RUN = "l3-$(Get-Date -Format yyyyMMdd-HHmmss)"
$TODAY = Get-Date -Format yyyy-MM-dd
$EXPIRES = (Get-Date).ToUniversalTime().AddHours(6).ToString("yyyy-MM-ddTHH:mm:ssZ")
```

Issue one `count_return` TRIAL grant bound to the four profile files and the
site (registry from L0 step 2):

```powershell
python scripts/room_capability.py issue-trial --capability count_return --trial-protocol l3-canningst `
  --aircraft-id tello-canningst --evidence-destination runs/$RUN --expires-at $EXPIRES `
  --session-id $RUN --registry runs/registry.json `
  --site fixtures/room_agent/v1/canningst-site.json `
  --drift-profile fixtures/room_agent/v1/profiles/canningst-drift.json `
  --energy-profile fixtures/room_agent/v1/profiles/canningst-energy.json `
  --command-limits fixtures/room_agent/v1/profiles/canningst-command-limits.json `
  --profile-file fixtures/room_agent/v1/profiles/canningst-flight-profile.json
$GRANT = (Get-Content runs/registry.json -Raw | ConvertFrom-Json).grants[-1].grant_sha256
```

It prints the grant hash (the registry's last grant). One grant is enough:
its binding is the calibration profile's hash plus the supervisor's
`calibration_hashes` of exactly these drift/energy/limits files and the site.

## L3: count-and-return acceptance trial (c)

1. Preview the acceptance manifest, bound to the same files:

```powershell
python scripts/room_session.py preview --target-mode live --run-id $RUN `
  --capability count_return --grant-registry runs/registry.json `
  --capability-grant-sha256 $GRANT --capability-grant-state TRIAL `
  --trial-protocol l3-canningst --evidence-destination runs/$RUN `
  --code-revision "$(git rev-parse HEAD)" --integration-revision "$(git rev-parse HEAD)" `
  --request-revision l3 --goal-parser-revision l3 `
  --site fixtures/room_agent/v1/canningst-site.json `
  --drift-profile fixtures/room_agent/v1/profiles/canningst-drift.json `
  --energy-profile fixtures/room_agent/v1/profiles/canningst-energy.json `
  --command-limits fixtures/room_agent/v1/profiles/canningst-command-limits.json `
  --profile-file fixtures/room_agent/v1/profiles/canningst-flight-profile.json `
  --aircraft-id tello-canningst --firmware-version UNVERIFIED `
  --requested-jev-identity jev --requested-detector-identity gemini-openrouter `
  --max-duration-s 600 --max-distance-cm 2000 --max-recovery-attempts 3 `
  --consent-reference "CanningSt/$TODAY" --manifest-out runs/$RUN.manifest.json
$DIGEST = (Get-Content runs/$RUN.manifest.json -Raw | ConvertFrom-Json).authorization_digest
```

   Expected: the manifest on stdout and **no** `missing gate:` line on
   stderr (the TRIAL grant admits it), then the `next:` hint.

2. Check the floor inside the cube is clear, then record the two site
   assurances (30 minutes, the site's `max_assurance_age_ms`):

```powershell
python scripts/room_assurance.py --site fixtures/room_agent/v1/canningst-site.json --session-id $RUN --clock-domain-id operator-$RUN --out-dir runs --prefix $RUN
```

   It prints the two assurance file paths. Everything from here to landing
   must happen inside those 30 minutes.

3. **Pre-flight checklist (before the motors-on step).** Do not run step 4
   until every line is true:

   - [ ] Battery at least **20 %** (check in the Tello app or the L0 line
         `battery=<n>%`). This is the landing reserve floor, not an
         unconditional takeoff permit. Each observation is admitted only
         while it can still pay, above that floor, its own worst case, a
         recovery and the whole return (35 % center, 40 % left/right at
         0.288 %/s): the center needs about 55 %, left/right about 60 %;
         below that the aircraft returns. Below about 45 % takeoff itself is
         refused. A real observation burns about 5-8 %.
   - [ ] The Tello sits on the start mark (250, 190 cm), nose toward the tag
         stands, and both tags are in view of its camera.
   - [ ] Clear distance **measured with the tape** in each move direction
         from the pad: left and right at least 90 cm (to x 160 / 340 cm),
         back at least 30 cm (to y 160 cm), forward at least 110 cm (to
         y 300 cm), and up to 2 m. Nothing and nobody inside the cube.
   - [ ] Operator's hand on the emergency stop: this terminal focused, ready
         to press `E` (motor stop) as soon as the command prints
         `EMERGENCY: press E to stop motors immediately.`
   - [ ] Consent recorded for today (L0 step 1) and everyone in frame agreed.

4. Run the trial (motors on):

```powershell
python scripts/room_session.py live --execute --manifest runs/$RUN.manifest.json --authorization-digest $DIGEST `
  --capability count_return --grant-registry runs/registry.json `
  --consent-db runs/consent.sqlite3 --location CanningSt --date $TODAY --i-authorise-paid-calls `
  --site fixtures/room_agent/v1/canningst-site.json --tag-map docs/wp5-flight3-map.json `
  --assurance runs/$RUN.nav.assurance.json --assurance runs/$RUN.energy.assurance.json `
  --clock-domain-id operator-$RUN `
  --drift-profile fixtures/room_agent/v1/profiles/canningst-drift.json `
  --energy-profile fixtures/room_agent/v1/profiles/canningst-energy.json `
  --command-limits fixtures/room_agent/v1/profiles/canningst-command-limits.json `
  --profile-file fixtures/room_agent/v1/profiles/canningst-flight-profile.json `
  --run-root runs --connectivity-timeout-s 20 --connectivity-max-age-s 90
```

   The request is always the standard one: "count the computers and phones
   in the surveyed room, report uncertain detections as uncertain, then
   return home". `--connectivity-max-age-s 90` (not L0's 30) because the
   preflight must stay fresh through Jev's goal compilation, which is live
   now. Expected: the preflight identity line, the E-stop line, takeoff, the
   session, landing, then `{"run_id": "<run>", "terminal_state": "<state>"}`
   on stdout, exit 0. `terminal.json` states `took_off`, the landing and the
   terminal state.

5. Verify the artifact:

```powershell
$TERMINAL = (Get-Content runs/$RUN/terminal.json -Raw | ConvertFrom-Json).state
python scripts/room_verify_artifact.py --manifest runs/$RUN/manifest.json --expect-terminal $TERMINAL --expect-capability count_return --require-event TAKEOFF_AUTHORIZED --require-event GOAL_LEDGER_ACCEPTED --profile-file fixtures/room_agent/v1/profiles/canningst-flight-profile.json
```

   Expected: the run id, exit 0. A `COMPLETED` or `INCOMPLETE_RETURNED` state
   also needs a confirmed landing time (`completed_at`), which the verifier
   checks.

### When L3 refuses before takeoff

| Message | Meaning | What to do |
|---|---|---|
| `acceptance admission requires exactly one matching TRIAL grant ...` | The grant was issued for another `$RUN`, other files, or it expired. | Re-run "Issue the TRIAL grant" for this `$RUN`. |
| `profile file ... is not among the manifest's profile_sha256s` | Preview and live were given different profile files. | Use the same four files in both. |
| `site assurance for trigger ... is missing, expired or not uniquely current` | Step 2 is older than 30 minutes, or for another run. | Re-check the floor and re-run step 2. |
| `the live Jev judge needs OPENROUTER_API_KEY to be set` / `... requires GEMINI_OPENROUTER_MODEL ...` | The key or detector model is not configured. | Set it in the environment or `.env`. |
| `connectivity preflight expired before takeoff` | Goal compilation took longer than the max age. | Re-run step 4; if it repeats, raise `--connectivity-max-age-s`. |
| `takeoff refused: start blocked: <reason>` | The supervisor refused the takeoff itself (for example `LOCALIZATION_LOST`: the pad does not see two tags). | Fix the cause (tags in view, battery), re-run. Nothing flew. |

## L2 safety trials (optional, after L3)

Optional; none is needed for L3. Each is its own TRIAL run with the same
grant procedure:

- a forced return (low battery reserve) from a hover, landing on the pad;
- a tag-free hold that goes COASTING and is re-anchored (Jev's `REANCHOR`,
  or the supervisor's mandatory recovery at `RECOVERY_REQUIRED`);
- an operator `E` motor stop from a low hover over the pad.

## 1. Preview a manifest

`preview` builds and prints a canonical `room-session-manifest-v1` document
(schema: `docs/superpowers/specs/contracts/room-session-manifest-v1.schema.json`)
to stdout, and, with `--manifest-out`, atomically writes exactly those
bytes to a file. It never touches anything else on disk.

```
python scripts/room_session.py preview \
  --target-mode live \
  --run-id 2026-09-23-count-return-01 \
  --capability count_return \
  --grant-registry path/to/registry.json \
  --profile <calibration-profile-sha256> \
  --capability-grant-sha256 <grant-sha256> \
  --capability-grant-state PROMOTED \
  --code-revision "$(git rev-parse HEAD)" \
  --integration-revision <integration-revision> \
  --request-revision <request-revision> \
  --goal-parser-revision <goal-parser-revision> \
  --site-sha256 <site-sha256> --tag-map-sha256 <tag-map-sha256> --camera-sha256 <camera-sha256> \
  --aircraft-id <aircraft-id> --firmware-version <firmware-version> \
  --requested-jev-identity <jev-model-id> --requested-detector-identity <detector-model-id> \
  --max-duration-s 300 --max-distance-cm 2000 --max-recovery-attempts 3 \
  --consent-reference <consent-record-id> \
  --manifest-out run/manifest.json
```

Preview also prints, to stderr:

- `missing gate: ...` for each authorization gate this manifest does not
  yet satisfy (capability admission against `--grant-registry`, and
  whether any `--consent-reference` was declared at all). An empty
  registry, an expired/mismatched grant, or no consent reference each show
  up here -- preview never fails just because a gate is unmet; it reports.
- `next: python scripts/room_session.py live --execute --manifest ...
  --authorization-digest ... --capability ... --grant-registry ...
  --consent-db <...> --location <...> --date <...> --i-authorise-paid-calls
  --profile-file <...> --site <...> --assurance <...> --clock-domain-id
  <...> --drift-profile <...> --energy-profile <...> --command-limits <...>
  --run-root <...>`, the exact, actually-runnable command to run next (positional
  `live`, not a `--mode` flag), once every reported gate is clear and
  `--manifest-out` was given. The `<...>` placeholders are the
  consent/profile/site/assurance evidence files preview does not itself
  take (see below); fill them in with real paths. Add
  `--check-connectivity-only` for the motors-off diagnostic instead of a
  full flight.

`--target-mode simulate` previews a manifest for the (currently deferred)
simulate mode instead; capability admission never applies to it.

## 2. Run the motors-off connectivity diagnostic

```
python scripts/room_session.py live \
  --execute \
  --manifest run/manifest.json \
  --authorization-digest <digest preview printed> \
  --capability count_return \
  --grant-registry path/to/registry.json \
  --consent-db path/to/consent.sqlite3 --location <site-name> --date <YYYY-MM-DD> \
  --i-authorise-paid-calls \
  --run-root path/to/runs \
  --check-connectivity-only
```

This is the sole capability-grant exemption in this build: it admits no
grant and checks no calibration profile (it only loads `--grant-registry` to
bind the run's session row, and an empty registry is fine), but it still requires
the manifest's own authorization digest and recorded provider/filming
consent. It prints `CONNECTIVITY_READY` and exits `0` only when both the
drone telemetry probe and the provider reachability probe answer fresh
(within `--connectivity-max-age-s`, default 5s) inside
`--connectivity-timeout-s` (default 5s) -- run concurrently, so one slow
path never silently doubles the wait for the other. It never sends a
takeoff command.

The CLI's default probes are the real ones (`sentinel.room.live_stack`),
so this command reaches the Tello and OpenRouter. The operator's exact
sequence, expected output and failure meanings are in "L0: motors-off
connectivity check" above.

## 3. The full live count-and-return session

**Emergency stop.** Once every gate passes and the flight stack is built,
this command prints `EMERGENCY: press E to stop motors immediately.` and
arms that hotkey (`sentinel.emergency.EmergencyHotkey`, the same one
`sentinel.cli`'s other real-flight paths use) on the runner's
`trigger_emergency` for the whole flight, disarming it only after landing or
on exit. Pressing `E` sends the motor-stop command directly, bypassing the
executor's own command queue, and latches the flight terminal -- keep the
terminal window focused so the key reaches it.

```
python scripts/room_session.py live \
  --execute --manifest run/manifest.json --authorization-digest <digest> \
  --capability count_return --grant-registry path/to/registry.json \
  --consent-db path/to/consent.sqlite3 --location <site-name> --date <YYYY-MM-DD> --i-authorise-paid-calls \
  --profile-file path/to/profile.json \
  --site fixtures/room_agent/v1/canningst-site.json --tag-map docs/wp5-flight3-map.json \
  --assurance path/to/navigation-assurance.json --assurance path/to/energy-assurance.json \
  --clock-domain-id <clock-domain-id> \
  --drift-profile path/to/drift-profile.json \
  --energy-profile path/to/energy-profile.json \
  --command-limits path/to/command-limits.json \
  --run-root path/to/runs \
  --connectivity-timeout-s 20 --connectivity-max-age-s 30
```

The `--connectivity-timeout-s`/`--connectivity-max-age-s` values match L0's
tested ones above, not this command's own defaults (5s/5s): the real drone
probe alone can take close to 20s (adapter selection plus DJITelloPy's
connect), so the 5s default always refuses a real flight. `before_takeoff`
re-checks the *same* recorded preflight's age again after collaborator
construction, supervisor bootstrap and goal compilation, so the max-age
margin must also cover that gap; time it once on your own hardware rather
than raising it further blind.

`--drift-profile` is a measured `pose-drift-v1` `DriftProfile` (every
field required, `validated: true` with evidence refs). `--energy-profile`
is a measured `EnergyProfile` for this site and aircraft: `execution_scope:
PROMOTED` under a PROMOTED grant, or `CALIBRATION` (the history profiles)
only under a TRIAL grant in its acceptance run. `--command-limits` overrides
`CommandLimits` fields; the defaults apply when it is omitted.

**One grant per capability** (Task B item 5). `room_capability.py
issue-trial` takes the same `--site`, `--drift-profile`, `--energy-profile`,
`--command-limits` and `--profile-file` files and binds the grant to all of
them at once: the calibration profiles' hashes plus
`sentinel.room.supervisor.calibration_hashes(energy, drift, limits, site)`.
`room_session.py preview` given the same files declares exactly that set as
the manifest's `profile_sha256s`, so capability admission and
`SafetySupervisor.validate_bindings` accept the one grant. (A separate
flight-calibration grant bound to the calibration hashes alone is still
accepted by the supervisor.)

Gate order (each one runs before the next; the run artifact is opened only
once every one of these passes, and a provider or drone client is never
constructed until connectivity does too):

1. Manifest authorization: `--manifest`'s bytes parse, and
   `--authorization-digest` matches exactly the digest preview printed for
   those bytes.
2. `--capability` is offered and matches the manifest's own
   `requested_capability`.
3. Provider/filming consent (`sentinel.survey.live_detect.check_gates`):
   `--i-authorise-paid-calls`, plus recorded consent for `--location` on
   `--date` in `--consent-db`.
4. Capability-grant admission (`sentinel.room.capabilities.admit_live`): a
   unique matching `PROMOTED` grant, or (with `--trial-protocol`/
   `--evidence-destination` at preview time) a unique matching `TRIAL`
   grant in acceptance mode.
5. Matching calibration profiles: every `profile_sha256` the manifest
   declares, other than the flight files' own `calibration_hashes` (checked
   after items 6 and 7 load them), needs a `--profile-file` whose own hash matches and whose
   state is admissible for the winning grant's state
   (`sentinel.room.calibration.admissible_for` -- `CALIBRATION` only for a
   `TRIAL`/acceptance grant, `PROMOTED` only for a `PROMOTED` grant).
6. Site assurance: a current, unexpired `--assurance` record for both
   `NAVIGATION_UNAVAILABLE` and `RETURN_ENERGY_UNAVAILABLE` on `--site`,
   bound to `--clock-domain-id`.
7. Flight calibration evidence: `--drift-profile` and `--energy-profile`
   (and optional `--command-limits`) load and validate.
8. Tag map (default flight stack only): `--tag-map`'s file bytes hash to
   the manifest's `tag_map_sha256`. The stack localises from this map.
9. The motors-off connectivity preflight (same as above), recorded as a
   durable `CONNECTIVITY_CHECKED` event under `--run-root`.

After the preflight, the session follows the steps in "Scope of this
build". Any refusal before takeoff is recorded as `LIVE_SESSION_REFUSED`,
exits `2` and leaves no `terminal.json`, so the run can be retried. Once the
controller reaches a terminal state, or the goal ledger ends the session on
the ground, live finalizes `terminal.json` (`state`, `took_off`,
`ledger_status`, landing state and `completed_at`) and `summary.json`. It
then prints `{"run_id": ..., "terminal_state": ...}` and exits `0`. After
takeoff, an unexpected failure in the session loop triggers the
supervisor's forced landing and is finalized as `ABORTED`.

## 4. Replay a frozen fixture

```
python scripts/room_session.py replay --fixture fixtures/room_agent/v1/manifest.json
```

Prints the reproduced `{final_snapshot_sha256, decision_stream_sha256,
report_sha256, terminal_state}` and exits `0`, or refuses (exit `2`) if the
fixture's manifest-bound hashes, stream integrity or replayed result don't
match. Never constructs a provider or drone client
(`sentinel.room.replay.replay_fixture`'s own default factories refuse).

## 5. Simulate mode (deferred)

```
python scripts/room_session.py simulate --fixture <anything>
```

Always refuses with `simulation deferred (Plan 06 Task 2)`, regardless of
`--fixture`'s content -- Plan 06 Task 2 (simulated flight) was never built.

## 6. Verify a finished run artifact

```
python scripts/room_verify_artifact.py \
  --manifest run/<run-id>/manifest.json \
  --expect-terminal COMPLETED \
  --expect-capability count_return \
  --require-event CHECKPOINT_COMPLETED \
  --profile-file path/to/profile.json
```

Prints the verified `run_id` and exits `0`, or refuses (exit `2`) on: a
tampered manifest digest; a manifest not stored under its own run_id
directory; a mismatched `--expect-capability`; a missing `terminal.json`;
a terminal `state` that does not equal `--expect-terminal`; a
safe-termination state (`COMPLETED`, `INCOMPLETE_RETURNED`, `SAFE_LANDED`,
`LANDED_AT_CERTIFIED_ZONE`) with no `completed_at` evidence of an actual
confirmed landing; a durable event whose independently recomputed hash
does not match what the store recorded; a `terminal.json` resolved-identity
record that disagrees with the durable `IDENTITY_RESOLVED` event log; a
`--profile-file` whose hash is not among the manifest's declared
`profile_sha256s`; or a `--require-event` type that never appears in the
run.

## Known follow-ups

- **Real-mode counting under the history profiles.** Observation is open
  in REAL mode under `count_return` grants. With the robust history drift
  the supervisor admits the three CanningSt regions only because they stand
  20 cm apart at the takeoff height (the `observation` envelope) and the
  hover drift is the operator-judged 0.05 cm/s; wider regions need a site
  whose pad is further from the cube faces (see "What L3 will do with the
  history profiles"). Hover drift is still unmeasured.
- **Arrival without trims.** `trim_rounds` is 0, so an observation is
  credited when the measured pose is within the planner's dead band (10 cm)
  plus 5 cm of the declared pose; an open-loop arrival further off (the
  history's median leg error is about 10 cm) is a `TOOL_FAILURE` for that
  region, and the loop moves on.
- **Pre-takeoff pose.** The default flight stack
  (`sentinel.room.live_stack`) anchors the tracker from a tag burst taken
  on the pad, at pad height. Start admission assesses the return from the
  airborne home the takeoff climbs to (the pad fix lifted to the home
  route's altitude), and the CanningSt home corridor reaches the pad level
  minus `max_height_error_cm`. The pad must see at least two mapped tags,
  or takeoff is refused with `LOCALIZATION_LOST`.
- **In-flight site assurance.** The verified `--assurance` records are
  adopted with their original monotonic window. Each contingency's
  `max_assurance_age_ms` (from the site survey) must cover the flight, or
  the supervisor lands on expiry. No live assurance observer renews them
  mid-flight.
- **Flight-valid site.** Fly `fixtures/room_agent/v1/canningst-site.json`
  (provenance in `canningst-site.PROVENANCE.md`, `operator_confirm` values
  listed there). `fixtures/room_agent/v1/semantic-site.json` is a replay
  fixture, not a flight site: its home route does not end over its pad.
- The CLI's site-assurance gate is a standalone re-check of
  `sentinel.room.site`'s per-trigger current-assurance rule. The supervisor
  then re-checks the adopted copies itself on every admission.
- `--cost-assumptions-json`/`--home-policy-json`/`--termination-policy-json`
  accept arbitrary JSON objects; this CLI does not itself validate their
  contents beyond what the manifest schema requires.

## Optional recorded perception review

In Mission Control, choose **Live** and then **Standard + recorded post flight review** in the separate Perception review dropdown before proposing the run. The live OpenRouter detector and saved PNG captures continue to drive the room mission. The Tello receiver also saves its raw stream as `flight.h264` in that run's directory, with `recording.json` containing its byte count, SHA-256 and recording status. The current run panel offers a raw recording download after the receiver closes.

After landing, click **Analyze recording** to run Dzivha's local Grounding DINO over sampled frames. The resulting `review/annotated.mp4`, `review/review.json` and `review/analysis.log` stay in the run directory. The panel shows annotated playback and a timestamped detection list; this visual review does not change the flight's decisions or counts and makes no yaw-scan bearing or unique-object claim.

Stage the optional model before the flight from `fixtures/perception`:

```powershell
uv sync --frozen
uv run python scripts/probe_models.py
uv run python scripts/stage_room_review.py
```

The Analyze button requires that virtual environment and its `models/manifest.json`. If either is missing, the raw recording remains downloadable and the panel names the missing setup. Analysis uses the manifest-pinned checkpoint from the local cache without network access. Raw H.264 has no frame timestamps, so timeline times are marked as estimates based on 30 frames per second.
