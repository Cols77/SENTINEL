# Jev/System One fixture + harness rebuild (kanban input)

**Date:** 2026-09-19 · **Board:** `jev-shadow` (NOT `sentinel-wp1-kanban`; separate worktree/branch `feat/jev-fixture-v2` off master `2193f59`)
**Origin:** architecture grill Q1-Q2. Demo freeze is 2026-09-25.

## Why (evidence, verified in code on master)

The recorded 21-case result (Jev 9 false-safe, LLM 6, static 0) cannot support any go/no-go:

1. Static baseline is `StaticJudge(hazard_relevant=(True, 0.9))`, a constant. 0 false-safe holds by construction; its 5 false-escalations are exactly the 5 negatives. It is not evidence static is "best".
2. Labels mix policy with ground truth. 12 of 16 positives (`amb-*`, `wf-*`) are `hazard_relevant=True` even where the question ("requires action") contradicts them (`wf-001` duplicate, `amb-003` conf 0.35 blob). Only 4 `pos-*` cases are clean positives.
3. Reference `priority` uses `none`/`critical`; the question options are `ignore/low/medium/high`. Priority is unscoreable.
4. `evaluation.compare_runs` compares whole answer dicts (floats, confidence, legend), so per-question agreement is ~0 by construction.
5. Replay sent prose observations and a criteria dict on `noul`; Jev's docs use structured state and criteria-less `Noul`. The versioned `state_snapshot v1` was not used. Jev's confidence bands were collapsed to a 0.5 threshold.
6. One run per case, ~4 clean positives: no noise estimate, 9 vs 6 is indistinguishable.

Original artifacts (`artifacts/jev/*.jsonl`) were never committed and are not on disk, so per-case answers are unrecoverable. Treat the old numbers as void, not as a baseline.

## Hard constraints

- **No live provider calls in cards 1-4.** Offline/hermetic only. Live calls need explicit operator consent per batch (brief's secrets rule). Card 5 is blocked until the operator says go.
- `.env` names only, never values.
- Jev stays `SHADOW_EVALUATION_ONLY`. Demo path is not changed by this work.
- Static judge remains the fail-closed veto on the demo path; the LLM coordinator loop (WP1) is the "in the loop" component. Not decided here beyond that.

## Cards (serial, one worktree; each: worker -> fresh reviewer -> parent verification)

### J1. Relabel fixture to `cases.v2.jsonl` (labels APPROVED by operator, 2026-09-19, as drafted below, including `pos-002` critical and the `wf-001`/`wf-002`/`wf-006` calls)
- Separate fields: `hazard` (true/false/null), `needs_review` (bool), `priority` (from a real option set: ignore/low/medium/high/critical, or null).
- Keep `cases.v1.jsonl` untouched. v2 carries a `rationale` per case.
- Labels (approved; J3 may consume them):
  - `neg-001..005`: hazard F, review F, priority ignore.
  - `pos-001`: T/F/high. `pos-002`: T/T/critical. `pos-003`: T/F/high. `pos-004`: T/T/high.
  - `amb-001..006`: hazard **null**, review **T**, priority null (unscored).
  - `wf-001` duplicate: F/F/ignore (no new action). `wf-002` designated detour: F/T/low.
  - `wf-003`: T/T/medium. `wf-004`: T/F/low. `wf-005`: T/T/high. `wf-006`: T/F/low.
- Acceptance: schema-validated fixture, no case with hazard true and an ignore priority, every label has a rationale, operator approval recorded in the card.

### J2. State shape: use `state_snapshot v1` / structured state
- Convert each case's prose to structured fields (`detection{type,confidence,size_m,trail_relation}`, `context{patroller_available,trail_status,duplicate_of_existing}`), passed through `state_snapshot`. Update `QUESTION_SET`: `noul` questions without criteria dicts (per Jev docs), priority options match J1.
- Acceptance: unit test that every replay payload validates against `judgment_context.v1`.

### J3. Fix the harness (`evaluation.py`)
- `compare_runs`: compare only the decision value per question (bool/choice), not full dicts; report probability deltas separately.
- Score `hazard` and `needs_review` separately; skip cases with null labels for that field.
- Add `--repeats N` (default 3) with per-case stability (answers flip across repeats).
- Preserve Jev confidence and record three-band routing alongside the plain threshold; report both.
- Trivial baselines built in: always-safe, always-escalate, static-rules. Any judge must beat always-escalate on false-escalation at equal false-safe.
- Acceptance: tests with hand-built logs showing agreement is nonzero when decisions match and differing floats; baselines reproduce known counts (always-escalate: 0 false-safe, N negatives false-escalation).

### J4. Offline dry run + report skeleton
- Run harness on the static and trivial baselines, and on a fake Jev transport. Confirm scoring and logs.
- Acceptance: full suite green; the pre-existing flaky `tests/test_env_file.py::test_parses_comments_and_quoted_values` still recorded as known (fixed in WP1 card `t_45e8629b`, do not touch here).

### J5. Live re-run (Jev + LLM baseline): CONSENTED 2026-09-19, gated on J1-J4
- Operator consent given for exactly this batch: 21 cases x 3 repeats x 2 arms = 126 calls, est. < ~$0.02, ~18 min wall time (Jev `typesafe/jev-1.13-20260917` and `z-ai/glm-5.3-flash`, both via OpenRouter, v2 fixture, structured state). Do not spawn until J1-J4 are reviewed and green. Any change in models, call count or cost > 2x estimate needs fresh consent. Consent does not cover other batches (e.g. F5).
- Acceptance: artifacts committed under `artifacts/jev/` (this time), summary with per-arm false-safe on clean positives, needs_review recall, stability, confidence-band routing.

## Decision rules for reading J5 (write these BEFORE running)

- A judge is only "worse" if it loses to always-escalate on false-safe on clean positives and needs_review recall; otherwise the fixture cannot rank it.
- False-safe on `pos-*` is a blocker for any authority. Misses on `amb-*` count against `needs_review` recall, not hazard.
- If Jev's low-confidence outputs concentrate its errors, route by confidence band and re-score rather than judging the 0.5 threshold.

## Out of scope

- Coordinator-facing tool choice via Jev (wrong tool for sequencing; WP1 loop covers it).
- Promoting either judge; env-overlay fix (WP1 card); WP1 live gate re-run.
