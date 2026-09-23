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
supplied. Every default refuses construction (the
`scripts/room_calibrate.py` convention), so the CLI alone never flies
anything, and no test in this repository does either.

**What a REAL-mode session can count today.** `RoomController` offers
OBSERVE checkpoints only in `ExecutionMode.SIMULATION`
(`_observation_enabled`, owned by Plan 03). A real session therefore takes
off, reads, finds no count evidence, returns home, lands and reports
`BLOCKED` (Jev asks for clarification). It does not answer a count. The
composition is complete: once that controller gate admits promoted real
profiles, the same invocation flies OBSERVE checkpoints through the
already-wired `sentinel.room.wiring` collaborators. See "Known follow-ups".

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

Running this from Python (e.g. an interactive check) requires injecting
real `drone_probe`/`provider_probe` callables into
`scripts.room_session.main(...)`; the CLI's own defaults always refuse,
so `python scripts/room_session.py ... --check-connectivity-only` alone
(no code-level override) always refuses too. That is deliberate: nothing
in this repository wires a real probe on its own.

## 3. The full live count-and-return session

```
python scripts/room_session.py live \
  --execute --manifest run/manifest.json --authorization-digest <digest> \
  --capability count_return --grant-registry path/to/registry.json \
  --consent-db path/to/consent.sqlite3 --location <site-name> --date <YYYY-MM-DD> --i-authorise-paid-calls \
  --profile-file path/to/profile.json \
  --site path/to/semantic-site.json \
  --assurance path/to/navigation-assurance.json --assurance path/to/energy-assurance.json \
  --clock-domain-id <clock-domain-id> \
  --drift-profile path/to/drift-profile.json \
  --energy-profile path/to/energy-profile.json \
  --command-limits path/to/command-limits.json \
  --run-root path/to/runs
```

`--drift-profile` is a measured `pose-drift-v1` `DriftProfile` (every
field required, `validated: true` with evidence refs). `--energy-profile`
is a measured `EnergyProfile` with `execution_scope: PROMOTED` for this site
and aircraft. `--command-limits` overrides `CommandLimits` fields; the
defaults apply when it is omitted. The registry needs **two** grants for
the capability:

- the admission grant, bound to the manifest's `profile_sha256s`;
- the flight-calibration grant that `SafetySupervisor.validate_bindings`
  checks, bound to `sentinel.room.supervisor.calibration_hashes(energy,
  drift, limits, site)` for exactly these files and `--site`.

Without the second grant, live refuses before takeoff with `flight
calibration refused: capability does not match ...`.

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
   declares needs a `--profile-file` whose own hash matches and whose
   state is admissible for the winning grant's state
   (`sentinel.room.calibration.admissible_for` -- `CALIBRATION` only for a
   `TRIAL`/acceptance grant, `PROMOTED` only for a `PROMOTED` grant).
6. Site assurance: a current, unexpired `--assurance` record for both
   `NAVIGATION_UNAVAILABLE` and `RETURN_ENERGY_UNAVAILABLE` on `--site`,
   bound to `--clock-domain-id`.
7. Flight calibration evidence: `--drift-profile` and `--energy-profile`
   (and optional `--command-limits`) load and validate.
8. The motors-off connectivity preflight (same as above), recorded as a
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

- **Real-mode counting.** `RoomController._observation_enabled` (Plan 03,
  `controller.py`, pinned by
  `tests/room/test_count_return.py::test_real_mode_never_offers_observation_candidate`)
  offers OBSERVE only in SIMULATION. Until Plan 03 opens it for promoted
  real profiles, a live session returns and lands safely but ends `BLOCKED`
  without a count. Opening it needs no change here.
- **Production flight stack.** No module yet builds the real
  `live_session.FlightStack` from a connected Tello: the `StepExecutor`
  over the client, the tag-pose `refresh()` that feeds the session's
  `LocalizationTracker`, the emergency landing and the frame source.
  `client_factory` defaults to refuse until one exists.
- **In-flight site assurance.** The verified `--assurance` records are
  adopted with their original monotonic window. Each contingency's
  `max_assurance_age_ms` (from the site survey) must cover the flight, or
  the supervisor lands on expiry. No live assurance observer renews them
  mid-flight.
- **Flight-valid site.** `fixtures/room_agent/v1/semantic-site.json`'s home
  route ends at (250, 190), not over its pad (250, 140), so
  `SafetySupervisor` refuses to start from it (`TERMINATION_PATH_INVALID`).
  It is a replay fixture, not a flight site; a real `--site` must end its
  home route over the pad.
- The CLI's site-assurance gate is a standalone re-check of
  `sentinel.room.site`'s per-trigger current-assurance rule. The supervisor
  then re-checks the adopted copies itself on every admission.
- `--cost-assumptions-json`/`--home-policy-json`/`--termination-policy-json`
  accept arbitrary JSON objects; this CLI does not itself validate their
  contents beyond what the manifest schema requires.
