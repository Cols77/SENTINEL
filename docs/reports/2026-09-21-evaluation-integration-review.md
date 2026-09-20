# Evaluation integration review — 2026-09-21

The user authorized independent subagent review, fixes and local integration of
`feat/p3b1-approval-gated-e`. The starting evaluation revision was `5798a62`;
local `master` was `52ae6eb`, already an ancestor of the evaluation branch.
No remote push, provider call or hardware action is part of this integration.

Two independent reviewers examined inspection scoring/replay and the production
proposer/approval boundaries. The integrating agent reviewed survey replay and
artifact retention. Findings are verified with focused regression tests before
integration; historical capability reports are not recomputed by these fixes.

## Findings and corrections

- The survey bridge could overwrite an existing output report. It now refuses
  that path before creating artifacts and writes new reports exclusively.
- Unexpected survey-loop exceptions could abort the remaining arms and omit the
  final report. Each failed arm now retains a sanitized error category, decisions,
  routes and database; remaining arms execute, and the aggregate status is ERROR.
  Instrumented read functions are restored even after a failed arm.
- Survey bridge tests assumed a sibling worktree. Discovery now also supports
  the main checkout's `.worktrees/survey-counting` location, preserving coverage
  after local integration.
- The inspection scorer did not check the persisted execution mode against its
  catalogue entry. A mode mismatch now fails the outcome.
- Strict replay compared plan hashes that include generated plan UUIDs verbatim.
  Complete, internally valid plan references now normalize both the UUID and its
  dependent hash. Changed stations, catalogue fields or invalid hashes stay
  detectable. Rejection followed by a different proposal has a replay regression.
- Capturing typed model results as generic dataclasses discarded wire metadata,
  so replay could fall back to the operator while appearing to complete the task.
  Capture now preserves the wire representation, and replay tests require the
  original accepted-proposal counts as well as terminal completion. Older captures
  remain historical; this fix does not reconstruct their missing metadata.
- The bound architecture E policy did not forward the model's conversation
  cleanup hook at loop return boundaries. Lifecycle forwarding is covered through
  the real router, adapter and loop.
- The real Jev transport converted boolean and string probabilities to floats
  before the proposer's validation. Choice probabilities must now be JSON numbers
  before conversion; invalid shapes escalate and retain an attempt record.
- Live dry runs displayed the uncapped scenario maximum as the fresh-call maximum.
  The latter now respects the chosen call budget; the uncapped scenario maximum
  remains separately visible.

## Generated request cases

During review the user explicitly authorized the agent to fill the 24 previously
operator-pending v2 request slots. These become generated synthetic cases, not
human-authored held-out observations. They retain the draft catalogue dependency;
this instruction does not approve that catalogue for production execution.
A separate 12-case supplement uses the currently approved two-plan catalogue.
Neither set changes historical baseline results or authorizes provider calls.

## Evidence locations

The inspection and survey baseline artifacts remain in the preserved evaluation
worktree at `C:/coding/sentinel/.worktrees/p3b1-approval-gated-e/artifacts/proposal-eval`.
They are ignored run outputs, not versioned source files. Relative artifact links
in the historical reports resolve when those reports are viewed in that worktree.
The survey baseline records its original source revision; the newer external
survey checkout is a separate dependency and is not merged by this integration.

## Verification

The preliminary serial full run was interrupted at 41% after review found issues
requiring fixes; it is not counted as a completed verification run. Focused tests
first reproduced both survey failures. Boundary regressions also reproduced the
four malformed-probability and missing-cleanup failures before fixes; the focused
boundary suite then passed 60 tests. Survey retention plus live-budget reporting
passed 11 tests after their regressions failed as expected. Final integrated
verification is recorded below.

Inspection replay and recording checks passed 20 tests after preserving the wire
metadata; the earlier inspection suite passed its mode-scoring regression. Fixture
regeneration passed 16 tests. Only the 24 former pending JSONL lines changed; the
other 78 lines remained byte-for-byte identical. A second agent checked all 24
expected labels against the draft catalogue and found no remaining blocker.
