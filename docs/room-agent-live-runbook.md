# Room-agent live runbook (Plan 06 Task 4, count-and-return scope)

This is the operator runbook for `scripts/room_session.py` and
`scripts/room_verify_artifact.py`. It covers the count-and-return use case
only (Plan 05 tracker/following is deferred); the CLI's built-in help
(`--help` on any subcommand) is the source of truth for exact flag names.

## Scope of this build

`scripts/room_session.py live` gates a session all the way through
manifest authorization, capability admission, matching calibration
profiles, provider/filming consent, site assurance and a motors-off
connectivity preflight -- and stops there. **Actual autonomous flight
dispatch (SafetySupervisor/tracker/reserve/recovery bootstrap and the
controller run loop) is not wired in this build.** `--check-connectivity-
only` is the one live sub-mode that runs end to end: it proves the aircraft
and the perception provider both answer, fresh, motors off, and exits.
Every other live invocation, once every gate and the connectivity preflight
pass, refuses with `full live flight execution is not wired in this build`
-- by design, not as a bug: this task never launches a real flight, and
`client_factory`/`jev_factory` (the two Python-level hooks a caller would
override to wire in a real Tello client and judgment model) default to
refuse construction, the same convention `scripts/room_calibrate.py` uses
for its own executor. Wiring a real flight loop through this CLI is future
work.

Every live invocation (including `--check-connectivity-only`) requires
`--run-root`: a `sentinel.room.artifacts.RunArtifacts` directory is opened
there under `manifest.run_id` before the connectivity preflight runs, and
the preflight's full result (both paths' readiness, identities,
`checked_at` timestamps and errors) is recorded as a durable
`CONNECTIVITY_CHECKED` event -- never only printed to stderr -- so it
survives the process and can be read back later (e.g. by
`room_verify_artifact.py --require-event CONNECTIVITY_CHECKED`, once a run
finalizes). Immediately before the point this build would otherwise
dispatch a flight, freshness is re-checked against that same recorded
preflight; one that has since expired refuses with `connectivity preflight
expired before takeoff` (recorded as `CONNECTIVITY_EXPIRED_BEFORE_TAKEOFF`)
rather than riding a stale "ready" verdict forward.

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
  <...> --run-root <...>`, the exact, actually-runnable command to run next (positional
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

This is the sole capability-grant exemption in this build: it does not
check the grant registry or calibration profiles, but it still requires
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

## 3. The full live gate chain (never reaches flight in this build)

Add the flight-capable gates to run the rest of the authorization chain
(everything short of actual flight):

```
python scripts/room_session.py live \
  --execute --manifest run/manifest.json --authorization-digest <digest> \
  --capability count_return --grant-registry path/to/registry.json \
  --consent-db path/to/consent.sqlite3 --location <site-name> --date <YYYY-MM-DD> --i-authorise-paid-calls \
  --profile-file path/to/profile.json \
  --site path/to/semantic-site.json \
  --assurance path/to/navigation-assurance.json --assurance path/to/energy-assurance.json \
  --clock-domain-id <clock-domain-id> \
  --run-root path/to/runs
```

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
7. The motors-off connectivity preflight (same as above), recorded as a
   durable `CONNECTIVITY_CHECKED` event under `--run-root`, then
   re-checked for freshness immediately before the point this build would
   otherwise dispatch a flight.

Only after all seven pass -- and the freshness re-check still holds --
does this build reach its own boundary and refuse with `full live flight
execution is not wired in this build`.

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

- Full live flight dispatch (SafetySupervisor/tracker/reserve/recovery
  bootstrap, the actual controller run loop, and real
  `client_factory`/`jev_factory` wiring) is out of scope for this task --
  see "Scope of this build" above. Fix round 2 investigated wiring this in:
  `RoomController._observation_enabled` (owned by a parallel, in-flight
  Plan 03 fix; this task may not edit `controller.py`) hard-gates OBSERVE
  checkpoints to `ExecutionMode.SIMULATION`, so a real-mode session could
  never actually fly the checkpoints count-and-return depends on even if
  the rest of the dispatch machinery were built here. Separately, no
  production factory exists anywhere in this codebase for constructing a
  `LocalizationTracker`'s drift profile or a `ReserveGuard`'s energy
  profile from `--profile-file` evidence -- `SafetySupervisor` needs both,
  and building them from scratch is new architecture, not a CLI wiring
  task. A run artifact/store and a durably-recorded, freshness-rechecked
  connectivity preflight are wired (this round); driving
  `RoomController.run_until_terminal` against a real supervisor is not.
- The site-assurance gate here is a simplified, standalone re-check of
  `sentinel.room.site`'s per-trigger current-assurance rule; it does not
  go through `SafetySupervisor`'s own clock-domain machinery (there is no
  live supervisor in this build to own one).
- `--cost-assumptions-json`/`--home-policy-json`/`--termination-policy-json`
  accept arbitrary JSON objects; this CLI does not itself validate their
  contents beyond what the manifest schema requires.
