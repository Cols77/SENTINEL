# Room-agent Jev check (Task V)

Does the real Jev model make sensible choices in the room controller loop?
`scripts/room_jev_check.py` runs `RoomController` with the real `JevJudge`
(the same construction `sentinel.coordinator.proposer_factory.build_proposer`
uses for the `jev` proposer arm — OpenRouter, `typesafe/jev-1.13-20260917`)
against a fake, offline `SafetySupervisor`/stub executor on the real
CanningSt site fixture (`fixtures/room_agent/v1/canningst-site.json`), and a
fake detector that reports a phone only from a chosen region. Nothing
contacts drone hardware; a real Tello client and a real vision detector are
never constructed.

Paid calls are gated exactly the way `build_proposer` gates the `jev` arm:
`SENTINEL_JEV_PROPOSER_CONSENT` must be a non-blank environment variable and
`OPENROUTER_API_KEY` must resolve, both read from the nearest `.env` up the
directory tree. This repository's checkout already carries both (the
project's standing live-model consent for this experiment), so the run
below is a real, paid model run, not a simulation of one. Each case is
capped at 40 Jev calls; none came close (5–11 calls each, 36 total).

## Results

*(This table was rebuilt from the run recorded in this repository's
`artifacts/room-agent/jev-check/<case>/{jev_calls,events}.jsonl` on
2026-09-24 — see "Fix 1" below for why a prior version of this table did
not match any evidence on disk.)*

| Case | Expected (brief) | What happened | Verdict |
|---|---|---|---|
| 1. Search ("find the phone") | Jev picks regions one at a time, stops once found, names the region, returns home, no invalid choice | Jev's confidence for the region that actually held the phone (`center-rear-middle`) never cleared the bar (refused at confidence 0.41, then 0.24); the one region it *did* accept (`right-rear-middle`, confidence 0.88) held no detection. It then worked through `ANSWER:SEARCH` (confidence 0.72), `READ_DRONE` (0.31), `READ_OBSERVATIONS` (0.75), `ESCALATE` (0.09), `CLARIFY` (0.13) and finally `READ_COUNTS` (accepted, confidence 1.0, but failed execution — `NO_EVIDENCE`, no legacy report exists) before running out of candidates. The aircraft still landed; no answer was ever recorded. Terminal: `BLOCKED`, not `COMPLETED`. 11 Jev calls. | **FAIL** (confidence-threshold mismatch — see "Jev's reasoning" below; the phone itself was never actually re-visited once its region's first two offers were refused) |
| 2. Staleness / re-anchor | Second observation triggers COASTING; Jev re-anchors or the supervisor forces recovery; search resumes and completes | Only **one** observation was ever accepted in this run (confidence 0.90, `right-rear-middle` — this case's designated phone region, so it *was* actually detected), so the harness's "after the second accepted observation" staleness injection never fired — this scenario was not exercised against the real model in this run. `ANSWER:SEARCH` was then refused at confidence 0.53 (probability 0.61); the run exhausted `READ_DRONE`/`READ_OBSERVATIONS`/`CLARIFY`/`ESCALATE`/`READ_COUNTS` the same way as case 1. Terminal: `BLOCKED`. 9 Jev calls. | **NOT EXERCISED** (blocked upstream of the staleness injection point, same confidence-threshold cause as case 1) |
| 3. Count ("count the phones") | Report 1 confirmed / 1 uncertain, coverage note, return home | Same pattern: the one region accepted (`right-rear-middle`, confidence 0.94) held no detection — the two seeded detections were at `center-rear-middle` (high band) and `left-rear-middle` (low band), neither of which Jev's confidence for observing ever cleared 0.8. `ANSWER:g1:INCOMPLETE` was then refused at confidence 0.57 (probability 0.66); the run exhausted the remaining read/clarify/escalate candidates and blocked without ever recording a count answer. Terminal: `BLOCKED`. 11 Jev calls. | **FAIL** (same confidence-threshold cause; no count answer was reached in this run at all, so the brief's "1 confirmed/1 uncertain" wording was never put to the test here) |
| 4. Read-only ("what is your battery level?") | Zero movement, direct answer | Jev picked `READ_DRONE` first (correct — probability 0.64) but at confidence 0.54, under the 0.8 bar, so it was refused; every subsequent candidate (`READ_OBSERVATIONS` 0.41, `CLARIFY` 0.23, `ESCALATE` 0.67) was refused the same way, down to `READ_COUNTS` (accepted, confidence 1.0, but `NO_EVIDENCE` — no legacy report exists) and `NO_LEGAL_CANDIDATES`. Zero movement held throughout (the executor was never called), but no answer was ever recorded. Terminal: `BLOCKED`. 5 Jev calls. | **FAIL** (same confidence-threshold cause) |

**36 real Jev calls total (11 + 9 + 11 + 5), well under the 40-per-case
budget. Zero invalid, malformed or illegal choices were recorded in any
case** — every one of the 36 calls returned a well-formed, legal answer;
`assess_choice` never hit `ILLEGAL_OR_MALFORMED` or
`MISSING_OR_NON_MAX_SELECTION` once, in any case. But this run's failure
mode is *not* simply "the right choice at the wrong confidence": in every
case, the single `OBSERVE` candidate Jev's confidence actually cleared the
0.8 bar was **not** the region holding the target detection (search, count)
or was accepted after the phone was already found on the very first
accepted look (staleness) — the model's per-region confidence in this run
did not track which region was actually worth visiting. Once no further
`OBSERVE` candidate could clear the bar, the loop fell through every
remaining read/clarify/escalate/answer candidate (each also under-confident
in turn) until nothing legal was left and the session blocked. This is a
genuine finding about Jev's calibration against this controller's defaults
— reported here rather than "fixed" (e.g. by lowering `act_at` for this
run, which would just be tuning the experiment to pass), per the brief.

## Jev's reasoning (excerpts)

**Case 1 (search), turn 3 (the only accepted observation — `right-rear-middle`, which did not hold the phone):**
```
value: OBSERVE:g1:right-rear-middle-toward_targets
probabilities: {right-rear-middle: 0.90, read_drone: 0.01, escalate: 0.01, read_counts: 0.00, clarify: 0.02, read_observations: 0.06}
confidence: 0.88
```

**Case 1, turn 6 (the refused answer, offered before the phone's actual region was ever re-tried):**
```
value: ANSWER:SEARCH
probabilities: {ANSWER:SEARCH: 0.76, read_observations: 0.11, clarify: 0.03, escalate: 0.02, read_counts: 0.00, read_drone: 0.08}
confidence: 0.72
```
`assess_choice` refuses this as `LOW_CONFIDENCE` because 0.72 < `act_at`
(0.8).

**Case 2 (staleness), turn 3 (the only accepted observation, `right-rear-middle` — this case's actual phone region):**
```
value: OBSERVE:g1:right-rear-middle-toward_targets
probabilities: {right-rear-middle: 0.92, read_drone: 0.01, read_observations: 0.04, read_counts: 0.00, clarify: 0.02, escalate: 0.01}
confidence: 0.90
```

**Case 3 (count), turn 4 (the accepted observation, `right-rear-middle` — the one region with no seeded detection):**
```
value: OBSERVE:g1:right-rear-middle-toward_targets
probabilities: {right-rear-middle: 0.94, escalate: 0.02, clarify: 0.02, read_drone: 0.01, read_counts: 0.01}
confidence: 0.94
```

**Case 4 (read-only), turn 1 (first, correct, but refused choice):**
```
value: READ_DRONE
probabilities: {READ_DRONE: 0.64, READ_OBSERVATIONS: 0.03, CLARIFY: 0.21, READ_COUNTS: 0.00, ESCALATE: 0.12}
confidence: 0.54
```
`READ_DRONE` is exactly the right first move for "what is your battery
level?" — refused anyway for being under the acceptance bar, and every
option offered afterward (`READ_OBSERVATIONS`, `CLARIFY`, `ESCALATE`,
`READ_COUNTS`) was refused or failed the same way before the session ran
out of candidates.

Full per-call logs (state, criteria, raw answer) are at
`artifacts/room-agent/jev-check/<case>/jev_calls.jsonl`; full event streams
at `artifacts/room-agent/jev-check/<case>/events.jsonl`. Neither is
committed (`artifacts/` is gitignored) — see "Fix 1" below for why that
matters and what changed as a result.

## How this was tested

- **Real model, offline harness.** `scripts/room_jev_check.py run_all(...)`
  builds one fake `Environment` (site + fake `SafetySupervisor`/executor +
  fake detector) per case and drives `RoomController` to a terminal state,
  capped at 40 Jev calls.
- **Regression test, no network.** `tests/room/test_jev_check.py` exercises
  the exact same harness mechanics (site loading, the fake supervisor/
  detector wiring, event logging, the call-budget wrapper, the consent/key
  gate) with `GreedyJudge` — a small deterministic double that always
  finishes observing before answering and always re-anchors when offered —
  so the plumbing stays tested for free without paid calls. All four cases
  reach `COMPLETED` under `GreedyJudge`; 12/12 tests pass:
  `python -m pytest tests/room/test_jev_check.py -q`. Every test now passes
  its own `artifact_root` (a `tmp_path` subdirectory) to `run_all(...)`
  rather than relying on the default — the default is the *same* fixed
  `artifacts/room-agent/jev-check/` tree a real run writes its evidence to,
  and this test suite must never write `GreedyJudge`'s synthetic logs there
  (see "Fix 1" in `task-V-report.md`).
- **The gate itself is tested**, not just used: refusal without
  `SENTINEL_JEV_PROPOSER_CONSENT`, refusal with consent but no
  `OPENROUTER_API_KEY`, and the nearest-`.env` search logic each have their
  own test.

## Known gaps / design choices

- **Regions.** The brief's example ("the phone is in the front-left-top
  region") does not match any region this site fixture actually declares —
  CanningSt's `pose_visibility` has exactly three: `center-rear-middle`,
  `left-rear-middle`, `right-rear-middle` (all `-toward_targets`, all the
  same declared yaw). The harness uses whatever `declared_regions()` finds
  rather than hardcoding the brief's wording.
- **Drift profile is synthetic, not CanningSt's own.** The real
  `fixtures/room_agent/v1/profiles/canningst-*.json` calibration, applied to
  this harness's generic ~60-second worst-case home-route budget (a fixed
  per-checkpoint overhead, not distance-proportional), projects
  `xy_uncertainty` past even that profile's own 30 cm ceiling before a
  single turn is taken, and refuses `home_clear` outright. The harness uses
  a synthetic, near-zero-drift profile instead (same convention as the
  existing `tests/room/test_supervisor.py` "safety" fixture) so that Jev's
  choices, not localization drift modeling, are what gets exercised.
  Reproducible directly: build an `Environment` with the real CanningSt
  drift profile and call `supervisor._path_clear(pose, supervisor._termination(pose, site), site, corridor=True)`
  — it returns `False`.
- **The fake detector fakes presence, not photogrammetry.** Every detection
  uses the same generic, always-in-frame bounding box regardless of where
  the "target" conceptually is; grounding places it at whatever room
  position that box's ray happens to intersect for that pose. This is
  sufficient for the search case (only the *region name* is reported) but
  means the count case's two single-view detections never geometrically
  corroborate into one "confirmed" object — see the results table.
- **Staleness case was never really exercised against the real model.**
  The scripted-Jev regression test proves the mechanism works (it forces
  `coast()` after the second accepted observation and asserts either
  `POSE_REANCHORED` or a forced `RECOVER`), but the real run's case 2 never
  reached a second accepted observation before running out of candidates,
  so the actual re-anchor decision was never put to the real model here.
- **Confidence didn't track which region actually mattered.** In this run,
  in both the search and count cases, the one `OBSERVE` candidate whose
  confidence cleared 0.8 was consistently `right-rear-middle` — the region
  with no seeded detection in either case — while the regions that actually
  held a detection (`center-rear-middle`, `left-rear-middle`) were refused
  at confidence 0.24–0.41 before ever being retried. This may be a
  positional/ordering artifact of how criteria are presented rather than a
  claim about Jev's search strategy in general; a repeat run with region
  order randomized would help separate the two, but is out of this task's
  scope.
- **No .env, no real run at all, is also a supported and tested path.**
  `build_real_jev_judge()` refuses with a clear message before building any
  fixture if `SENTINEL_JEV_PROPOSER_CONSENT` or `OPENROUTER_API_KEY` is
  missing; this is exercised in `tests/room/test_jev_check.py` without
  relying on the environment this run happened to have credentials in.
