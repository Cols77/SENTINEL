# Handoff: room agent, larger cube, sweep captures, coverage and the Jev prompt

**For:** Codex
**Date:** 2026-09-24
**Demo:** 2026-09-25, so the goal is tomorrow's demo. Aim for low-hanging fruit and working behaviour, not a redesign.

**Where to work:**
- Start from branch `claude/room-agent-ui` at `c4c6c02` (worktree `C:\coding\sentinel\.worktrees\jev-agentic-room-loop`).
- Create your own branch from it, for example `codex/room-agent-coverage`.
- Do not push to `claude/room-agent-ui` or `feat/jev-agentic-room-loop`.

## 1. What the room agent is (read this first)

The room agent is the "Room agent (Jev adaptive)" architecture in Mission Control.

- **The operator** types a request, for example "count the phones and computers in the room".
- **The goal compiler** (`src/sentinel/room/goals.py`) turns the request into objectives. Each one is a Jev fixed-choice question.
- **The controller** (`src/sentinel/room/controller.py`) runs turns. On each turn it builds a list of **legal candidates** from code, then asks Jev to pick one (`controller.py:808-813`). Jev never plans coordinates. It only chooses among candidates.
- **The safety supervisor** (`src/sentinel/room/supervisor.py`) admits or refuses every physical action. It checks the battery reserve, the drift forecast, the route home and the landing contingency.

**What an "observation point" is.**
- It is a *declared pose*: a semantic name such as `left-rear-middle-toward_targets`. The parts are horizontal (left/center/right), depth (rear/middle/front), height (bottom/middle/top) and look (toward_targets/left/right).
- The name is resolved against the site's observation volume (`src/sentinel/room/semantic.py`, `resolve_pose_id`).
- The site file lists which poses are legal (`pose_visibility`), and for each pose which `coverage_cells` it sees. Both are hand-written today.
- `build_observation_candidates` (`src/sentinel/room/candidates.py:402`) offers one OBSERVE candidate per listed pose that still resolves an unresolved (cell, class) pair.
- Choosing a candidate does three things:
  - `make_resolve_checkpoint` (`src/sentinel/room/wiring.py:93`) plans the flight to the pose;
  - the aircraft captures **one** frame there (`wiring.py:58`, `_SINGLE_CAPTURE_COUNT = 1`);
  - `make_perception_capture` (`wiring.py:213`) reads **one** frame after arrival and sends it to the detector (Gemini 2.5 Flash via OpenRouter).
- Detections are placed on support planes by `CountGeometry` (`src/sentinel/room/grounding.py:54`, built in `wiring.py:170`, camera `TELLO_MEASURED`: fx ≈ 893, 8.6° down pitch).
- Coverage and counts are rebuilt every turn from the session's durable events in `runs/<run>/session.sqlite3`. Explored cells are therefore already tracked, and a pose is never offered twice.

**Why there are only 3 points today.** `fixtures/room_agent/v1/canningst-site.json` lists three poses: left, center and right, all `rear-middle-toward_targets`. It also has three hand-drawn strips on the demo table (y 190–270). Jev cannot explore anything else.

## 2. State of the live system (what already works on the real Tello)

On 2026-09-24 there were eight real flights, all started from Mission Control (Playwright MCP) and all landed cleanly. Fixes made during them (all committed):

| Commit | Fix |
|---|---|
| `050c3c2` | Takeoff is no longer gated on a tag fix from the floor; the camera sees no tags from the pad. The airborne home burst gives the first fix (`supervisor._declared_pad_pose`, `live_stack.measure_home`). The home is wherever the tags place the aircraft (`live_stack.HOME_TOLERANCE_CM = 150`). |
| `c4c6c02` | An OBSERVE checkpoint is re-planned from the refreshed pose. It used to land on `STALE_CHECKPOINT_START`: `supervisor.prepare(replan=...)`, passed from `controller._execute_observation`. The CanningSt room-site cube grew backward and the observation volume widened. |

Proven on the real aircraft:
- takeoff;
- the airborne two-tag fix;
- Jev goal compilation;
- Jev choosing "Observe right region";
- the energy guard sending the aircraft home;
- landing in place.

**Not yet proven live:** a complete observation (capture → detector → count → answer). The last flight started at 49 % battery and was correctly sent home. Each observation needs about 55–60 % battery before it is admitted.

**Known test debt: 11 failures on `c4c6c02`.**
- Ten are in `tests/room/test_canningst_site.py` and `tests/room/test_canningst_mission.py`. Their expectations were computed from the old site geometry and battery thresholds.
- One is `tests/room/test_cli.py::test_live_default_flight_stack_shares_the_probe_connection_and_never_takes_off_without_tag_poses`, which asserts the old rule that takeoff needs a pad fix. It now takes off and lands if no airborne fix comes.
- Update all 11 to the new site, with a brief reason in each.

## 3. The measured free space (from the operator, 2026-09-24)

**Frame:**
- the tags are at y = 400 cm (`docs/wp5-flight3-map.json`): tag 2 at x 215.6 and tag 0 at x 284.4, 53 cm high, facing −y;
- the aircraft faces +y (heading 90°);
- units are cm.

| Axis | Measured free space | Use in the site file |
|---|---|---|
| Depth (y) | 291 cm from the tag wall, so y 109–400 | flight volume y 140–360 (30 cm margin; 40 cm from the wall) |
| Width (x) | 320 cm centred on x 250, so x 90–410 | flight volume x 120–380 |
| Height (z) | clear from 70 cm to 300 cm of true height (below 70 cm the free space shrinks) | see below |

**Height:**
- The Tello's height reading is 30–40 cm low: it reads about 40 cm right after takeoff, when the true height is about 70–80 cm.
- The site z values are compared against that reading, so use a floor of **40** and a ceiling of **250** in the site file.
- Say so in `fixtures/room_agent/v1/canningst-site.PROVENANCE.md`.

**Where things are:**
- The demo surface is at z 50 (support plane `demo-surface`).
- The cube, corridor and landing polygons are also in `canningst-site.json`.
- Keep `cube`/`flight`/`home-corridor`/landing polygons consistent with each other.
- Keep the home route checkpoint and the `home.nominal_pose` inside them.

## 4. Work to do (in order)

### Task 1: site geometry and more observation points
- Resize the volumes in `canningst-site.json` as in §3.
- Declare about **8 poses** in `pose_visibility` and `pose_tag_visibility`:
  - left/center/right × rear/front, all `toward_targets`;
  - `center-middle-middle-left` and `center-middle-middle-right`, the side looks.
- `pose_tag_visibility` should list tags 0 and 2 only for poses that face the tags and are within about 3 m of them. Side looks see no tags. That field drives the expected-re-anchor credit in the supervisor.
- **Acceptance:** `RoomSite.validate` passes; `resolve_pose_id` resolves every declared pose inside the flight volume; `room_live_prepare.py --new-run` succeeds.

### Task 2: coverage from the camera model, not by hand
- Write `scripts/room_site_coverage.py`. It generates `coverage_cells` and `pose_visibility`:
  - a grid of about 40 cm cells over the demo surface (and, optionally, the floor plane);
  - for each declared pose, and for each sweep heading (Task 3), a cell counts as visible when its centre projects inside the image with `TELLO_MEASURED` (use the existing `sentinel.camera`/`grounding` code) and is within a detection range of about 250 cm.
- Write the result into the site JSON. Commit the generator and its output.
- **Acceptance:** re-running the script is deterministic; every cell is visible from at least one pose, or is dropped and logged.

### Task 3: a left–centre–right sweep at each pose, every frame to perception
- At each observation pose, capture at yaw −30°, 0° and +30° relative to the pose's look.
- `plan_checkpoint` already takes `looks_deg` and `capture_count`, but it must end at the canonical heading. `_retarget_terminal_heading` (`wiring.py`) then adds the final rotation to the pose yaw.
- The controller checks that the checkpoint ends at the declared pose (`controller.py:1017` and `:1033`). Keep the sweep's end heading equal to the pose yaw.
- **Every frame of the sweep must reach the detector.** Today `perception_capture` reads a single frame after arrival and sends one `PerceptionRequest`, and the count path ingests one observation per OBSERVE.
  - Simplest route: capture each look's frame during the checkpoint. `live_stack._capture` (`live_stack.py:644`) already saves a frame per CAPTURE command and returns its observation ID.
  - Then submit one perception request per frame, tagged with **the heading at capture**, so `CountGeometry` places detections correctly for that heading.
  - Check that duplicates across overlapping frames don't double-count. The count logic already de-duplicates by placed position and cell; verify it with overlapping sweep frames.
- **Acceptance:** in `JEV_REHEARSAL`, one OBSERVE produces three frames, three detector results and one merged count; the re-anchor log shows the yaw rotations.

### Task 4: a real per-turn prompt for Jev
- Replace the instruction at `controller.py:812` ("Choose one legal action. Reads are historical. Never compose factual values.") with a mission-aware instruction. It should contain:
  - the operator's request and the current objective(s);
  - which coverage cells or classes are still unexplored, and how many are explored;
  - localization health (already in the state);
  - the rule: *prefer observing unexplored zones; answer only when no admissible observation adds coverage; re-anchor when the position is stale; escalate only when nothing else can make progress.*
- Improve the candidate descriptions in `candidates.py` so each OBSERVE candidate states what it adds, for example "resolves 3 unexplored cells on the left of the table".
- Keep the fixed-choice contract: Jev returns a candidate ID, never coordinates.
- **Acceptance:** in the real-Jev rehearsal, Jev does not choose Escalate on the first turn of a count and keeps observing until coverage is complete or the battery sends it home. In the one live run where Jev had a turn, it chose Escalate first at p 0.36.

### Task 5: tests
- Fix the 11 failing tests (§2).
- Add tests for the generator (Task 2), the sweep (Task 3: three captures, three requests, headings recorded) and the prompt (Task 4: unexplored cells appear in the instruction).
- Run `tests/room`, `tests/cube` and `tests/ui` with the venv below. Node tests: `node --test tests/survey_console/room-agent.test.mjs`.

## 5. How to run and check

**Environment:**
- Python: `C:\Users\33630\AppData\Local\Temp\claude\C--coding-sentinel\2541e4bd-1eaa-4102-a60d-558629bb5271\scratchpad\ui\venv-c0\Scripts\python.exe`. The shared `.venv` lacks nicegui and fastapi.
- Always set `$env:PYTHONPATH = "src;."`.

**Rehearsal check (fake drone, real Jev; paid OpenRouter calls):**

```powershell
python -m sentinel.ui --mode rehearsal --port 8793 --data-dir artifacts/mc-coverage --i-authorise-paid-calls
```

Then open `http://127.0.0.1:8793/console/`: Room agent (Jev adaptive) → `JEV_REHEARSAL` → "count the phones and computers in the room" → Propose run → Approve and fly.

- The selectors are listed in `tests/ui/e2e/room_agent_e2e.md`.
- Note that the rehearsal fake drone never produces a new tag fix between planning and admission. The live-only races in §2 are invisible there.

**Live:**
1. Run `python scripts/room_live_prepare.py --run-root runs --local-ip 192.168.10.2 --new-run`. Today's filming consent is already recorded.
2. Start Mission Control Live on **port 8790**:

   ```powershell
   python -m sentinel.ui --mode live --site-profile CanningSt --data-dir artifacts/room-live-N --room-live-config runs/live.pack.json --i-authorise-paid-calls --port 8790
   ```

   Ports 8771–8773 and 8776 are other Mission Control instances already running; leave them alone.
3. Re-run prepare before each flight. Site assurances last 30 minutes, and a run that has flown is never reused.

The steps are in `docs/room-agent-morning-flight.md`. Live flights are the operator's call; do not fly without them.

## 6. Constraints

- **Safety:**
  - Never widen a volume beyond the measured free space in §3. The supervisor trusts the site file as measured clearance.
  - Landing must never depend on localization. Today a LOST pose still sends `land` through the executor, never a motor stop. Keep it that way.
  - Keep the two-tag rule for a position fix: a single tag is not a fix.
- **Battery:** the sweep adds about three rotations and two captures per pose. Expect about 60–65 % needed before each observation is admitted, and about 3 poses per full pack. Report the new figure.
- **Scope:** keep changes additive where possible; the Mission Control UI (`handoff/lightcode-mission-control-docs`) is Codex's own branch and is merged forward into `claude/room-agent-ui`. Do not delete branches or worktrees.

## 7. Longer term (not for tomorrow)

- **Coverage from real captures:** mark cells seen from the tag-measured pose and heading of each actual frame, not the planned pose. Handle partial views and occlusion.
- **Free viewpoint choice:** sample candidate views around the free volume, score each by new cells seen per battery percent, and offer Jev the top few.
- **A coverage map in Mission Control,** plus cross-view tracking of detections so the same phone is never counted twice.
- **Continuous low-rate perception** on hover video, not only at planned captures.
