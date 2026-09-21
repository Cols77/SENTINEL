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
rtk proxy C:/coding/sentinel/.venv/Scripts/python.exe -c "import os,pytest; os.environ['SENTINEL_REGEN_TURNS_V2']='1'; raise SystemExit(pytest.main(['tests/coordinator/test_turn_fixture_v2.py','-q']))"
```

Regeneration keeps whatever `label`/`label_source`/`rationale` is already
committed for a given `turn_id` (matched by id, not position), so an
operator's approved relabelling at gate **G-labels-v2** survives a
regeneration that only refreshes `packet`/`candidates`/`rule_choice`.
Generated rows instead take their requests and expected labels from
`generated-requests.v1.json`; regeneration replaces the old empty slots.

## What's in it

| Group | Decision point | Count | How built |
|---|---|---|---|
| Exact per-plan match | `SELECT_PLAN` (vs the **draft** catalogue) | 42 (14 plans × 3 phrasings) | `RUN_CREATE` with request text naming exactly one draft-catalogue plan's coverage |
| Ambiguous / out-of-catalogue / contradictory | `SELECT_PLAN` | 5 + 5 + 4 = 14 | request text that names no plan, or names a place outside any catalogue, or contradicts itself on scope |
| Generated requests | `SELECT_PLAN` (vs the **draft** catalogue) | 24 | 16 supported requests spanning all 14 plans, plus 8 escalation boundaries; `label_source: "generated"` |
| Duplicate detection | `EVIDENCE_GATE`, `rule_choice is None` | 8 | a new detection at the same station as an already-tasked one |
| Near-tie ranking | `ASSIGN`, `rule_choice is None` | 6 | `SimulatedPatroller.rank_for_task` scores within `ASSIGNMENT_CLEAR_GAP` of each other |
| RULE turns | `WAIT_APPROVAL`, `WAIT_OBSERVATION`, `EVIDENCE_VETO`, `RANK`, `ASSIGN` (clear gap), `EVIDENCE_GATE` × 3 (clean create / out-of-plan low / in-plan below 0.75) | 8 | one per rule outcome, `label_source: "rule"`, label = `rule_choice` when the rule set one, else the sole legal candidate |

Total: 80 scored `SELECT_PLAN` (56 original + 24 generated) + 8 duplicate + 6 near-tie + 8 rule
= **102**. There are **no `REPLAN` turns** — see below.

## Why the counts are what they are, not rev 1's 80/80/60/60 (blocker A12)

`DecisionState.to_packet_v2()` carries **bands and booleans**, not raw values
(`score_band`, `inside_plan`, `rank_gap_band`, …), so the number of genuinely
distinct reachable packets is small regardless of how many raw scenarios are
driven through the gateway. `SELECT_PLAN` is the exception: `packet.operator_request`
is the raw request text itself, so every distinct phrasing is a distinct
packet by construction — that is why 80 scored requests are all kept, but
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

## The 24 generated requests in the former pending slots

On 2026-09-21 the user authorized generated request texts and expected labels
for all 24 slots. Their historical IDs (`sp2-pending-01`..`sp2-pending-24`)
remain stable, but none is pending or empty. The canonical input is
[`generated-requests.v1.json`](generated-requests.v1.json). The generator submits
each exact request through `RUN_CREATE`, then captures the real packet and legal
candidates. It no longer blanks or rewrites the captured request.

The 16 supported examples cover all 14 draft plans, including single stations,
alley routes, paired stations and verified inspections. Two use explicit
conversational corrections. The other 8 require escalation: 2 ambiguous,
2 contradictory, 2 outside the inspection catalogue, and 2 unsupported
combinations of scope and verification mode. All 24 participate in schema,
candidate membership, uniqueness and gateway regeneration checks.

Their provenance is `label_source: "generated"`. These are synthetic expected
labels, not operator-authored requests or independent human labels. The other
78 records and their existing labels remain unchanged. Neither filling the
slots nor passing these tests approves the draft catalogue for production or
establishes fresh model quality. The current inspection evaluator continues to
exclude this incompatible draft catalogue.

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
