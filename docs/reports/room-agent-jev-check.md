# Room-agent Jev check (Task V, re-run in Task V2)

Does the real Jev model make sensible choices in the room controller loop?
`scripts/room_jev_check.py` runs `RoomController` with the real `JevJudge`
(OpenRouter, `typesafe/jev-1.13-20260917`) against a fake, offline
supervisor and executor on the real CanningSt site fixture, with a fake
detector. Nothing contacts drone hardware.

## Task V2 re-run (2026-09-24): the room-loop acceptance policy

What changed since the Task V run (the "before" column):

- **Acceptance policy** (`sentinel.room.controller.AcceptancePolicy`, the
  default in `live_session.run_count_return` and in this harness): Jev's
  top legal choice is accepted at **0.5** for OBSERVE (one supervised
  checkpoint), REANCHOR and the reads, and at **0.6** for a final ANSWER.
  CLARIFY and ESCALATE, which end the session, and any other kind keep
  the strict **0.8**. A choice below its bar is **re-asked once** with the
  refusal as feedback (`state.previous_refusal`: refused choice,
  confidence, required confidence, the thresholds). Then the turn counts
  as refused. There is no loop. Physical safety does not change: the
  SafetySupervisor still admits or refuses every movement, and mandatory
  recovery and return still bypass Jev. The policy is recorded in every
  `ROOM_CHOICE_ATTEMPT`, in the live session's `ROOM_ACCEPTANCE_POLICY`
  event, and in each case's `config.json`. `--strict` reruns the old
  single 0.8 bar.
- **Paid-call gate:** the harness refuses without `--i-authorise-paid-calls`,
  before reading any `.env`.
- **Staleness case:** staleness is now injected after the **first**
  accepted observation. Two estimator failures leave the pose COASTING,
  and Jev is offered REANCHOR. If Jev does not re-anchor on its next turn,
  one more failure crosses the profile limit, and the supervisor forces
  its own recovery.
- **Fifth case:** "count the computers in the room", with two computers,
  one in the left region and one in the right region.

Run: `python scripts/room_jev_check.py --i-authorise-paid-calls
--artifact-root artifacts/room-agent/jev-check-v2` (one run, no re-runs).
The logs are in `artifacts/room-agent/jev-check-v2/<case>/{events,jev_calls}.jsonl`
and `config.json`. They are gitignored.

| Case | Before (Task V, strict 0.8) | After (Task V2 policy) | Jev calls (before → after) | Verdict |
|---|---|---|---|---|
| 1. Search ("find the phone"; phone in center) | BLOCKED. The phone's region was refused at 0.41/0.24 and never observed; no answer | Center observe was refused at 0.34, re-asked, and the re-ask (left, 0.26) was also refused. On the next turn **center was accepted at 0.57**: phone seen. `ANSWER:SEARCH` was accepted at 0.65. The answer names the center region; safe return and landing. **COMPLETED** | 11 → **4** | **PASS** |
| 2. Staleness / re-anchor (phone in right) | NOT EXERCISED (never reached the injection point) | Center was observed at 0.50, then staleness was injected (COASTING). REANCHOR was offered. **Jev tied it with the left observe (0.32 each) and picked the observe**, which was refused below 0.5; the re-ask (right, 0.38) was also refused. The harness then crossed the limit, and the **supervisor forced RECOVER** (`RETURN_REQUIRES_RECOVERY`), a stationary burst, and `POSE_REANCHORED`. **0 yaw ROTATE commands**: the burst at the current heading was enough. The search resumed: left 0.57, right 0.61 (phone found), `ANSWER:SEARCH` 0.77. **COMPLETED** | 9 → **8** | **PASS** (the re-anchor was reached; the supervisor forced it, not Jev) |
| 3. Count ("count the phones"; high band center, low band left) | BLOCKED; no count answer | All three regions observed (right 0.53 after a re-ask, left 0.57 after a re-ask, center 0.67). `ANSWER:COUNTS` was refused at 0.39, then **accepted at 0.75 on the re-ask**. Answer: "phone: 0 confirmed; 2 uncertain", coverage complete; safe return and landing. **COMPLETED** | 11 → **9** | Jev: **PASS**. The harness's pinned "1 confirmed; 1 uncertain" fails: single-view fake detections never corroborate (see the known gap below; `GreedyJudge` gets the same answer) |
| 4. Read-only ("what is your battery level?") | BLOCKED. `READ_DRONE` was refused at 0.54 | `READ_DRONE` **accepted at 0.54** (0.5 bar), `ANSWER` accepted at 0.65: "battery_percent=61 ...". Zero movement (the executor was never called). **COMPLETED** | 5 → **2** | **PASS** |
| 5. Count computers (new; computers in left and right) | (new case) | Right 0.54 (re-ask), center 0.69, left 0.72 (after one refused turn at 0.37). `ANSWER:COUNTS` was refused at 0.36, then accepted at 0.66 on the re-ask. Answer: "computer: 0 confirmed; 2 uncertain", i.e. **two computers**, coverage complete; safe return and landing. **COMPLETED** | — → **10** | **PASS** |

**33 real Jev calls in total (4 + 8 + 9 + 2 + 10).** The Task V run made 36
calls over four cases. The budget is 40 per case, and no case came close.
**Zero invalid, malformed or illegal choices** again: no
`ILLEGAL_OR_MALFORMED` and no `MISSING_OR_NON_MAX_SELECTION`. No provider
error occurred. Four of five cases pass the harness as written. Case 3
fails only on the harness's own "1 confirmed; 1 uncertain" wording, which
the fake detector cannot produce. Jev's choices in it were sound.

### What the re-run shows about Jev

- **Its confidence is low but directionally right.** Every accepted
  OBSERVE was in the 0.5–0.72 range, and every accepted answer in the
  0.65–0.77 range. None of them would have passed the old 0.8 bar.
- **The re-ask helps mainly for answers.** Both count answers were first
  offered at 0.36–0.39 and came back at 0.66–0.75 once the refusal was
  explained. For observations, the re-ask usually picked *another* region
  at similar low confidence (e.g. center 0.34 → left 0.26). The next turn
  then accepted a region above 0.5.
- **REANCHOR:** offered while COASTING, Jev rated it 0.32, level with the
  best observation, and then 0.17 on the re-ask. It did not choose it, so
  the supervisor floor did the re-anchor. Either way the re-anchor
  happened before any further observation.

### Jev's choices (excerpts from `jev_calls.jsonl`)

JevJudge returns a choice, a probability per legal action and a
confidence. There is no free-text rationale, so these are the "reasoning"
excerpts. Region names are shortened (`center` = `OBSERVE:g1:center-rear-middle-toward_targets`).

**Case 1 (search), turn 1 and its re-ask, then the next turn:**
```
call 1: value=center  confidence=0.34  {center: 0.43, READ_OBSERVATIONS: 0.16, CLARIFY: 0.11, READ_DRONE: 0.11, right: 0.09, left: 0.07}   -> refused (< 0.5), re-asked
call 2: value=left    confidence=0.26  {left: 0.36, right: 0.19, center: 0.17, READ_OBSERVATIONS: 0.15, ...}                                -> refused, turn refused
call 3: value=center  confidence=0.57  {center: 0.63, right: 0.18, READ_OBSERVATIONS: 0.08, ...}                                           -> ACCEPTED, phone seen
call 4: value=ANSWER:SEARCH confidence=0.65 {ANSWER:SEARCH: 0.71, READ_DRONE: 0.15, READ_OBSERVATIONS: 0.08, CLARIFY: 0.05}           -> ACCEPTED (0.6)
```

**Case 2 (staleness), the turn after staleness was injected (REANCHOR offered):**
```
call 4: value=left   confidence=0.22  {left: 0.32, REANCHOR: 0.32, READ_DRONE: 0.13, right: 0.11, READ_OBSERVATIONS: 0.07, ...}  -> refused, re-asked
call 5: value=right  confidence=0.38  {right: 0.46, READ_OBSERVATIONS: 0.22, REANCHOR: 0.17, ...}                                 -> refused
(harness: third estimator failure -> RECOVERY_REQUIRED; supervisor RECOVER, stationary burst, POSE_REANCHORED, no ROTATE)
```

**Case 3 (count phones), the answer and its re-ask:**
```
call 8: value=ANSWER:COUNTS confidence=0.39 {ANSWER:COUNTS: 0.49, READ_DRONE: 0.25, READ_OBSERVATIONS: 0.16, ...}  -> refused (< 0.6), re-asked
call 9: value=ANSWER:COUNTS confidence=0.75 {ANSWER:COUNTS: 0.79, ESCALATE: 0.06, READ_OBSERVATIONS: 0.06, ...}     -> ACCEPTED
```

**Case 4 (read-only), the choice refused in Task V and accepted now:**
```
call 1: value=READ_DRONE confidence=0.54 {READ_DRONE: 0.64, CLARIFY: 0.24, ESCALATE: 0.09, READ_OBSERVATIONS: 0.03, READ_COUNTS: 0.00}  -> ACCEPTED (0.5)
```

**Case 5 (count computers), the answer and its re-ask:**
```
call 9:  value=ANSWER:COUNTS confidence=0.36 {ANSWER:COUNTS: 0.47, READ_DRONE: 0.26, READ_OBSERVATIONS: 0.15, ...}  -> refused, re-asked
call 10: value=ANSWER:COUNTS confidence=0.66 {ANSWER:COUNTS: 0.71, READ_OBSERVATIONS: 0.12, ESCALATE: 0.08, ...}     -> ACCEPTED
```

### Known gaps (Task V2)

- **Counting corroboration in the harness.** The fake detector gives every
  detection the same generic box, so each object is a single view and is
  reported "uncertain". "1 confirmed / 1 uncertain" (case 3) is therefore
  unreachable in this harness. Case 5 checks the total (2) instead.
- **Staleness was resolved by the supervisor, not Jev.** One run is too
  small to say how often Jev would pick REANCHOR. Its 0.32 tie suggests
  the option is taken seriously but not preferred over observing.
- **Thresholds come from one run's calibration.** 0.5/0.6 fit what this
  model reports. The re-ask costs one more call per refused turn
  (11 of the 33 calls here were re-asks).

---

# Task V (before): the original run at the strict 0.8 bar

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
