# Claude Code Work Queue

All Claude work uses separate worktrees and task-sized branches. Claude must
not edit `feat/jev-agentic-room-loop`, merge, rebase, push, run live providers,
or operate hardware.

## Ready now

1. **Plan 03 Task 1 — semantic room poses**
   - Handoff: `2026-09-23-claude-plan03-semantic.md`
   - Base: `391db567d28442cf07a0173d262bfb4e0ea481a8`
   - Branch: `claude/plan03-semantic-pose`
   - Review: independent specification review, then quality review.

2. **Plan 06 Task 1 — manifests, artifacts, and capability grants**
   - Handoff: `2026-09-23-claude-plan06-artifacts.md`
   - Base: `391db567d28442cf07a0173d262bfb4e0ea481a8`
   - Branch: `claude/plan06-artifacts`
   - Review: Opus specification/security review plus repository quality review.
   - Integration: park the reviewed branch until the roadmap reaches Plan 06.

These two packets own disjoint source and test files and may be developed in
parallel.

## Gated next packets

Do not start these until the named prerequisite has been reviewed and merged.

1. **Plan 03 Task 2 — support-aware geometry, coverage, association, counts**
   - Prerequisite: reviewed Plan 03 Task 1 integration SHA.
   - Risk: modifies the semantic site schema and fixture established by Task 1.
   - Exclude: controller, supervisor, and executor.

2. **Plan 03 Task 3 — safe closer-viewpoint generation**
   - Prerequisite: reviewed Plan 03 Task 2 integration SHA.
   - Risk: consumes checkpoint, localization, recovery, and energy APIs.
   - Rule: report a missing safety API; do not patch safety modules on this
     branch.

3. **Plan 03 Task 5 — offline replay integrity and readiness report**
   - Prerequisite: reviewed Plan 03 Task 4 integration SHA and frozen event
     projection.
   - Risk: replay records must match the final event schema exactly.
   - Exclude: invented real labels, fresh provider claims, and hardware claims.

## Retained locally

Keep integration-heavy tasks with the primary reviewed workflow for now:

- Plan 03 Task 4;
- Plan 04 Task 3;
- Plan 05 Tasks 2 through 4;
- Plan 06 live CLI, promotion, and hardware acceptance tasks.

These tasks touch the controller, supervisor, executor, or live authorization
boundary and should not be developed concurrently against an unfrozen base.

