# Reliable evaluation implementation ledger

User-approved scope: inspection evaluation through real policies, AgentLoop and
gateway; separate survey readiness evaluation, with production survey work owned
by Claude. Worktree: `p3b1-approval-gated-e`. No live batch, hardware trial,
model-default change, merge or push is part of this execution.

## Tasks and acceptance

1. Repair optional Jev confidence; audit every Jev and LLM attempt separately
   from accepted recommendations. Preserve legal-label, probability and margin gates.
2. Share scripted/LLM/Jev construction with rehearsal. Require consent before
   provider initialization and filesystem artifacts; evaluation consent binds to
   an immutable manifest and budget.
3. Add outcome cases and scorer using approved catalogue and fresh databases,
   real architecture F/E policies, AgentLoop and gateway. Validate persisted
   plans, tasks, assignments, accepted events, targets and approval ordering.
   Exercise escalation, pending approval, rejection/reproposal, qualifying and
   missing evidence, malformed/provider failures and audit recovery.
4. Add scripted, recorded and explicitly consented live modes; append-only raw
   records, revision/hash/config/budget manifests, per-case snapshots and reports.
   Dry run reports maximum calls and cost assumptions. Synthetic cases prove
   harness behavior only; independent labels are required for model-quality claims.
   Preserve historical reports and label the direct-adapter suite legacy.
5. Prepare versioned independent survey scenes, projection reference, one-to-one
   scoring, option-specific replay validation and source-rechecked Claude handoff.
6. Connect actual survey loop only once dependencies land. Until then report
   missing production components and measured scenes as blocked coverage.

## Verification and reviews

Tests are serial, using `rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest`.
The global Python cannot collect video tests because `av` is absent; use the
existing repository environment. Focused regressions follow each change, with
full-suite integration checks. Independent specification review precedes quality
review. Task-sized commits retain the P3b1 branch for review.

Initial revisions: P3b1 `f13568b`; survey `73846e4`. Recheck survey before handoff.
Initial survey dependencies absent: loop, report, eval and consent bridge.
