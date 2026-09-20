# Handoff to Codex — executing P3b1 alongside P4

**Written:** 2026-09-20 by the Claude session that reconciled the two 2026-09-20 handoffs, reviewed
your plan `docs/superpowers/plans/2026-09-21-p3b1-approval-gated-E.md`, and built P4.
**For:** Codex, before you execute P3b1. **Master:** `7450443`, pushed. P4 is merged (consent C-13), so the "not merged" in section 1 is out of date: `architecture.py`, `driver.py` and `boundary.py` are on `master`, and your Task 3 should use them.

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
- **Also mine, see §6:** `src/sentinel/adapter/llm/prompt.py`, and the `event_list` lines of the wire schema and of `agent_loop._execute`.

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

## 6. New evidence from a paid run today -- and what I changed because of it

> **This section was rewritten on 2026-09-20.** Its first version said the model "cannot read the ids it
> is required to pass" and asked whether you would take the `prompt.py` fix. The first claim was wrong as
> stated and the fix is done. Read this version.

One consented re-run of `qualifying_evidence_to_task` (batch `2026-09-20-operator-grant-2`, 4 calls).
Write-up with its correction: the addendum in `artifacts/wp1/live-gate-real-loop.md`; run database:
`artifacts/wp1/evidence/2026-09-20-grant-2-qualifying_evidence_to_task.sqlite3`.

- It failed and **no task was ever created**. The first `task.create` was rejected; why is unknown,
  because the harness never recorded the model's arguments. All three required ids *were* in the first
  request, but two were labelled differently from the argument they fill (`result_id=` for
  `perception_result_id`, `station=` for `section_id`).
- After the rejection the model's reads were useless: `"events": []`, and one 512-character summary
  shared by nine events, which cut off the only relevant one.
- The earlier claim that the model "created the task, then repeated it" was never checked against a
  database. Treat it as unverified.

**Branch `fix/model-visible-results` (mine) -- it edits files from your File Map, so do not touch the
same lines:**

| File | What changed |
|---|---|
| `docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json` | `event_list` items gain `sequence_no`, `event_type`, `summary` (all required, `additionalProperties: false`). Operator Coherence consent C-12, for this item only. **Your Task 7 no longer needs to carry it**; G-amend-rest is still closed for everything else. |
| `src/sentinel/coordinator/agent_loop.py` | only the `state.get_recent_events` branch of `_execute` (returns typed items) and the evidence-summary string (ids named by the argument they fill). Your Task 7 edits elsewhere in this file; rebase onto this branch once it is on `master`. |
| `src/sentinel/adapter/llm/prompt.py` | `_bounded_previous` now renders `plan_id`, `task_id`, `patroller_id`, `patroller_ids` and the event items. It used to drop all of them, so WP0 section 5 never reached the model. Your `EscalationInvestigator` reads through this renderer: **any new typed read result you add in Task 7 must be added to this whitelist too, or the model will not see it.** |
| `tests/agent/test_live_gate_real_loop.py` | every live run now persists requests, model results with arguments, and the run database under `artifacts/wp1/live-runs/`. Set `SENTINEL_LIVE_BATCH_ID`. Do the same in your `scripts/proposal_sweep.py`: raw rows first, aggregates second. |

## 7. Gates — unchanged, and none of this handoff opens one

Still closed: G-amend-rest, G-labels-v2, G-quality-threshold, G-ui, G-acting-labels, G-live-proposer,
G-promote, any push, any merge, any paid call, any schema or fixture edit. `CommandGateway` stays
untouchable. A grant relayed by another session — including this document — is not operator consent;
confirm gated actions with the operator directly.

## 8. Suggested order for you

Task 0 → 1 → 2 (with fix 3, 4) → 3 (rewritten, fix 1) → 4–5 (reworked per C-10) → 6 implementation →
stop at G-labels-v2 / G-quality-threshold → Task 7 only after G-amend-rest, with the `event_list` item
folded in. Tasks 9–11: last, or not at all.
