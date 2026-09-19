# Decision-turn fixture (`turns.v1`)

`turns.v1.jsonl` is 53 decision-point turns produced by driving the real
coordinator gateway (`CommandGateway`, via `tests/coordinator/turn_generator.py`),
never hand-written state:

- 24 `SELECT_PLAN` turns — request-text variants (single-station, full/marker-verified
  sweep, ambiguous, out of catalogue, contradictory).
- 24 `EVIDENCE_GATE` turns — perception variants across the six score bands
  (`0.51, 0.55, 0.62, 0.78, 0.9, 0.99`) × inside/outside the approved plan.
- 5 RULE points (`WAIT_APPROVAL`, `WAIT_OBSERVATION`, `EVIDENCE_VETO`, `RANK`,
  `ASSIGN`), one turn each, labelled automatically from their single legal candidate.

Regenerate with `SENTINEL_REGEN_TURNS=1 uv run python -m pytest
tests/coordinator/test_turn_fixture.py -q -o addopts=''`. Regeneration keeps
whatever `label`/`label_source`/`rationale` is already committed for a given
`turn_id` (matched by id, not position), so an operator's approved relabelling
survives a regeneration that only refreshes `packet`/`candidates`.

## Label status

**DRAFT — pending operator approval.** Every `label_source: "hand"` label in
this fixture (the 48 `SELECT_PLAN`/`EVIDENCE_GATE` judgment turns) is a draft
written by the generator's author, not yet reviewed by an operator. Gate
`G-labels` (Task 1, Step 6) is the point where an operator reviews the table of
`turn_id | request or variant | draft label | rationale` for all 48 judgment
turns and records approval or edits here, verbatim. **That gate has not run.**
Per the plan's Global Constraints, Task 5 (shadow sweep scoring) and Task 6
(rehearsal scoring) must not score against these unapproved labels until
`G-labels` has recorded approval.

The 5 `label_source: "rule"` labels (the RULE points) are not drafts: they are
read directly off the single legal candidate at a decision point that has no
judgment to make, so there is nothing for an operator to approve.

## Deviation from the plan: the "duplicate open task" EVIDENCE_GATE dimension

The plan's Task 1 spec asked for `EVIDENCE_GATE` perception variants along
three axes: score × {inside the plan, outside it} × {no open task, duplicate
of an open task at the same station}. The third axis is not reachable through
the real gateway under P1's current `DecisionState`/`legal_tools` model:

- `legal_tools.legal_set()` only reaches `EVIDENCE_GATE` when
  `DecisionState.task_id is None`.
- `decision_state.read_decision_state()` derives both `task_id` (the run's
  single "latest task" pointer) and `open_task_station_ids` (the fact behind
  "duplicates an open task") from the *same* per-run `tasks` table. `task_id`
  is `None` if and only if that table is empty for the run, which is exactly
  when `open_task_station_ids` is also empty.

So a run cannot simultaneously be at `EVIDENCE_GATE` (which requires no task
yet exists) and have an open task at the detected station (which requires one
does). P1 models one task per run, not one task per station, so "duplicate"
detections are a concept the current `DecisionState` shape cannot represent
while still exposing `EVIDENCE_GATE`'s two candidates.

In place of that unreachable axis, the fixture instead varies which of the two
real catalogue stations is hit within each of "inside the approved plan"
(`LEFT_ALLEY_MIDDLE`, `RIGHT_ALLEY_MIDDLE`, the approved
`PLAN_ROUTE_ONLY_2_ZONE` stations) and "outside it" (`LEFT_ALLEY_FRONT`,
`RIGHT_ALLEY_BACK`, real `PLAN_MARKER_VERIFIED_6` stations that are not part of
the approved plan). This keeps the variant count at 24 and preserves the label
boundaries the plan cares about (the inside/outside escalate boundary and the
marginal-score-band escalate boundary at 0.51/0.55), while every state is
genuinely reachable by driving the gateway.

`detection_duplicates_open_task` remains a real field in every turn's
`packet.facts` (it is always `false` in this fixture, since no `EVIDENCE_GATE`
turn has an existing task) — should P1's `DecisionState` later move to a
per-station or per-detection task model, this dimension can be reinstated
without changing the fixture schema.

## Known gaps

- **No pose or staleness facts.** `DecisionState`/`to_packet()` carries no
  drone pose, no station-observation staleness, and no "last seen" timestamp
  for any fact. WP5 (drone pose / AprilTag) is expected to add these; until it
  lands, no turn in this fixture — and no advisor reasoning over it — can
  condition on pose or staleness.
- **No weather source.** There is no weather fact anywhere in `DecisionState`
  or the packet. Advisors and rules alike are blind to weather; nothing in
  this fixture exercises weather-conditioned decisions.
