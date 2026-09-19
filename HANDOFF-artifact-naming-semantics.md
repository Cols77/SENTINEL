# HANDOFF — Artifact naming & semantics review (A02 follow-up)

> Created 2026-09-17 by the SENTINEL native-coherence-bootstrap parent session.
> Audience: a separate session doing semantics/naming work on SENTINEL artifacts.
> Scope boundary: THIS handoff is a discussion brief only. It is not a requirement,
> not a plan, and it does not authorize any code, schema, or artifact changes.

## Why this exists

During the atomic SR consent round, the user deferred **A02** ("Catalogue-bound
proposals") and flagged a systemic issue rather than a defect in one SR:

> "INTENT could represent something else than a plan ID. Unless I don't understand
> what is a plan. I feel like the concepts behind the naming are a bit blurry.
> I'd rather have composed names to be sure to understand what is being referred
> to than approximate names like this."

All other ledger candidates (A01, C01–C05, W04–W06) were **accepted as-is**;
only A02 is held hostage to this naming question.

## The naming concepts under review

All names below come verbatim from the canonical specs
(`docs/superpowers/specs/2026-09-16-*`, contract revision `sentinel.v0`):

| Name | Defined in | Current meaning | Blurriness |
|---|---|---|---|
| `INTENT` | WP1 §3.1, §4 | The agent's structured outcome status in `AgentModelResult`: a successful `INTENT` carries exactly one `catalogue_plan_id` | "INTENT" does not say intent *about what*. Other outcomes: `NO_ACTION`, `MALFORMED`, `UNAVAILABLE` |
| `catalogue_plan_id` | WP1 §3.1; `survey-catalogue-v1.schema.json` | ID of a plan template in the fixed six-zone survey catalogue | "catalogue plan" vs "plan" vs "SurveyPlanReference" is a three-layer distinction carried by naming alone |
| `SurveyPlanReference` | WP0 §3.2 (`sentinel-v0-wire.schema.json`) | Immutable resolved plan: `plan_id`, `catalogue_id`, `catalogue_revision`, ordered `station_ids`; hashed into `plan_sha256` | The word "reference" hides that this is the *immutable concrete plan*, not a pointer into the catalogue |
| "plan" (bare) | WP1 §4.3, C01 | The immutable persisted `PLAN_CREATED` record | Overloaded: catalogue template, proposal, and persisted plan are all "plans" in prose |
| `RUN_CREATE` vs `FORCE_RUN_CREATE` | WP0; WP1 §4.2 | Normal run creation vs operator-only override of a failed preflight | Force distinction is clear, but "run" vs "mission" (`MISSION_CREATED`) still coexists |
| `OPERATOR_DECIDE` (target PLAN/TASK) | WP1 §4 | One command, two typed targets: plan approval vs task/assignment confirmation | Bare verb hides that it covers two distinct human decisions (plan approval, patroller assignment) |
| `NEEDS_REVIEW` | WP1 §4.1 | Review-hold run state (not terminal) | Sounds terminal; only the spec text disambiguates |
| `WAITING_FOR_APPROVAL` | WP1 §4 | One wire enum value; plan-vs-task disambiguation rides in `legal_operator_actions` | Name says "waiting"; the discriminating information is outside the name |
| `catalogue_revision` | WP0; C01 | Version of the survey catalogue a plan was resolved against | "revision" (catalogue) vs "version" (plan hash, run/state version) are used interchangeably in prose |

## Questions for the semantics session

1. Should `INTENT` be renamed to something composed, e.g. `PROPOSAL_READY` /
   `CATALOGUE_PLAN_PROPOSED` — and should the outcome family
   (`INTENT`/`NO_ACTION`/`MALFORMED`/`UNAVAILABLE`) get a shared prefix?
2. Should the three-layer plan identity be renamed as a set, e.g.
   `survey_plan_template` (catalogue entry) → `survey_plan_proposal` (agent
   output) → `SurveyPlanReference` (immutable persisted plan)? Or kept and
   clarified with a glossary?
3. Should `OPERATOR_DECIDE` split into `PLAN_APPROVE`/`PLAN_REJECT`/
   `ASSIGNMENT_CONFIRM`/`ASSIGNMENT_REJECT`, or keep one command with the typed
   target?
4. Should `NEEDS_REVIEW` become `HELD_FOR_REVIEW` or similar to kill the
   "terminal-sounding" reading?
5. Do `revision`/`version` need distinct composed names
   (`catalogue_revision` vs `plan_sha256` already differ; run/state version does not)?
6. Is a glossary artifact (WP0-level) the right vehicle, or should renames land
   in `sentinel-v0-wire.schema.json` before any coordinator code exists?

## Constraints on the outcome

- The wire schema (`sentinel-v0-wire.schema.json`, 92.9K) and the parent
  compatibility mapping table (coherence spec §3) are the authority; any rename
  is a WP0 compatibility amendment, per "No component may use a parent value
  outside this table without a WP0 compatibility amendment."
- SR C01/C02/C03 (accepted) anchor on these names; a rename triggers a
  re-anchor review of the accepted SRs, not a re-consent of their semantics.
- A02 stays deferred until this thread resolves question 1 at minimum.
- Nothing in the current bootstrap dispatches on these names; the only
  implementation-anchored artifact is
  `tests/test_indoor_alley_contract_probe.py` (A02's probe).
