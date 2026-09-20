# Handoff to Codex — executing P3b1 alongside P4

**Written:** 2026-09-20 by the Claude session that reconciled the two 2026-09-20 handoffs, reviewed
your plan `docs/superpowers/plans/2026-09-21-p3b1-approval-gated-E.md`, and built P4.
**For:** Codex, before you execute P3b1. **Master:** `b8af3b9` (local; origin is at `b302636`).

Read this first. It changes three of your tasks, fixes two that cannot run as written, and tells you
which files are yours.

---

## 1. What exists now that your plan does not know about

**P4 — one agent loop, two selectable architectures.** Branch `feat/p4-architecture-switch`, worktree
`.worktrees/p4-arch-switch`, 6 commits, full suite 636 passed / 3 skipped / 0 failed. **Not merged into
`master`** — the operator has not decided when. Plan and results:
`docs/superpowers/plans/2026-09-20-p4-one-loop-two-architectures.md` on that branch.

| File (P4 owns these) | What it is |
|---|---|
| `src/sentinel/coordinator/architecture.py` | `resolve_architecture()` reads `SENTINEL_ARCHITECTURE` (`F` default, `E`). `build_policy("F", ...)` returns the bare `ShadowLadder(RulePolicy(), advisors, sink)` — today's demo path, untouched. `build_policy("E", ..., proposer=...)` returns an `ArchitectureRouter`. |
| `src/sentinel/coordinator/driver.py` | `drive_run(...)`: the run-driving loop, in `src/`. Operator actions are two callbacks; it submits nothing itself except `RUN_CREATE`. |
| `src/sentinel/coordinator/boundary.py` | `CoordinatorBoundary`, moved out of `tests/coordinator/harness.py` (the harness re-exports it). |
| `scripts/rehearsal_run.py` | gains `--architecture {F,E}` and `--proposer scripted:<PLAN_ID>`. |

**How E works in the router.** At `SELECT_PLAN` only, the router calls `proposer.decide(request, context)`.
It accepts the result **only if** it is one `TOOL_CALLS` call named `inspection.propose_plan` whose
`catalogue_plan_id` is a current legal candidate. Anything else — an exception, a `finish`, another tool,
an unknown plan, a malformed dict — is logged as `architecture_route / fallback_F / <reason>` and the turn
is handed to the F ladder. A broken proposer cannot break a run; it costs one operator choice.

Verified end to end: F completes with 2 approvals + 1 operator choice; E with a legal scripted proposal
completes with **2 approvals and 0 choices** (the operator's "approvals only" target); E with an illegal
proposal completes through F.

## 2. The seam — how your `LlmProposer` plugs in

Your Task 2 pins `LlmProposer(adapter, sink).decide(request, context) -> agent_result dict`. That is the
`ContextualPolicy` protocol, which is exactly what `build_policy(..., proposer=)` takes. **No P4 code
changes when your class lands.** What remains is P4 Task 6, which is *mine to do after your Task 2 merges*:
a `--proposer llm` option that lazy-imports your class behind a consent gate (every live E run is a paid call).

Consequence for your Task 3: once P4 is on `master`, prove the lifecycle through
`build_policy("E", proposer=LlmProposer(...))` and `drive_run`, not through a bare
`bind_context(LlmProposer(...))`. If P4 is not merged when you get there, write it as your plan says and
leave a note; do not copy P4's files into your branch.

## 3. File ownership — assign files, not tasks

Two collisions happened on 2026-09-20 because sessions shared tasks. So:

- **Yours (from your File Map):** `src/sentinel/cli.py`, `src/sentinel/coordinator/__init__.py`,
  `src/sentinel/coordinator/agent_loop.py`, `proposer.py`, `proposal_review.py`, `reads.py`,
  `investigator.py`, `src/sentinel/adapter/llm/openai_compatible.py`, `src/sentinel/proposal_eval.py`,
  `src/sentinel/contracts/schemas.py`, the wire schema JSON (gated), `scripts/proposal_eval.py`,
  `scripts/proposal_sweep.py`, `scripts/reband_shadow.py`, `scripts/build_acting_fixture.py`, and their tests.
- **Mine:** the four P4 files above, plus `tests/coordinator/test_architecture.py`,
  `tests/coordinator/test_boundary.py`, `tests/test_rehearsal_architecture.py`.
- **Unowned, and someone must take it — see §6:** `src/sentinel/adapter/llm/prompt.py`.

## 4. Operator decisions taken on 2026-09-20 that change your plan

Authoritative record: `docs/superpowers/specs/2026-09-20_operator-consent-record.md`, entries C-7 to C-11.

**C-10 — E's operator experience is "propose, then confirm". Rework Tasks 4 and 5.**
Your Tasks 4–5 (and the brief they came from) hide the model's suggestion behind
`--show-assistant-analysis` and record `analysis_expanded`. That cannot work in this flow. The proposer
emits `inspection.propose_plan`, the run parks in `WAITING_FOR_APPROVAL` with **one specific plan
pending**, and the operator must see that plan to confirm it. The pending plan *is* the suggestion.
The collapsed view in your Task 4 skeleton does not even say which plan is pending, so `review-plan
--decision CONFIRM` would confirm something the operator was not shown. "Anchoring" — the operator being
steered by what the machine showed first — is built into propose-then-confirm and cannot be hidden away.
The alternative (operator picks from a fixed-order list, suggestion hidden) is three operator actions and
is architecture F with an optional advisor; the operator declined it.

So, for Tasks 4–5:
- **Keep:** `submit_plan_review` (it is the missing production path for confirming/rejecting a plan), the
  `plan_review` audit record, the fixed catalogue order wherever a list is shown, and showing the reason
  together with the facts it cites.
- **Drop:** `show_analysis`, `analysis_expanded`, `--show-assistant-analysis`, and the tests that pin the
  suggestion as absent.
- **Add:** the pending-plan view shows the proposed plan, its reason and cited facts, **and the other
  catalogue plans in fixture order beneath it**, so rejecting is as easy as confirming. Record in
  `plan_review`: `decision`, `proposed_label`, and `seconds_to_decision` (from the `PLAN_PROPOSED` event to
  the review). Rejection rate and time-to-confirm are the anchoring signals this flow can actually yield.
- Task 6's metrics are unaffected; add the two above to its report.

**C-8 — the Jev re-sweep is deferred.** Your Task 10 Steps 5–6 and Task 11 do not run. Reason, which your
own Task 11 Step 1 already states: no decision point is eligible for promotion, so the batch has no
consumer. Task 9 and Task 10 Steps 1–4 (offline tooling) may still be built, but they are now low priority —
do them last or not at all, and say which.

**C-11 — the `event_list` fix rides in your Task 7's schema revision.** Proposal:
`docs/superpowers/specs/2026-09-21_wp0-proposal-event-list-items.md` (on the P4 branch). This is sequencing,
**not consent**: G-amend-rest is still closed. §6 explains why this item just became important.

## 5. Defects in your plan as written

1. **Task 3's test cannot run.** `PLAN_MARKER_VERIFIED_2_ZONE` does not exist — the live catalogue has two
   plans, `PLAN_MARKER_VERIFIED_6` and `PLAN_ROUTE_ONLY_2_ZONE`. `P3Run(tmp_path)` needs a `name`
   argument, and `run.create()`, `run.review_plan()`, `run.legal()`, `run.event_types()` do not exist; the
   real helpers are `propose_plan`, `decide_latest_plan`, `state` (see `tests/coordinator/p3_helpers.py`).
   Your brief says executors are cheaper models that see only their task: one of them will either stall
   here or start editing helpers. Rewrite the test against the real helpers before dispatching it.
2. **Every test command omits `rtk proxy`.** On this machine a hook can swallow a bare
   `rtk uv run pytest` and print "No tests collected". The working form is
   `rtk proxy uv run python -m pytest <paths> -q -o addopts='' -p no:cacheprovider`. The full suite takes
   5–7 minutes; give it a 10-minute timeout.
3. **Task 2 passes a model's `finish` through unchecked** (`if call.tool_name == "finish": return
   result.to_dict()`). Coerce it to your own `_escalate(...)`. The P4 router already refuses it, so this
   is defence in depth, but your class is also used outside the router (Task 6's sweep).
4. **Task 2 writes `proposal_recommendation` before the gateway has accepted the proposal.** A rejected
   submission leaves a record for a plan that was never pending. Your Task 4 note ("match the
   recommendation to the pending plan and state version") covers the read side; say so in Task 2 too.
5. **Task 1 passes a `PolicyContext` through a parameter named `read_tools`.** It works, and renaming is
   out of scope, but add a one-line comment at the call site or the next reader will "fix" it.
6. **Two timing tests are load-sensitive:** `tests/coordinator/test_shadow_latency.py::test_total_turn_cost_is_one_timeout_not_one_per_advisor`
   and `test_shadow.py::test_non_interference_opposite_raising_and_timeout_advisors` failed once when two
   pytest processes ran at the same time, and pass alone. Never run two suites concurrently.

## 6. New evidence from a paid run today — read before touching the agent loop

One consented re-run of `qualifying_evidence_to_task` (batch `2026-09-20-operator-grant-2`, 4 calls).
Full write-up: the addendum in `artifacts/wp1/live-gate-real-loop.md`; the run's database is kept at
`artifacts/wp1/evidence/2026-09-20-grant-2-qualifying_evidence_to_task.sqlite3`.

- It failed, and **no task was ever created** — the `tasks` table is empty. The model called
  `task.create`, read events twice, called `task.create` again, and the adapter's parser raised
  `INVALID_ARGUMENTS`. `task.create` needs `perception_result_id`, `evidence_artifact_id` and `section_id`;
  those live only in the `DETECTION_RECORDED` payload, and `state.get_recent_events` returns
  `"events": []` plus truncated prose because of the `event_list` schema defect. **The model cannot read
  the ids it is required to pass.** The earlier claim that it "created the task, then repeated it" was
  never checked against a database and should be treated as unverified.
- **Unowned defect, found by reading:** `prompt._bounded_previous` whitelists only `kind`, `run_status`,
  `phase`, `state_version`. The typed ids WP0 §5 added (`plan_id`, `task_id`, ranking and assignment ids)
  are dropped before the prompt is built — the amendment never reaches the model. This is a plain code
  fix with no gate, in `src/sentinel/adapter/llm/prompt.py`. **Tell the operator whether you take it;
  otherwise I will.** Your `EscalationInvestigator` (Task 8) reads through the same renderer, so it is on
  your path either way.
- Do not plan another paid run of that scenario until both are fixed.

## 7. Gates — unchanged, and none of this handoff opens one

Still closed: G-amend-rest, G-labels-v2, G-quality-threshold, G-ui, G-acting-labels, G-live-proposer,
G-promote, any push, any merge, any paid call, any schema or fixture edit. `CommandGateway` stays
untouchable. A grant relayed by another session — including this document — is not operator consent;
confirm gated actions with the operator directly.

## 8. Suggested order for you

Task 0 → 1 → 2 (with fix 3, 4) → 3 (rewritten, fix 1) → 4–5 (reworked per C-10) → 6 implementation →
stop at G-labels-v2 / G-quality-threshold → Task 7 only after G-amend-rest, with the `event_list` item
folded in. Tasks 9–11: last, or not at all.
