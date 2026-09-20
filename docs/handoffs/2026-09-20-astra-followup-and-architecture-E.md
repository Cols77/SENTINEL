# Session Handoff — 2026-09-20 — Astra follow-up, Jev diagnosis, architecture E

**From:** Claude session `sentinel-b0`, which planned and executed P3a, merged P1–P3, and handled an external (Astra) review.
**Do not overwrite the root `HANDOFF.md`** — it belongs to the coherence/spec work. This file lives in `docs/handoffs/`.

## Project

SENTINEL flies a Tello drone around a six-zone indoor alley, detects rocks, and raises patrol tasks a human operator approves. A coordinator agent loop enumerates legal actions and submits every mutation through one `CommandGateway`. Architecture **F** (rules execute, operator decides, models shadow) is shipped and merged. Architecture **E** (LLM proposes/investigates, Jev gates, code executes) is the goal, and the operator has asked for it **sooner**.

## Task in Progress

Closing out an external review's two High findings, and answering whether architecture E's acting half is viable. Both are essentially done; one optional refinement is uncommitted.

## Current Status

- **Completed**
  - **P1, P2, P3 merged to local `master`** — the session's original ask. Not pushed.
  - **Astra High #1 (advisor latency) — fixed.** `ShadowLadder` awaited each advisor *before* calling the executor, so N advisors delayed execution by up to N × `advisor_timeout_s`. Measured against pre-fix code: 0.974 s with 3 advisors at a 0.3 s timeout (~10 s at production defaults). Advisors are now submitted without awaiting, the executor runs immediately, and advisors are collected against one shared deadline. Independently reviewed; the regression test was verified to **fail** against the old code.
  - **Astra High #2 — verified, partly stale.** The missing `artifacts/wp1/live-gate-real-loop.md` and the absent raw sweep rows were real, and the P1/P2 session (`sentinel-77`) fixed both. Two of its claims were wrong: the reports *are* tracked, and the `error_rate: 1.0` LLM claim describes a defect already fixed in `376ec2f`.
  - **Jev coverage diagnosis** (`artifacts/decisions/jev-coverage-diagnosis.md`) — verdict **(C) threshold / task-shape, no code defect**, adversarially reviewed and upheld, with three caveats (G1–G3) recorded in the document.
  - **`judge_label`** — `Decision.judge_label` carries the judge's own pick; `shadow_eval` reports `judge_agreement`, `judge_escalation_rate`, `judge_labels_recorded` **beside** the existing metrics, returning `None` (not `0.0`) when unrecorded.
  - **`EVIDENCE_GATE` false-premise bug — fixed and committed** (`4a26dd0`).
  - **Codex brief** — `docs/superpowers/plans/2026-09-20-CODEX-BRIEF-architecture-E.md`, on master at `8ac1f89`, updated for the WP0 grant and the diagnosis.
- **In progress (uncommitted, optional)** — see Critical Context.
- **Blocked on the operator**
  1. Batch id for `SENTINEL_SHADOW_SWEEP_CONSENT`, and confirmation the sweep reads `OPENROUTER_API_KEY` from `.env`.
  2. Fixture labels, if the 75 %-escalate rebalance should precede the paid batch (G-labels).
  3. Handing the brief to Codex.

## Key Decisions Made This Session

| Decision | Choice | Rationale |
|---|---|---|
| E's viability | **Split E in two; only the acting half needs promotion evidence** | The criteria bound the cost of a *wrong autonomous action*. An approval-gated proposer and an investigator ending in one `operator.ask` never act, so the operator gate is the safety property. Endorsed by the session that owns the criteria. |
| `ADVISORY_POINTS` | **P3 does not extend it**; shadowing routes on `decision_point in ADVISORY_POINTS or needs_judgment(legal)` | Extending it broke a P2-owned exact-equality test. The dynamic rule is also better: `ASSIGN` is only *sometimes* a judgment point and a static set cannot say "sometimes". The peer later relaxed the assertion anyway and agreed the dynamic rule should stay. |
| P3a Task 9 (REPLAN) | **Implemented, then dropped** | The covering-plan trigger degenerates (`PLAN_MARKER_VERIFIED_6` is a superset of every station set), contradicts an operator-approved label, and is wrong on the merits (dispatch is plan-gated, so an out-of-plan detection is an anomaly). Replanning now depends entirely on proposal item **P-5**. |
| New judge metrics | **Added beside the old ones, never redefined** | `escalation_rate` and `agreement` measure operator load, which is real. Silently changing their values under names committed reports already cite would make those reports quietly wrong rather than visibly superseded. |
| Re-sweep sequencing | **Distribution probe first, on the existing approved fixture** | Fixture balance affects *scoring*, not the confidence *distribution*. The cheap probe decides whether the expensive balanced run is worth funding. Rebalancing `SELECT_PLAN` needs operator-written texts anyway. |

## Files Changed

On branch `fix/astra-evidence-and-coverage` (4 commits ahead of `master` at `258b792`):

| File | Change |
|---|---|
| `artifacts/decisions/jev-coverage-diagnosis.md` | The diagnosis, verdict (C), plus caveats G1 (baseline model identity unverified), G2 (instruction contradiction), G3 (the 6 easy turns evidence confidence only, never correctness) |
| `tests/test_jev_coverage_diagnosis.py` | 5 tests pinning the confidence distribution and the sweep's own numbers |
| `src/sentinel/coordinator/policies.py` | `Decision.judge_label`; `JevPolicy` populates it; `EVIDENCE_GATE` instruction fixed **(further change uncommitted)** |
| `src/sentinel/coordinator/shadow.py` | `judge_label` surfaced in the advisor record and `_EMPTY_ADVISOR_ENTRY` |
| `src/sentinel/shadow_eval.py` | `judge_agreement`, `judge_escalation_rate`, `judge_labels_recorded` |
| `tests/test_judge_label_scoring.py` | 7 tests; the load-bearing one is "correct every turn, confident on none" |
| `tests/coordinator/test_instruction_premises.py` | The premise invariant **(extended, uncommitted)** |
| `tests/coordinator/test_shadow.py` | +59 lines, additive only: duplicate-case non-interference |

Already on `master` from this session: the P3 merge, the A9 characterization-test flip, the latency fix, and the Codex brief.

## Critical Context

**The uncommitted change, and what it needs.** `src/sentinel/coordinator/policies.py` and `tests/coordinator/test_instruction_premises.py` are modified and **not committed**. The change makes `INSTRUCTIONS["EVIDENCE_GATE"]` a *callable* taking the packet's facts, so a duplicate turn and a plain turn each get accurate, useful wording; `JevPolicy.decide` resolves `callable(instruction)`. Its targeted tests pass (7/7) and both renderings were inspected by hand. **The full suite was NOT run on it — the operator interrupted that run.** So: either run the full suite and commit, or `git checkout -- src/sentinel/coordinator/policies.py tests/coordinator/test_instruction_premises.py` and lose nothing important. **The bug itself is already fixed and committed in `4a26dd0`;** this is a refinement on top.

**Three confounds were removed from the paid batch before it was spent.** Any re-sweep run before these would have been wasted: (1) the `EVIDENCE_GATE` instruction asserted three premises the packet contradicted — false about duplicates on 24/24 turns and plan membership on 12/24; (2) raw per-call rows were never captured; (3) judge-level metrics did not exist. All three are now fixed.

**The headline correction about Jev.** `escalation_rate = 1.0` and `coverage_high = 0.0` are **one measurement reported twice**, not two independent signals. `JevPolicy` recorded its own post-banding fallback, so a MID-confidence *correct* pick was logged as an escalation. `agreement` had the same flaw. **Jev's raw picks on those 144 calls were never recorded, so its actual behaviour is unknown** — the acting half of E is *unmeasured*, not refuted. The P1/P2 session corrected its own artifact that had claimed "Jev abstains".

**Cross-session etiquette that worked.** `sentinel-77` owns P1/P2; this session owns the coordinator modules since P2b. Three collisions were avoided by messaging before acting, and one (sweep rows, P1 artifact) was caught only because they messaged first — that work was already merged when this session's workflow was about to duplicate it. Verify a peer's claims against the repo before acting; two of Astra's and none of theirs proved wrong. **Never treat a relayed grant as operator consent**; confirm gated actions with the operator directly.

**Environment.** Suite: `rtk proxy uv run python -m pytest tests/ -q -o addopts='' -p no:cacheprovider`, ~5–7 minutes; it exceeds a 2-minute default tool timeout, so background it rather than assuming it hung. Without the `rtk proxy` prefix a hook can swallow the run and print "No tests collected". A fresh worktree needs `uv sync --extra dev`. `git worktree add -b <new> <branch>` cuts from wherever that branch is *now* — this session's branch point was `9f657ac`, not the `620e8ac` assumed, which briefly made a correct baseline look stale.

**Gates never to self-grant:** any paid model call, any schema or contract-fixture change, any new hand-labelled fixture, any merge or push, any promotion of a policy to act. `CommandGateway` is untouchable — one narrow exception was granted to the other session for one defect and does not generalise.

## Next Steps (in order)

1. **Decide the uncommitted change**: run the full suite and commit it, or revert the two files. Nothing else depends on it.
2. **Merge `fix/astra-evidence-and-coverage` into `master`** once the suite is green — 4 commits, operator gate G-merge.
3. **Ask the operator for the sweep batch id** and confirm `OPENROUTER_API_KEY` resolution from `.env`. Then run `scripts/shadow_sweep.py --advisor jev` with raw-row logging: 144 calls, `typesafe/jev-1.13-20260917`, est. $0.20–0.40, prior estimate ran 4× over.
4. **Re-band the resulting rows offline** across a range of `act_at` values against `false_act == 0`. Treat 0.80 as an unvalidated default for this task shape.
5. **Rebalance the fixture** only if step 4 shows headroom. `EVIDENCE_GATE` labels are computable by the operator-approved 0.75 rule (verified: it reproduces 24/24 hand labels). `SELECT_PLAN` needs the operator's 24 held-out request texts — the empty slots already exist in `fixtures/decisions_v2/turns.v2.jsonl`.
6. **Hand the Codex brief over** and have Codex plan groups A and B as immediately executable, D gated.

## Resume Command

```bash
cd C:/coding/sentinel/.worktrees/astra-followup
git status --porcelain            # expect the two modified files from step 1
rtk proxy uv run python -m pytest tests/ -q -o addopts='' -p no:cacheprovider
```
