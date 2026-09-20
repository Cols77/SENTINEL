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

**Gate `G-labels`: APPROVED, with relabelling — recorded 2026-09-20.** The
operator reviewed the table of `turn_id | request or variant | draft label |
rationale` for all 48 `SELECT_PLAN`/`EVIDENCE_GATE` judgment turns (Task 1,
Step 6) and recorded these decisions, quoted verbatim:

1. EVIDENCE_GATE: "Make it a rule, keep shadow" — the create_task/escalate
   policy is computable, so a deterministic rule decides it; advisors are
   still consulted in shadow at that point and recorded. (This is
   `legal_tools.legal_set()`'s deterministic `rule_choice`, Task A.)
2. Score cut: "Escalate below 0.75" — a detection becomes a task only when
   it is inside the approved plan AND its score is >= 0.75; otherwise
   escalate.
3. Single-station requests: the operator observed "Seems like
   PLAN_ROUTE_ONLY_2_ZONE does not make sense if you're visiting one zone?"
   and chose "Escalate, and propose a catalogue entry" — `sp-001`..`sp-006`
   are labelled `escalate` because no catalogue plan covers exactly one
   station (`PLAN_ROUTE_ONLY_2_ZONE`, the narrowest catalogue plan, visits
   two); a WP0 proposal for a 1-zone catalogue entry is drafted separately
   (Task C).
4. Catalogue fixture: "Proposal only, you consent" —
   `docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json`
   was **not** edited by this relabelling (decisions 2 and 3 above needed no
   catalogue change; decision 3's 1-zone entry is Task C's proposal, not an
   edit here).
5. "All other draft labels in fixtures/decisions/turns.v1.jsonl are APPROVED
   as drafted (gate G-labels), subject to the relabelling in decisions 2 and
   3."

Applying decisions 2 and 3, `turn_generator.py` was updated and the fixture
regenerated (`SENTINEL_REGEN_TURNS=1`): `sp-001`..`sp-006` now carry
`escalate` (decision 3), and the 24 `EVIDENCE_GATE` turns now carry
`create_task` iff `detection_inside_plan` and `detection_score_band` is
`high`/`very_high` (raw_score >= 0.75), `escalate` otherwise (decision 2) —
which changed `eg-009`/`eg-010` (score `0.62`, inside the plan) from
`create_task` to `escalate` relative to the original draft. Every other
judgment turn keeps its originally drafted label, per decision 5.

Because decision 1 makes `EVIDENCE_GATE` a deterministic rule
(`legal_tools.legal_set()`'s `rule_choice`) and decision 2 gives that rule the
same 0.75 cut this fixture's `EVIDENCE_GATE` hand labels now use, the 24
`EVIDENCE_GATE` labels mirror the rule exactly. Scoring an advisor against
them (Task 5/6) is therefore a **rule-agreement check** — does the advisor
agree with what the deterministic rule would already have done — and not an
independent judgment benchmark; the `SELECT_PLAN` labels remain the
judgment benchmark, since `SELECT_PLAN` has no `rule_choice` and the operator
still decides it (P2 Task 3).

The 5 `label_source: "rule"` labels (the RULE points) are unaffected: they
are read directly off the single legal candidate at a decision point that
has no judgment to make, so there was nothing to approve or relabel.

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
