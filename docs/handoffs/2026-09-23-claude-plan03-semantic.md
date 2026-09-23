# Claude Code Handoff — Plan 03 Task 1

Work in a separate Git worktree and branch named
`claude/plan03-semantic-pose`, based exactly on
`391db567d28442cf07a0173d262bfb4e0ea481a8`. Do not edit the existing
worktree.

Implement only Plan 03 Task 1 from:

`docs/superpowers/plans/2026-09-22-jev-room-agent-03-count-closer-return.md`

## Goal

Add deterministic semantic room-pose resolution.

## Files

- Create `src/sentinel/room/semantic.py`.
- Modify `src/sentinel/room/site.py`.
- Modify `docs/superpowers/specs/contracts/room-site-base-v1.schema.json`.
- Create `tests/room/test_semantic.py`.
- Add `fixtures/room_agent/v1/semantic-site.json`.

## Development workflow

Use strict test-driven development:

1. Write the required failing tests and run them.
2. Implement the smallest compliant resolver.
3. Run:

   ```powershell
   rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_semantic.py tests/cube/test_coverage.py -q --tb=short -o addopts='' -p no:cacheprovider
   ```

4. Run affected room-site and store tests serially.
5. Self-review the implementation against the approved design and the requirements below.
6. Commit the finished task as:

   ```text
   feat(room): resolve semantic room poses
   ```

## Requirements

- Resolve all 27 combinations of:
  - left / center / right
  - rear / middle / front
  - bottom / middle / top
- Treat `front` as the target/table side, currently maximum Y.
- Preserve arbitrary user ordering, subsets, repetitions, and landmarks.
- Validate normalized orthogonal axes, unique landmark IDs, schema and hash,
  envelope containment, deterministic insets, and unique stable pose IDs.
- Expose semantic regions through model-facing APIs; do not expose raw
  coordinates to the model.
- Keep `docs/wp5-flight3-corners.json` historical and excluded.

## Scope limits

- Do not implement Plan 03 Task 2 or later tasks.
- Do not rebase, merge, or push.
- Do not run live model calls or hardware operations.

## Return evidence

Report:

- completion status;
- commit SHA;
- exact changed paths;
- failing-test and passing-test evidence;
- any self-review concerns.
