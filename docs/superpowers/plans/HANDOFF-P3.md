# P3 handoff — the decision surface and architecture E

**Written:** 2026-09-20 by the session that executed P1 and P2. **For:** a parallel Claude Code session that owns P3.
**Your worktree:** `C:/coding/sentinel/.worktrees/p3-decision-surface`, branch `feat/p3-decision-surface`, branched from the tip of `feat/p2-shadow-advisors`.
**Demo freeze:** 2026-09-25. **P3 is post-freeze work.** Nothing you do may destabilise the demo path before then.

## 0. Corrections from the planning session (2026-09-20) — read before the rest

Verified on `feat/p2-shadow-advisors` @ `620e8ac`. Where this section and a later section disagree, this section wins.

1. **The worktree and branch in the header do not exist yet.** P3a Task 0 creates them. The P3a plan and this handoff existed only in a temp scratchpad; they now live untracked in the main checkout under `docs/superpowers/plans/` until Task 0 commits them on the P3 branch.
2. **P2b has landed** (updated 2026-09-20, later the same day). The P2 session shipped it at `847d7af`, and this branch's merge-base turned out to be `9f657ac` — P2b's code commit — so the P2b code was present for all of Wave 1. `LegalSet.rule_choice`, `EVIDENCE_TASK_SCORE = 0.75`, `JUDGMENT_POINTS = {SELECT_PLAN}` and `ADVISORY_POINTS = {SELECT_PLAN, EVIDENCE_GATE}` are all live. The branch is rebased onto `847d7af` and the interface pin is filled: see `p3a-interface-pin.md`. **Wave 1 is complete; Task 6 is complete; Tasks 7–11 are ready.** P3 must still never implement `rule_choice` itself — the P2 session owns it.
3. **`docs/superpowers/specs/2026-09-20_wp0-proposal-single-zone-catalogue-entry.md` (section 1, item 5) does not exist.** It is superseded by `docs/superpowers/specs/2026-09-20_wp0-proposal-p3-decision-surface-amendments.md`.
4. **Section 5 understates the gaps.** The gateway itself blocks three of the four decision types end-to-end (run completes on the first confirmed assignment; rank-1-only proposals and no `scores` in `TASK_RANK`; the catalogue schema, wire schema, DB CHECK and a hardcoded path pin the two-plan catalogue). Full table A1–A13 in the consolidated proposal. **Operator decision 2026-09-20: `CommandGateway` stays untouchable; everything goes into that one proposal (gate G-amend); blocked slices become a short P3a-2 plan after consent.**
5. **A read-model bug was found (A13):** after a rejected mid-run plan proposal, `read_decision_state` reports the rejected plan as the approved one. P3a Task 3 fixes it in the read model.
6. A fresh worktree needs `uv sync --extra dev` before the test command in section 8 works.
7. Section 8, step 3 still holds (Workflow: dev → fresh review → fix → suite gate, on Sonnet, gates outside), against **rev 2** of the P3a plan. Wave 1's code was dry-run verbatim in a throwaway checkout: 30 new tests passed, full suite 0 failures.
8. **Section 4's "472 passed" is correct** for `620e8ac`. An earlier revision of this section called it stale; that was wrong, and the cause was assuming this worktree sat on `620e8ac` when its merge-base is `9f657ac`. See Baseline.
9. `scripts/shadow_sweep.py` keeps a local `JUDGMENT_POINTS = (SELECT_PLAN, EVIDENCE_GATE)` that does not know about P3's new decision points. It is P2-owned and on P3's Never-edit list, so P3 leaves it alone; the P2 session may want to update it before any sweep that should cover `ASSIGN`/`REPLAN`.

## Baseline

Measured in `C:/coding/sentinel/.worktrees/p3-decision-surface` (after `uv sync --extra dev`; use `rtk proxy uv run python -m pytest …` if the hook swallows the run):

| Commit | Collected | Passed | Skipped | When |
|---|---|---|---|---|
| `620e8ac` | 475 | 472 | 3 | P2 tip as the original handoff described it |
| `9f657ac` | 495 | 492 | 3 | **this branch's actual merge-base** — P2b code included |
| after Wave 1 | 525 | 522 | 3 | 30 new tests from Tasks 1–5 |
| after rebase onto `847d7af` | 527 | **524** | 3 | current; 0 failed |

The 3 skipped are the opt-in live LLM gate. Every task's suite gate is measured against the current row.

## Wave 1 result (2026-09-20)

5/5 tasks, each implemented then reviewed by a fresh agent that re-ran the suite itself; **zero fix rounds** — every task approved on first review. Commits after the rebase: `da5a828` (draft catalogue), `fa34518` (multi-detection state), `0890a08` (confirmed-plan fix, A13), `b2f03a7` (near-tie facts), `e6d557a` (operator-touch counting).

Note for future workflow runs: commit trailers on this branch are mixed (`Claude Opus 5` on most, `Claude Sonnet 5` on `0890a08`) because each session applies its own attribution rule. Harmless, but do not "fix" it by rewriting history.

## 1. Read these first, in order

1. `docs/superpowers/plans/2026-09-19-p1-agent-loop-foundations.md` — what the loop does now, and the defect table D1–D11 it fixed.
2. `docs/superpowers/plans/2026-09-19-p2-shadow-advisors-F.md` — the shipped demo architecture (F).
3. `docs/superpowers/plans/2026-09-20-p3a-decision-surface.md` — your first plan.
4. `docs/superpowers/plans/2026-09-19-p3-hybrid-E-followup.md` — the original E plan. **Its promotion criteria stay valid; its task list is superseded by P3a and P3b.**
5. `docs/superpowers/specs/2026-09-19_wp0-amendment-decision-context-and-reads.md` and `2026-09-20_wp0-proposal-single-zone-catalogue-entry.md` — proposals awaiting the operator's Coherence consent. **Never self-grant.**
6. `src/sentinel/coordinator/{decision_state,legal_tools,policy,policies,shadow,operator_choice}.py` — the P1/P2 code you build on.

## 2. Why P3 needs to change

By the end of P2, rules settle every decision except which plan to propose, and even that escalates to the operator whenever the request names one station, because the catalogue can't express a single-station survey. The evidence gate became a deterministic rule on 2026-09-20 (inside the approved plan and score ≥ 0.75).

So **architecture E has almost nothing left to decide**. The fix is not to give a model authority over rules; it is to build the parts of the workflow where judgment genuinely lives. That is P3a. Only then does P3b (the E policies) mean anything.

## 3. Operator decisions (2026-09-20, verbatim where quoted)

- E should own: **multi-detection runs, patroller choice, replan mid-run, and closer look / revisit** (all four selected).
- Catalogue: **"Rich set with subsets"**, roughly 12–15 plans (per-zone, per-side, verified variants).
- Operator load target: **"Approvals only"** — the operator approves the plan and the assignment; everything else runs unattended unless something is genuinely unclear. Escalations are the exception, not the rhythm.
- Earlier, still binding: the LLM never acts directly and may only propose (proposals are approval-gated); Jev's authority comes only from the promotion criteria; the static veto and `CommandGateway` are untouchable; paid calls and schema changes are operator gates.

## 4. What exists, and what is true about it

- Branches: `feat/wp1-gate-fixes` (P1) → `feat/p2-shadow-advisors` (P2, branched from P1). **Neither is merged into `master` yet**; ask the operator before merging or pushing anything.
- Suite from the P2 worktree: 472 passed, 0 failed, 3 skipped before P2b; re-run it yourself and trust that number, not this document.
- The 3 skipped tests are the opt-in live LLM gate.
- Key modules:
  - `decision_state.read_decision_state(repo, run_id)` → `DecisionState` with `facts()`, `to_packet()`, `ids()`
  - `legal_tools.legal_set(state, catalogue)` → `LegalSet(decision_point, candidates, rule_choice)`; `Candidate(tool_name, arguments, label, description)`
  - `policy.bind_context(contextual_policy, repo, catalogue)` → a callable usable as `AgentLoop`'s policy
  - `policies.RulePolicy` / `JevPolicy(judge, act_at, review_below)` → `Decision`
  - `shadow.ShadowLadder(executor, advisors, sink)` — advisors are recorded and can never change execution (proven by `tests/coordinator/test_shadow.py`)
  - `operator_choice.pending_decision` / `submit_choice` — the operator surface; it never exposes advisor output before a choice exists
- The fixture `fixtures/decisions/turns.v1.jsonl` holds 53 decision turns generated by driving the real gateway; the operator approved its labels on 2026-09-20.

## 5. Known gaps you inherit (each one is a P3a task)

1. **One task per run.** `read_decision_state` takes the latest task, and `legal_set` only reaches the evidence gate when `task_id is None`. A run with an open task never evaluates another detection. This makes duplicate handling untestable and multi-detection patrols unmodelled.
2. **The catalogue has 2 plans**, and `PLAN_ROUTE_ONLY_2_ZONE` names a station count, which invites the station-versus-plan confusion that made GLM escalate in the WP1 live gate.
3. **Patroller choice is hardcoded to rank 1** (`legal_tools` ASSIGN branch).
4. **No replanning.** Once a plan is approved there is no path to propose another within the run.
5. **No closer-look choreography.** Re-flying a station to get better evidence does not exist; it likely needs a WP5 amendment. Confirm with the operator before building.
6. **No pose, staleness or weather facts**, because no data source exists (WP5 pending).
7. **`AgentLoop` still has no runtime wiring**; it is exercised by tests and `scripts/rehearsal_run.py` only.

## 6. Parallel working agreement (avoid merge pain)

The other session keeps P2 and the demo. Until the freeze:

| Owned by the P2 session | Owned by you (P3) |
|---|---|
| `fixtures/decisions/*`, `artifacts/**` | `fixtures/decisions_v2/*` (new), `artifacts/p3/**` |
| `src/sentinel/chat.py`, `scripts/rehearsal_run.py`, `scripts/shadow_sweep.py` | new modules under `src/sentinel/coordinator/` (e.g. `tasks_multi.py`, `replan.py`, `assignment_policy.py`) |
| `docs/superpowers/plans/2026-09-19-p2-*` | `docs/superpowers/plans/2026-09-20-p3*` |

Shared files — `legal_tools.py`, `decision_state.py`, `shadow.py`, `policies.py`, `operator_choice.py` — are **frozen for the P2 session after P2b lands**; you own them from then on. If you must change one before that, say so in the branch and rebase rather than duplicating logic.

Merge order is P1 → P2 → P3, and every merge is an operator gate.

## 7. Gates you must never self-grant

| Gate | What it covers |
|---|---|
| Coherence consent | any schema or contract-fixture change: the wire schema, `survey-catalogue-v1.fixture.json`, SR documents |
| Paid live calls | any Jev or LLM provider call; state the call count, the models and a cost estimate first, and use the consent env var |
| Merge and push | every merge into `master`, and every push |
| Label approval | any new hand-labelled fixture |
| Promotion | giving any policy authority to act (the criteria live in the P3 plan) |

## 8. First moves

1. `cd C:/coding/sentinel/.worktrees/p3-decision-surface`, run `uv run python -m pytest tests/ -q -o addopts=''`, and confirm the baseline.
2. Read section 1's documents.
3. Work through `2026-09-20-p3a-decision-surface.md` with the `Workflow` tool: dev → fresh review → fix → suite gate per task, on Sonnet. Keep the gates outside the workflow.
4. Bring the operator: the catalogue proposal (rich set), and any new labels, before scoring anything.
