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

## Delivered implementation and evidence

Completed proposer compatibility and attempt auditing, shared consent-aware
construction, outcome inspection cases/scorer, manifest-bound sweep CLI,
strict recorded-response replay, and independent survey scene/scoring assets.
The survey loop subsequently landed and the external-worktree bridge exercised
F/E/J with synthetic observations and explicit simulated approvals. Production
survey files remain owned by Claude and were not modified by this evaluation.

The [inspection baseline](../../reports/inspection-capability-baseline.md) retains
45 scripted executions and a separate final-scorer rescore. The [survey baseline](../../reports/survey-readiness-evaluation-baseline.md)
retains three synthetic production-loop episodes, databases, decision packets
and an exact hash-verified archive of the dirty loop used by the run. Reports
distinguish successful workflow recovery, accepted proposal quality, report
consistency, truth accuracy and missing production capabilities.

Independent specification and quality reviews covered proposer boundaries,
shared construction/consent, inspection scoring, recording/orchestration and
survey evaluation. Final cross-reviews checked each published report against
its retained evidence. Review findings corrected permanent audit-failure
handling, draft escalation label import, rejected-command recovery scoring,
survey database retention and read-dispatch claims.

No live provider batch or hardware flight was run. Real-scene labels, live
quality/repeat measurements, production-generated survey options, direct survey
read-tool dispatch and the source-reproduced survey defects remain explicit
coverage limits. No numerical release gates, default model changes, merge or
push were introduced.

Final serial integration verification on 2026-09-21:
`rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short`
completed with **857 passed, 3 skipped, 3 warnings in 500.27 seconds** (exit 0).
The warnings concern the existing jsonschema RefResolver deprecation and an
unregistered unit marker. The retained log is
`artifacts/proposal-eval/full-pytest.log`. The report-only rescorer was executed
against all 45 saved outcomes and independently checked against its input and
script hashes; it made no policy or provider calls.
