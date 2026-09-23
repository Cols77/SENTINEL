# Room agent count-and-return readiness — 2026-09-23

This reports what Plan 03 (`docs/superpowers/plans/2026-09-22-jev-room-agent-03-count-closer-return.md`)
delivers through Task 5: a **count-and-return** capability for the agentic room
agent (`src/sentinel/room`), and one frozen, byte-stable **replay fixture**
(`fixtures/room_agent/v1`) that exercises it offline. It is not a capability
promotion and not a live run: **replay is offline evidence only — a
byte-stable reproduction of one already-recorded, synthetic trace, never a
fresh model or hardware measurement.** No live batch, autonomous-action
promotion, or hardware flight informs this report. Capability promotion is
Plan 06's job ("Execute Plan 06 through count-and-return promotion before
advertising real capability", per the Task 5 brief).

## Supported request shape

The room agent (`sentinel.room.controller.RoomController`) accepts one
natural-language request compiled into a `GoalLedger` of `COUNT` and
`ANSWER_STATE` objectives (`sentinel.room.goals`), executes them through a
bounded, append-only session (`sentinel.room.store.RoomStore`), and reports a
single terminal answer plus a `TerminalState`. In simulation mode, with the
observation collaborators wired (`resolve_checkpoint`, `perception_capture`,
`perception_provider`), a `COUNT` objective may also drive `OBSERVE`
checkpoints at surveyed poses declared by the site's `pose_visibility`
(Plan 03 Task 4). **`OBSERVE` is never offered in `REAL` execution mode**
(`RoomController._observation_enabled`; see `test_real_mode_never_offers_observation_candidate`)
until Plan 06 promotes matching pose/energy/site profiles.

## Coverage and count semantics

- Coverage is **region-visited**, not per-detection geometric grounding: once
  an `OBSERVE` checkpoint at a declared pose completes and its perception
  result is ingested and authorized, every `(cell, class)` pair that pose
  declares visibility of is marked resolved (`RoomController._coverage`,
  `sentinel.room.coverage.project_coverage`). Wiring per-detection `(x, y)`
  estimates (`sentinel.room.spatial`) into per-cell polygon containment is
  explicitly out of scope for this completion semantic (see the Task 4
  report and `RoomController._coverage`'s own docstring).
- A plain legacy `ANSWER` is gated off while the site declares coverage cells,
  a `COUNT` objective is pending, and coverage is not yet complete
  (`RoomController._answer_gated_by_coverage`); the only route to a terminal
  answer before that is the explicit `ANSWER:<objective>:INCOMPLETE`
  candidate, which reports what remains uncovered and still requests a safe
  return.
- Detections are classified through grounded counting
  (`sentinel.room.association`, `sentinel.room.counting._static_counts`), not
  through raw VLM confidence bands. **A single low-confidence sighting is
  reported as uncertain (`confirmed=0`, `uncertain=1`), never promoted to
  confirmed** — proven by `test_uncertain_detection_stays_uncertain_after_observation`
  (`tests/room/test_count_return.py`), which drives the same classification
  the replay fixture's single low-confidence "computer" detection exercises.
- Legacy `READ_COUNTS` (merged multi-view survey reports) remains available
  as a separate, explicitly labelled evidence source
  (`SINGLE_VIEW_LOWER_BOUND` / `MERGED_MULTI_VIEW` / `NOT_OBSERVABLE`); it is
  never conflated with a session's own grounded `READ_OBSERVATIONS` reads.

## Safety transitions

Every physical action is admitted and dispatched through
`sentinel.cube.step_executor`'s `SafetySupervisor`, shared with Plans 01/02.
Plan 03 Task 4 adds no new transition kind; `OBSERVE` checkpoints ride the
same `CONTINUE` / `RECOVER` / `RETURN_NOW` / `LAND_CONTINGENCY` machinery
every other checkpoint uses, including:

- a mandatory battery/localization-forced return **before** an unaffordable
  observation is ever dispatched (`test_battery_forces_return_before_an_unaffordable_observation`);
- the checkpoint and its correlated perception wait are admitted as one
  action, so a checkpoint cannot be preempted before its own perception wait
  runs (`test_observation_admission_reserves_the_perception_wait_too`);
- a terminal answer — success or explicit incompleteness — requests a
  supervisor-owned safe return/landing first whenever the session has
  actually flown, and reports `ABORTED` rather than a false success on an
  unconfirmed or failed landing (`RoomController._finalize_terminal`,
  `test_failed_return_never_reports_success`);
- a stale Jev decision, a dispatch exception, and a stale or unauthorized
  perception result are each typed refusals that never credit coverage or a
  physical effect that did not actually happen (`test_stale_observation_decision_is_never_dispatched`,
  `test_dispatch_exception_during_observation_is_typed_not_stale_decision`,
  `test_stale_perception_result_is_refused_and_coverage_not_credited`).

## Disabled / deferred capabilities

Per the operator's 2026-09-23 scope-reduction ruling (Task 5 brief, Plan 03
ledger Ruling 1/2), the following are **disabled or deferred**, not
promoted, by this plan:

- **Closer inspection** (Task 3: closer-inspection viewpoints) — deferred.
  `RoomController._closer_unavailable_candidates` reports an explicit
  `CLOSER_INSPECTION_UNAVAILABLE` outcome for any objective that can only be
  satisfied by getting closer; no closer/validate-by-approach candidate is
  ever offered (`test_closer_inspection_never_offers_an_executable_closer_candidate`).
  There is no `test_closer_inspection.py`; the replay baseline covers
  count-and-return only and never exercises a closer scenario.
- **Certified landing** — not implemented by this plan. `LAND_NEAR` remains a
  compiled goal kind (`sentinel.room.models.GoalKind.LAND_NEAR`) with no
  executing candidate in this controller.
- **Following** — not implemented by this plan. `FOLLOW` remains a compiled
  goal kind with no executing candidate in this controller.

## Replay: what it is and is not

`sentinel.room.replay.replay_fixture` verifies a fixture's manifest-bound
file hashes, its room-store schema and projector revisions, and the
recorded event/perception-ingestion stream's structural integrity (no
missing, duplicate, reordered, corrupted, or stale-authorizing record; see
`tests/room/test_replay.py`), then replays it through the **same** store and
query projectors live mode uses — `RoomStore`, `ingest_result`,
`ReadQueries.project`, `render_answer` — into a private, throwaway
`RoomStore`. It never constructs a provider or drone client
(`provider_factory`/`client_factory` are asserted unused).

The one fixture shipped, `fixtures/room_agent/v1` (label `synthetic`,
`session_id=room-agent-replay-001`), is a hand-authored but schema-faithful
count-and-return trace: a `COUNT` objective for "computer", one `OBSERVE`
turn that ingests a single low-confidence detection, a `READ_OBSERVATIONS`
turn that reads it back, and a plain `ANSWER` that reports it and completes
(`TerminalState.COMPLETED`). It is **synthetic**, not a capture of a real
flight — RoomController/SafetySupervisor were never run to produce it; every
event and the ingested perception result were appended directly through the
same primitives replay itself uses, so the golden fixture's `expected`
hashes are exactly `replay_fixture`'s own output on that one frozen trace,
not an independently re-derived value.

This proves:

- the replayed final snapshot, decision stream, and rendered terminal report
  are byte-identical across repeated replay runs of the same fixture
  (`test_replay_is_byte_stable_across_repeated_runs`);
- tampering with the recorded stream — a missing, duplicate, reordered, or
  corrupted record, or a perception-ingestion record whose declared snapshot
  version does not match the replayed session's actual state — is rejected
  (`ReplayIntegrityError`), never silently reproduced;
- the projector code this replay depends on is explicitly version-bound
  (`sentinel.room.replay.PROJECTOR_REVISION`); a fixture bound to a
  different revision is refused rather than silently replayed against code
  that may no longer compute the same thing.

It does **not** prove: model quality, live provider behavior, real detector
accuracy, real flight/localization behavior, coverage-cell polygon
containment against a real site, or anything about closer inspection,
certified landing or following (none of those are exercised). One fixture is
not a population estimate; it is a regression baseline against silent
projector drift.

## Limitations

- No fresh model or provider call is made anywhere in this plan's replay
  path; `READ_OBSERVATIONS`/`READ_COUNTS`/answer wording are deterministic,
  historical reads (`sentinel.room.queries`'s own module docstring).
- `REAL` execution mode never offers `OBSERVE`; count-and-return with actual
  flight is `SIMULATION`-only until Plan 06 promotes matching profiles.
- Coverage crediting trusts that a dispatched checkpoint actually reached the
  pose its candidate declared and that its own `CAPTURE` command completed
  (`RoomController._checkpoint_targets_declared_pose`,
  `RoomController._capture_completed`) — it does not independently verify
  per-detection geometric placement within a cell.
- The replay fixture is one hand-authored scenario, not a corpus; it is
  cheap to extend with further scenarios (additional detections, multiple
  cells, `ANSWER:<objective>:INCOMPLETE`) without changing `replay_fixture`
  itself, but no such extension is included here.

## Evidence

- `src/sentinel/room/replay.py`, `tests/room/test_replay.py`,
  `fixtures/room_agent/v1/{manifest.json,events.jsonl,frame-1.png}`.
- `tests/room/test_count_return.py` (Plan 03 Task 4's full controller/safety
  trajectory suite) for the safety-transition and count-semantics claims
  above.
- `rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room -q --tb=short -o addopts='' -p no:cacheprovider`
  — 511 passed at the commit this report ships with.
