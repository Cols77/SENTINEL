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

| Case | Expected (brief) | What happened | Verdict |
|---|---|---|---|
| 1. Search ("find the phone") | Jev picks regions one at a time, stops once found, names the region, returns home, no invalid choice | Jev explored regions in a sensible order and found the phone (region 3, p=0.90); no illegal/malformed choice at any turn; but it never *landed* on a final answer — its own confidence for `ANSWER:SEARCH` (p=0.77) sat just under the controller's 0.8 acceptance bar, so that turn was refused as `LOW_CONFIDENCE` and the session ran out of legal candidates before ever answering. Terminal: `BLOCKED`, not `COMPLETED`. | **FAIL** (controller-threshold/confidence-calibration mismatch, not an illegal or nonsensical choice — see "Jev's reasoning" below) |
| 2. Staleness / re-anchor | Second observation triggers COASTING; Jev re-anchors or the supervisor forces recovery; search resumes and completes | Only **one** observation ever succeeded (the first two regions were themselves refused as `LOW_CONFIDENCE` before the third finally cleared the bar), so the harness's "after the second observation" staleness injection never fired — this scenario was not actually exercised against the real model in this run. Terminal: `BLOCKED`. | **NOT EXERCISED** (blocked upstream of the staleness injection point, same confidence-threshold cause as case 1) |
| 3. Count ("count the phones") | Report 1 confirmed / 1 uncertain, coverage note, return home | Same confidence-threshold pattern: one region observed and accepted (p=0.94), the rest refused as `LOW_CONFIDENCE`, then `ANSWER:g1:INCOMPLETE` itself refused (p=0.66 < 0.8). Session ran out of candidates. Terminal: `BLOCKED`. Separately: even under the deterministic scripted-Jev regression test (see below), the fake detector's design (two single-view detections in different regions, never geometrically merged) produces "0 confirmed; 2 uncertain", not the brief's illustrative "1 confirmed; 1 uncertain" — a harness/geometry mismatch, not a controller defect. | **FAIL** (same confidence-threshold cause; count-merge expectation also does not hold under this harness's fake detector — see "Known gaps") |
| 4. Read-only ("what is your battery level?") | Zero movement, direct answer | Jev picked `READ_DRONE` first (correct, p=0.61) but under the 0.8 bar, so it was refused; every subsequent read/clarify/escalate candidate was *also* refused as `LOW_CONFIDENCE` in turn, down to `READ_COUNTS` (`NO_EVIDENCE`, no legacy report exists) and `NO_LEGAL_CANDIDATES`. Zero movement held throughout (the executor was never called), but no answer was ever recorded. Terminal: `BLOCKED`. | **FAIL** (same confidence-threshold cause) |

**Zero invalid, malformed or illegal choices were recorded in any case** —
every one of the 36 real calls returned a well-formed, legal answer;
`assess_choice` never hit `ILLEGAL_OR_MALFORMED` or
`MISSING_OR_NON_MAX_SELECTION` once. The failure mode across all four cases
is the same and is not about Jev picking a *wrong* action: when it commits
to an action instead of continuing to explore, that action is directionally
correct in every case that reached a genuine decision point. The failure is
that `RoomController`'s default `act_at=0.8` acceptance threshold is
stricter than this model's typical self-reported confidence for these
prompts, so it is very often refused for being under-confident rather than
for being wrong, and the loop then works through every remaining legal
candidate (also under-confident) until nothing is left and the session
blocks. This is a genuine finding about Jev's calibration against this
controller's defaults, not a bug in the harness or the controller's
correctness logic — per the brief, it is reported here rather than "fixed"
(e.g. by lowering `act_at` for this run, which would just be tuning the
experiment to pass).

## Jev's reasoning (excerpts)

**Case 1, turn 3 (the accepted observation, right-rear-middle):**
```
value: OBSERVE:g1:right-rear-middle-toward_targets
probabilities: {right-rear-middle: 0.90, read_observations: 0.05, escalate: 0.02, read_counts: 0.00, clarify: 0.02, read_drone: 0.01}
confidence: 0.89
```

**Case 1, turn 6 (the refused answer — this is the whole finding in one call):**
```
value: ANSWER:SEARCH
probabilities: {ANSWER:SEARCH: 0.77, read_drone: 0.07, read_observations: 0.11, clarify: 0.03, read_counts: 0.00, escalate: 0.02}
confidence: 0.73
```
`assess_choice` refuses this as `LOW_CONFIDENCE` because 0.77 < `act_at`
(0.8) — one turn was doing the right thing at the wrong confidence.

**Case 3 (count), turn 4 (the accepted observation, right-rear-middle):**
```
value: OBSERVE:g1:right-rear-middle-toward_targets
probabilities: {right-rear-middle: 0.94, ...}
confidence: 0.92
```

**Case 4 (read-only), turn 1 (first, correct, but refused choice):**
```
value: READ_DRONE
probabilities: {READ_DRONE: 0.61, CLARIFY: 0.25, ESCALATE: 0.10, READ_COUNTS: 0.00, READ_OBSERVATIONS: n/a}
confidence: 0.53
```
`READ_DRONE` is exactly the right first move for "what is your battery
level?" — refused anyway for being under the acceptance bar, and every
option offered afterward (`READ_OBSERVATIONS`, `CLARIFY`, `ESCALATE`) was
refused the same way before the session ran out of candidates.

Full per-call logs (state, criteria, raw answer) are at
`artifacts/room-agent/jev-check/<case>/jev_calls.jsonl`; full event streams
at `artifacts/room-agent/jev-check/<case>/events.jsonl`. Neither is
committed (`artifacts/` is gitignored).

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
  `python -m pytest tests/room/test_jev_check.py -q`.
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
- **No .env, no real run at all, is also a supported and tested path.**
  `build_real_jev_judge()` refuses with a clear message before building any
  fixture if `SENTINEL_JEV_PROPOSER_CONSENT` or `OPENROUTER_API_KEY` is
  missing; this is exercised in `tests/room/test_jev_check.py` without
  relying on the environment this run happened to have credentials in.
