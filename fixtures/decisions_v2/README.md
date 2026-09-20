# Decision-turn fixture v2 (`turns.v2`)

`turns.v2.jsonl` is **102 records** produced by driving the real coordinator
gateway (`CommandGateway`, via `tests/coordinator/p3_helpers.py`'s `P3Run`,
following the same never-hand-written-state method as v1's
`tests/coordinator/turn_generator.py`). It extends v1
(`fixtures/decisions/turns.v1.jsonl`, 53 records, `decision_packet.v1`) with the
judgment surface P3a added on top: every record here carries `packet` in the
**v2** shape (`decision_packet.v2` — adds `queue`, `detection`, `assignment`
facts to v1's `run`/`operator_request`/`facts`) plus a new `rule_choice` field.

**Regenerate** with:

```
SENTINEL_REGEN_TURNS_V2=1 uv run python -m pytest tests/coordinator/test_turn_fixture_v2.py -q -o addopts=''
```

Regeneration keeps whatever `label`/`label_source`/`rationale` is already
committed for a given `turn_id` (matched by id, not position), so an
operator's approved relabelling at gate **G-labels-v2** survives a
regeneration that only refreshes `packet`/`candidates`/`rule_choice`.

## What's in it

| Group | Decision point | Count | How built |
|---|---|---|---|
| Exact per-plan match | `SELECT_PLAN` (vs the **draft** catalogue) | 42 (14 plans × 3 phrasings) | `RUN_CREATE` with request text naming exactly one draft-catalogue plan's coverage |
| Ambiguous / out-of-catalogue / contradictory | `SELECT_PLAN` | 5 + 5 + 4 = 14 | request text that names no plan, or names a place outside any catalogue, or contradicts itself on scope |
| **Pending slots** | `SELECT_PLAN` | 24 | placeholders — see below; **excluded from every count and from scoring** |
| Duplicate detection | `EVIDENCE_GATE`, `rule_choice is None` | 8 | a new detection at the same station as an already-tasked one |
| Near-tie ranking | `ASSIGN`, `rule_choice is None` | 6 | `SimulatedPatroller.rank_for_task` scores within `ASSIGNMENT_CLEAR_GAP` of each other |
| RULE turns | `WAIT_APPROVAL`, `WAIT_OBSERVATION`, `EVIDENCE_VETO`, `RANK`, `ASSIGN` (clear gap), `EVIDENCE_GATE` × 3 (clean create / out-of-plan low / in-plan below 0.75) | 8 | one per rule outcome, `label_source: "rule"`, label = `rule_choice` when the rule set one, else the sole legal candidate |

Total: 56 scored `SELECT_PLAN` + 24 pending + 8 duplicate + 6 near-tie + 8 rule
= **102**. There are **no `REPLAN` turns** — see below.

## Why the counts are what they are, not rev 1's 80/80/60/60 (blocker A12)

`DecisionState.to_packet_v2()` carries **bands and booleans**, not raw values
(`score_band`, `inside_plan`, `rank_gap_band`, …), so the number of genuinely
distinct reachable packets is small regardless of how many raw scenarios are
driven through the gateway. `SELECT_PLAN` is the exception: `packet.operator_request`
is the raw request text itself, so every distinct phrasing is a distinct
packet by construction — that is why 56 scored requests are all kept, but
duplicate/near-tie turns collapse hard:

- **Duplicate detection**: the plan's own table asks for score × station ×
  earlier-task-ranked × extra-pending-detection = 3 × 2 × 2 × 2 = 24 raw
  combinations. The generator (`tests/coordinator/turn_generator_v2.py`,
  `_generate_duplicate_turns`) builds all 24 and keeps only the turns whose
  `(decision_point, packet, candidates)` triple is new. Two collapses happen:
  the two raw scores 0.90 and 0.99 both land in the `very_high` score band
  (only 0.78/`high` differs), and station (`LEFT_ALLEY_MIDDLE` vs
  `RIGHT_ALLEY_MIDDLE`) never appears in `detection_facts` at all — both are
  inside the approved plan, so it changes nothing in the packet. That leaves
  2 (score band) × 2 (ranked) × 2 (extra pending) = **8** distinct states,
  exactly the plan's stated minimum.
- **Near-tie ranking**: 3 gap bands (`tie`, `very_close`, `close`) × 2
  patroller counts (2, 3) = **6** distinct states — every combination here is
  genuinely distinct, since `packet.assignment.patrollers_ranked` differs by
  count and `rank_gap_band` differs by gap. No collapse, no padding needed.

Nothing here is padded to hit a number: `_dedupe_and_relabel` in
`turn_generator_v2.py` drops a raw combination outright if its triple already
occurred, and the fixture reports the number that survived, not a target.

## The headline result: the rich catalogue deletes a whole escalation class

v1's fixture (`fixtures/decisions/turns.v1.jsonl`, gate `G-labels`, approved
2026-09-20) escalates every single-station request (`sp-001`..`sp-006`)
because **no v1 catalogue plan covers exactly one station** —
`PLAN_ROUTE_ONLY_2_ZONE`, the narrowest v1 plan, visits two.

Against the **draft** rich catalogue (Task 1,
`fixtures/catalogue_draft/survey-catalogue-v2.draft.json`), the same kind of
request has an exact match: `PLAN_ROUTE_LEFT_ALLEY_FRONT`,
`PLAN_ROUTE_LEFT_ALLEY_MIDDLE`, `PLAN_ROUTE_LEFT_ALLEY_BACK`,
`PLAN_ROUTE_RIGHT_ALLEY_FRONT`, `PLAN_ROUTE_RIGHT_ALLEY_MIDDLE`, and
`PLAN_ROUTE_RIGHT_ALLEY_BACK`. All 18 turns naming one of these six plans'
coverage (`sp2-001`..`sp2-018`, 6 stations × 3 phrasings) are hand-labelled
with the matching plan id, **not** `escalate`, and each of their rationale
strings says so explicitly. This is the sharpest evidence for proposal item
**P-3** (adopt the rich catalogue): it is not a marginal improvement, it
deletes an entire class of escalation that the operator otherwise sees on
every single-station request. Put this in front of the operator at gate
**G-labels-v2**.

## The 24 pending slots

`RUN_CREATE`'s `request_text` is required and non-empty (wire schema,
`minLength: 1`), so a genuinely empty request cannot be built through the
gateway. Each pending slot (`sp2-pending-01`..`sp2-pending-24`) is instead
built from a placeholder run (whose placeholder text is discarded, never
scored) and then has `packet.operator_request` set to `null` and
`label_source: "operator_pending"`, `label: null`, `rationale: null`. All 24
therefore carry an identical `packet`/`candidates` pair (the neutral, no-plan
`SELECT_PLAN` state against the 14 draft candidates + `escalate`) — this is
the one place in the fixture where the `(decision_point, packet, candidates)`
uniqueness rule is deliberately not enforced, because these rows are not
reachable states to be distinguished; they are blank rows waiting for the
operator's own request text.

**Gate G-labels-v2 (Task 10, Step 5) asks the operator to write the 24 real
request texts** (and to choose their intended plan/escalate label) directly
into these slots. Until then they are excluded from every count in this
README, from `test_per_point_minimums`, and from the uniqueness check in
`tests/coordinator/test_turn_fixture_v2.py`.

## No `REPLAN` turns

Task 9 (`REPLAN`, an out-of-plan detection triggering a new plan proposal)
was implemented and then **dropped** (commit `8248355`): it degenerated (the
six-station plan is a superset of every real out-of-plan station, so the
"narrow" rule became the general case), and it overrode an operator-approved
`escalate` label for out-of-plan detections. See
`src/sentinel/coordinator/replan.py`'s module docstring for the full
reasoning. `REPLAN` is **not a decision point on this branch** —
`legal_set` never returns it, `ADVISORY_POINTS`/`JUDGMENT_POINTS` do not
mention it, and this fixture generates zero turns for it. There is no
minimum asserted for it in `test_turn_fixture_v2.py`, and none should be
added until P3a-2 (behind proposal item P-5) revisits the trigger.

## Known gaps (carried over from v1)

- **No pose or staleness facts.** Neither `to_packet()` nor `to_packet_v2()`
  carries drone pose, station-observation staleness, or a "last seen"
  timestamp. WP5 is expected to add these.
- **No weather source.** Nothing in `DecisionState` or either packet version
  conditions on weather.
- **`repeat_of_handled` is untested here.** Reaching a task with status
  `ASSIGNED` or `CLOSED` alongside a pending detection requires the run to
  survive past its first confirmed assignment, which blocker A1 (the run
  completes on the first confirmed assignment) makes unreachable through the
  real gateway today — exactly as `tests/coordinator/test_decision_state_multi.py`'s
  `test_repeat_of_handled_on_a_constructed_state` documents for the read
  model itself. Every duplicate turn in this fixture instead exercises
  `duplicates_open_task`, which is reachable.
