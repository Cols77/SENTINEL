---
id: SR-SENTINEL-V0-A02
title: Catalogue-bound proposals, never dispatch
statement: "a successful `INTENT` proposes exactly one `catalogue_plan_id`; the coordinator resolves its catalogue revision/hash before persisting the immutable plan; `NO_ACTION`, `MALFORMED` and `UNAVAILABLE` carry no proposal; no raw SDK command, coordinate, height or unbounded capture count is ever emitted; the agent never dispatches a plan."
domain: behavioral
status: accepted-pending-registration
feature: FEAT-SENTINEL-V0-BOOTSTRAP
consent:
  decided_by: Colin Aubé
  decided_at: 2026-09-17
  decision: "accepted (user correction: approved, not deferred)"
  session: native-coherence-bootstrap parent chat
---

# SR-SENTINEL-V0-A02 — Catalogue-bound proposals, never dispatch

**Normative statement:** a successful `INTENT` proposes exactly one
`catalogue_plan_id`; the coordinator resolves its catalogue revision/hash before
persisting the immutable plan; `NO_ACTION`, `MALFORMED` and `UNAVAILABLE` carry no
proposal; no raw SDK command, coordinate, height or unbounded capture count is
ever emitted; the agent never dispatches a plan.

**Anchors:** WP1 §1, §3.1, §4 row 2; indoor-alley design §2;
`docs/superpowers/specs/contracts/survey-catalogue-v1.schema.json`;
`tests/test_indoor_alley_contract_probe.py::test_agent_proposal_is_catalogue_bound_and_force_run_is_typed`.

**Acceptance:** proposal → immutable plan only; no dispatch path from the model
result. **Proof shape:** existing probe plus WP1 §7 simulated-flow tests.
**Evidence class:** contract/bootstrap-verifiable (probe) + implementation-pending
(flow). **Uncovered/deferred:** revisit policy beyond "new bounded revisit
requires fresh approval".

**Naming note:** the `INTENT` / `catalogue_plan_id` / `SurveyPlanReference`
naming concerns raised in consent remain tracked in
`HANDOFF-artifact-naming-semantics.md`; this approval accepts the *semantics* as
specified. Renames, if any, follow the WP0 compatibility-amendment flow and
re-anchor this SR without re-consenting its semantics.
