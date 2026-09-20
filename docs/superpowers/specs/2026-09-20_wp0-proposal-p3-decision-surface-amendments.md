# WP0 proposal — amendments the P3 decision surface needs (consolidated)

**Status:** PROPOSAL. Awaiting the operator's Coherence consent. **Nothing here is implemented; no agent may self-grant any part of it.**
**Written:** 2026-09-20, by the P3 planning session, from a code reading of `feat/p2-shadow-advisors` @ `620e8ac`.
**Supersedes:** the separate proposals P3a originally planned (`…rich-survey-catalogue`, `…detection-disposition`, `…replan`, `…closer-look-design`) and the single-zone catalogue entry proposal. One document, one consent conversation.

## Why one document

The operator chose (2026-09-20) that `CommandGateway` stays untouchable and every gateway/schema change is proposed once. The code reading found that **three of the four decision types the operator wants E to own are blocked end-to-end by the gateway or its contracts**, not by missing coordinator logic. P3a builds everything reachable today; each item below unlocks one blocked slice. Items are independent: consent can be given per item.

## Evidence table (all verified by reading the code, file:line at `620e8ac`)

| # | Blocker | Where | What it blocks |
|---|---|---|---|
| A1 | Run becomes `COMPLETED` on the **first** confirmed assignment (`assigned >= 1`); terminal runs reject every ordinary command | `command_gateway.py:414-418`, `:139-142` | Multi-detection runs: the second task is stranded `PROPOSED` forever |
| A2 | `AssignmentService.propose` rejects any patroller that is not the persisted rank 1 | `tasks.py:78-83` | Patroller choice: rank 2/3 can never be proposed |
| A3 | `CommandTaskRankPayload` is `additionalProperties:false` with only `task_id`; the gateway then ranks with the default `{"PATROLLER_1": 0.0}` | wire schema `CommandTaskRankPayload`; `command_gateway.py:360` | Near-ties cannot occur through the gateway: there is always exactly one patroller with score 0.0 |
| A4 | Catalogue schema: `station_steps.minItems = 2`; `mode` enum has two values; `MARKER_VERIFIED` ⇒ exactly 6 stations; `ROUTE_ONLY_2_ZONE` ⇒ exactly the two middle zones | `survey-catalogue-v1.schema.json` (`survey_plan_entry.allOf`) | Under v1 **exactly two plans are expressible**. No single-station, per-side or pair plan can validate |
| A5 | `mode` is also pinned in the wire schema (`PlanReadModel`, `EventPlanProposedPayload`, `EventPlanCreatedPayload`) and in the DB (`plans.mode CHECK (mode IN ('MARKER_VERIFIED','ROUTE_ONLY_2_ZONE'))`, mirrored in `contracts/ddl_manifest.json`) | wire schema; `sql/schema.sql:112` | Any new mode needs a wire, DDL and manifest change |
| A6 | The gateway hardcodes the v1 fixture path, and `CatalogueResolver.from_fixture` loads the schema from `parents[1]/survey-catalogue-v1.schema.json` | `command_gateway.py:51-53`, `catalogue.py:31` | A draft catalogue can feed `legal_set` (via `load_catalogue_plans(path=…)`) but can never be proposed through the gateway |
| A7 | No command records that a detection was handled without a task. `finish(NEEDS_REVIEW)` is not a gateway command and persists nothing | command enum (12 types) | (a) "dismiss as duplicate" cannot exist; (b) **head-of-line blocking**: a vetoed or escalated detection stays pending forever and the queue re-presents it on every resume |
| A8 | `USER_MESSAGE_RECEIVED` is only appended inside `RUN_CREATE` | `command_gateway.py:83-95` | No mid-run operator message, so replanning can only be triggered by an out-of-plan detection |
| A9 | `plan_id = uuid5(run_id:entry_id)`, `plans.plan_id` is the primary key and `plans.plan_sha256` is UNIQUE | `command_gateway.py:241`, `schema.sql:107-111` | Re-proposing the same catalogue entry after a rejection raises a raw `sqlite3.IntegrityError: UNIQUE constraint failed: plans.plan_sha256` (not a `CoordinatorError`), which escapes `AgentLoop`'s corrective path. **Reproduced 2026-09-20.** |
| A13 | `read_decision_state` takes the **latest** plan row and sets `plan_approved` from **any** `PLAN_APPROVED` event | `decision_state.py:73-75` | After a mid-run proposal is **rejected**, the read model reports the rejected plan's stations as the approved plan (reproduced: 6 stations, `approved=True` after rejecting the 6-station plan). Not a gateway change: P3a Task 3 fixes it in the read model |
| A10 | `AgentFinishArguments.status` is `DONE / NEEDS_REVIEW / FAILED` | wire schema | The original P3a `finish(COMPLETED)` wrap-up candidate is schema-invalid; use `DONE` |
| A11 | No choreography, command or event for re-flying a station | `WorkerPort`, command enum | Closer look / revisit |
| A12 | The decision packet has no fact that separates "same rock seen twice" from "second hazard at the same station" (no capture time gap, no bounding box, no image similarity), and no patroller attributes (skill, distance, load) | `decision_state.py` | The duplicate and near-tie judgments are **information-thin**: distinct packets number in the tens, so P3b's "n ≥ 60 HIGH picks per point" cannot be met with distinct turns |

## Proposed amendments

Each item: the change, the files that change on consent, and what must be re-verified.

### P-1 Run completion rule (unblocks multi-detection) — code only, no schema change
- Change `OPERATOR_DECIDE/TASK/CONFIRM`: the run becomes `COMPLETED` only when **no task of the run is open** (`status NOT IN ('ASSIGNED','CLOSED','REJECTED')`) **and no actionable detection is pending**; otherwise `RUNNING`.
- Files: `command_gateway.py` (one block), its guardrail tests.
- Re-verify: `test_one_station_flow`, `test_vertical_slice`, the v1 turn fixture golden test, the rehearsal script. Single-detection runs must complete exactly as today.
- Operator-load consequence to decide: N tasks need N assignment approvals, so a clean two-detection run costs **3** touches, not 2. Either accept "approvals only = one approval per assignment", or add a batch approval (a schema change; not proposed here).

### P-2 Patroller choice — code + schema
- `AssignmentService.propose` accepts any patroller **present in the persisted ranking** (not only rank 1); the chosen `rank_no` is recorded in `proposal_reason`.
- `CommandTaskRankPayload` gains optional `scores: {patroller_id: number}` **or** (preferred) ranking scores come from a configured patroller roster fixture, so the agent never supplies scores. Decide which.
- Files: `tasks.py`, wire schema (if `scores`), a roster fixture, tests.
- Depends on A12: without patroller attributes the choice is only a score gap.

### P-3 Rich survey catalogue (v2) — schema + fixture + wire + DDL
- New catalogue schema `survey-catalogue-v2`: `mode ∈ {MARKER_VERIFIED, ROUTE_ONLY}`; `station_steps` 1–6; `MARKER_VERIFIED` requires a `marker_id` on every step (any count); `ROUTE_ONLY` requires `marker_id: null`.
- 14 entries, named by coverage, never by count (draft: `fixtures/catalogue_draft/survey-catalogue-v2.draft.json`, built by P3a Task 1):
  - six single-station route plans `PLAN_ROUTE_<STATION>`;
  - `PLAN_ROUTE_LEFT_ALLEY`, `PLAN_ROUTE_RIGHT_ALLEY`;
  - `PLAN_ROUTE_FRONT_PAIR`, `PLAN_ROUTE_MIDDLE_PAIR`, `PLAN_ROUTE_BACK_PAIR`;
  - `PLAN_VERIFIED_LEFT_ALLEY`, `PLAN_VERIFIED_RIGHT_ALLEY`, `PLAN_VERIFIED_FULL_SITE`.
- Migration: `PLAN_ROUTE_ONLY_2_ZONE → PLAN_ROUTE_MIDDLE_PAIR`, `PLAN_MARKER_VERIFIED_6 → PLAN_VERIFIED_FULL_SITE`. Old ids stay resolvable as aliases for one revision so that the v1 turn fixture and the demo scripts keep working.
- Files on consent: the catalogue schema + fixture (new revision, real sha256 values), wire schema `mode` enums (3 places), `sql/schema.sql` CHECK, `contracts/ddl_manifest.json`, `command_gateway._catalogue_fixture`, `catalogue.py:31` schema path, `legal_tools.load_catalogue_plans` default path.
- Re-verify: `legal_set` SELECT_PLAN size (2+1 → 14+1), the v1 turn fixture (regenerate + relabel = a **G-labels** gate), any live sweep (paid gate).

### P-4 Detection disposition — schema
- New command `DETECTION_DISPOSE {perception_result_id, disposition ∈ {DUPLICATE, REVIEWED_NO_TASK}, reason}` → event `DETECTION_DISMISSED`. Operator or agent actor; agent use is a legal candidate only at the triage point.
- Unblocks: the `dismiss_duplicate` candidate, clearing vetoed/escalated detections (fixes A7 head-of-line blocking), `keep_current_plan` at REPLAN.
- Files: wire schema (command + event enums, two payload defs), `command_gateway.py`, a `detection_dispositions` table (DDL + manifest), `decision_state.py` (exclude disposed detections).

### P-5 Mid-run operator message — schema  ⬅ **now the only viable replan trigger**
- New command `OPERATOR_MESSAGE {request_text}` → appends `USER_MESSAGE_RECEIVED`. Unblocks the message-triggered REPLAN.
- **Raised in priority by a P3a finding (2026-09-20, commit `8248355`).** P3a tried to deliver replanning without this, using the only trigger today's gateway allows: an out-of-plan detection with a covering catalogue plan. It was implemented as specified and then dropped, for three reasons, each pinned by a test in `tests/coordinator/test_replan.py`:
  1. **It degenerates.** `PLAN_MARKER_VERIFIED_6` holds all six stations, so it is a superset of every station set; `covering_plans()` is non-empty for *every* real out-of-plan station. Adopting the rich catalogue (P-3) does **not** fix it — `PLAN_VERIFIED_FULL_SITE` is a superset too. Any future covering-set rule needs a tighter criterion: smallest covering plan, a bound on added stations, or an explicit instruction.
  2. **It contradicts an approved label.** Out-of-plan detections are labelled `escalate` in the v1 fixture, approved 2026-09-20. Wiring the trigger broke the frozen golden fixture (1 failure → 10 errors) in a P2-owned file now merged to master, which would have altered the demo path before the 2026-09-25 freeze.
  3. **It is the wrong trigger on the merits.** `INSPECTION_DISPATCH` is plan-gated, so an out-of-plan detection is an anomaly in the evidence rather than a change of intent. Answering an unexpected artifact by proposing a *larger* mission is silent scope growth; escalating to a human is safer.
- The gateway half already works: a mid-run `AGENT_INTENT_PROPOSE` is accepted, and Task 3's read model keeps the old plan in force until the new one is confirmed. **What is missing is only a legitimate trigger, i.e. this item.** Granting P-5 is therefore what actually delivers "replan mid-run", one of the four capabilities the operator selected on 2026-09-20.

### P-6 Re-proposal after rejection — ✅ RESOLVED ELSEWHERE, no longer requested here
- The operator released the gateway for this one defect and the P1/P2 session implemented it (`1ba6bfb`, branch `fix/gateway-integrity-error`, **unmerged** as of 2026-09-20). `CommandGateway.submit` now catches `sqlite3.IntegrityError` only — no bare except — mapping the plans-table case to `CoordinatorError("PLAN_ALREADY_PROPOSED")` and anything else to a distinct `STATE_CONSTRAINT_VIOLATION`.
- **That release was granted to that session for that defect. It does not extend to P3, which still treats `CommandGateway` as untouchable.**
- **Scheduled test flip:** P3a Task 3's `test_characterization_reproposing_an_entry_raises_a_raw_integrity_error` asserts the *raw* `sqlite3.IntegrityError`. It is correct today and becomes wrong the moment `1ba6bfb` reaches a branch P3 builds on. Flip it to expect `CoordinatorError("PLAN_ALREADY_PROPOSED")` at that point — not before, or the two branches assert contradictory behaviour.

### P-7 Closer look — needs an operator answer first (gate G-closer-look)
- Question: does a revisit choreography exist, or is it wanted, given WP5 (pose) is pending?
- If yes, it needs: `WorkerPort.dispatch` with a `revisit` choreography; a command `INSPECTION_REVISIT {section_id, mission_id, reason}` (or a `purpose` field on `INSPECTION_DISPATCH`); a decision point `REVISIT` for the marginal band (0.5–0.75, inside the plan, not a duplicate) with candidates `closer_look / create_task / escalate`; a bound of one revisit per station per run; a second marginal result escalates. The static veto and the flight envelope are unchanged: a revisit is an ordinary dispatch inside the approved plan.

### P-9 Pose-aware navigation: recover positioning before acting — **design together with P-7**

Operator idea, 2026-09-20: instead of always routing to one fixed point, the drone should notice when its positioning is weak and proceed carefully to re-establish an accurate fix from the tags.

**Feasibility: better than expected. The hard parts already exist.**

| Need | Status |
|---|---|
| A measurable accuracy signal | ✅ exists. `drone-pose-report` carries `reprojection_error_px`, per-tag `distance_m`, `zone_id` and tag count, plus read-time staleness. Test A measured median = p95 = 4.4 cm at 0.23 px reprojection — precision is excellent and the residual is a systematic bias, not noise |
| A way to move "carefully" | ✅ expressible today. `djitellopy_backend` exposes `set_speed(cm_per_second)`, `move(direction, distance_cm)` and `rotate(direction, degrees)`. "Careful" = lower speed + shorter steps + re-observe between steps. **No new control mode and no continuous RC servo loop is needed** |
| A number for "weak" | ❌ **missing, and this is the real blocker.** WP5's Test B (static accuracy at many ground-marked points against the 6-tag map) has not run. Until it has, any threshold for "positioning is degraded" is a guess |
| Merged data | ❌ `master` has none of WP5. `src/sentinel/pose/*`, the pose contracts and the tag map live only on `feat/sentinel-wp5-drone-pose` |

**Proposed shape, once Test B gives the numbers.** Two layers, kept apart:

- **Worker layer (WP5/WP4, not the coordinator):** a creep-and-check routine — short `move` at reduced `set_speed`, re-observe tags, re-estimate, repeat until the fix is good or a step budget is spent. **Safety-critical default: prefer rotation over translation when the fix is poor.** A drone that translates *because* it is lost is more dangerous than one that holds position and turns to reacquire tags. Rotation is cheap, stays inside the envelope (±50 cm x/y), and is the natural recovery when tags have simply left the frame. Translate only once a fix is re-established; escalate if the budget is spent.
- **Coordinator layer (P3-shaped):** pose confidence becomes facts in the decision packet (`pose_fix_band`, `tags_visible`, `pose_stale`), and a decision point `POSE_DEGRADED` offers `relocalize_in_place` / `continue` / `escalate`. A rule settles the clear cases (good fix → continue; no tags at all → escalate); the marginal band is a genuine judgment point.

**Why this is worth more than it looks:** unlike the duplicate and near-tie judgments, this decision is **information-rich** — continuous error metrics, tag geometry and staleness, rather than the coarse bands of blocker A12. It is the first P3 judgment point where an advisor would have real signal to work with, so it is the most promising candidate for a P3b promotion study.

**Dependencies and sequencing:** needs WP5 merged, Test B complete, an `INSPECTION_RELOCALIZE`-style command or a `purpose` field on `INSPECTION_DISPATCH`, and the pose report surfaced in the decision read model.

**Design it with P-7 (closer look).** Both are "go back and observe again under better conditions", and both need a revisit choreography on `WorkerPort`. Designed separately they would produce two incompatible mechanisms for the same manoeuvre; designed together, the closer look is just a re-observation whose trigger is evidence quality rather than pose quality. **Recommend one combined design pass covering P-7 and P-9, after P3 lands and WP5 Test B reports.**

### P-8 Richer decision facts (A12) — read-model only once data exists
- Duplicate judgment: capture time gap band, bounding-box overlap band. Near-tie judgment: patroller skill / distance / load. Needs WP3/WP5 data sources; listed so the promotion criteria are not applied to an information-thin point.

## What P3a builds without any of this

Multi-detection read model; oldest-first triage queue up to the first assignment; the duplicate judgment point; near-tie detection at ASSIGN with candidates `assign_rank_1 / escalate`; the approved-plan read-model fix; REPLAN on an out-of-plan qualifying detection (the gateway already accepts a mid-run proposal); the draft catalogue at `legal_set` level only; the v2 turn fixture over reachable, distinct states; the operator-load report. Characterization tests pin A1, A2 and A9 so that a later consent flips a named test instead of surprising anyone.
