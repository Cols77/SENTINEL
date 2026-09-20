# P4 — One agent loop, two selectable architectures (F and E)

**Written:** 2026-09-20 by the Claude session that reconciled the two 2026-09-20 handoffs
(`docs/handoffs/2026-09-20-astra-followup-and-architecture-E.md` on branch
`fix/astra-evidence-and-coverage`, and `2026-09-20-HANDOFF-coordinator-decision-line.md`).

**Goal:** the coordinator agent loop gets a real composition root and a real driver in `src/`,
and one switch selects which architecture runs through it:

- **F** (default, the 2026-09-25 demo path): rules execute, the operator chooses at `SELECT_PLAN`,
  advisors answer in shadow.
- **E** (approval-gated half): identical to F everywhere except `SELECT_PLAN`, where a *proposer*
  policy emits `inspection.propose_plan`. The gateway parks the run in `WAITING_FOR_APPROVAL`; the
  operator confirms or rejects. Any proposer failure **degrades to F** on that same turn.

**Why this is one loop and not two:** E's runnable part differs from F at exactly one decision
point. The legal-action machine, `AgentLoop`, `CommandGateway` and the operator approvals are
shared. The switch is a choice of policy composition, nothing more.

**What E does NOT include here:** any model acting without the operator. No decision point is
eligible today (`EVIDENCE_GATE` is a deterministic rule by operator decision C-0a; duplicate and
near-tie points have 8 and 6 distinct packets). No `promotions.json` is created or read.

## File ownership — Codex is executing P3b1 in parallel

Codex owns every file in `2026-09-21-p3b1-approval-gated-E.md`'s File Map. **P4 must not create
or modify any of these:**

`src/sentinel/cli.py`, `src/sentinel/coordinator/__init__.py`, `src/sentinel/coordinator/agent_loop.py`,
`src/sentinel/coordinator/proposer.py`, `src/sentinel/coordinator/proposal_review.py`,
`src/sentinel/coordinator/reads.py`, `src/sentinel/coordinator/investigator.py`,
`src/sentinel/adapter/llm/openai_compatible.py`, `src/sentinel/proposal_eval.py`,
`src/sentinel/contracts/schemas.py`, the wire schema JSON, `scripts/proposal_eval.py`,
`scripts/proposal_sweep.py`, `scripts/reband_shadow.py`, `scripts/build_acting_fixture.py`,
and their tests.

**The seam between the two lines of work** is the existing `ContextualPolicy` protocol
(`decide(request, context) -> agent_result dict`). Codex's plan pins `LlmProposer(adapter, sink)`
to exactly that shape. P4's E mode takes *any* `ContextualPolicy` as its proposer, and is tested
with a scripted stub. When Codex's `LlmProposer` lands it plugs in with no change to P4 code
(Task 6, deferred).

## Global constraints

1. Never modify `src/sentinel/coordinator/command_gateway.py`.
2. Never edit an existing test to make it pass. A failing existing test is a finding: stop and report.
   (`tests/coordinator/harness.py` is a helper, not a test; Task 2 changes one import in it.)
3. No schema, contract fixture, hand-labelled fixture, paid model call, merge into `master`, push,
   or promotion. Commits on the feature branch are fine.
4. No network call of any kind in any test or verification step.
5. **F must not change.** `build_policy("F", ...)` returns the very same object graph the code
   builds today — a bare `ShadowLadder(RulePolicy(), advisors, sink)`, not a wrapper. The existing
   `tests/test_rehearsal_run.py` and `tests/test_live_consent_gates.py` must pass unedited.
6. Suite command (5–7 min, run in the background, never assume it hung):
   `rtk proxy uv run python -m pytest tests/ -q -o addopts='' -p no:cacheprovider`
   Without the `rtk proxy` prefix a hook can swallow the run and print "No tests collected".
7. Work happens in `.worktrees/p4-arch-switch` on `feat/p4-architecture-switch`, never in the main checkout.

---

## Task 0 — Close out the uncommitted change on `fix/astra-evidence-and-coverage`

**Where:** `C:/coding/sentinel/.worktrees/astra-followup` (existing worktree).

Two files are modified and uncommitted: `src/sentinel/coordinator/policies.py` and
`tests/coordinator/test_instruction_premises.py`. They make `INSTRUCTIONS["EVIDENCE_GATE"]` a
callable of the packet's facts. `docs/handoffs/` is untracked.

1. In `policies.py`, the `JevPolicy.__init__` annotation still reads `instructions: dict[str, str]`.
   Change it to `dict[str, Any]` so it matches the `INSTRUCTIONS` annotation. Nothing else.
2. Run the full suite (constraint 6). Expected: 0 failed.
3. If green: `git add` the two files and commit
   `fix(coordinator): derive the EVIDENCE_GATE instruction from the packet's facts`; then
   `git add docs/handoffs/` and commit `docs: handoff for the Astra follow-up`.
4. If not green: do **not** commit. `git stash` nothing, revert nothing; report the failures verbatim.

**Produces:** the branch tip sha, and the suite counts.

## Task 1 — Worktree

From `C:/coding/sentinel`: `git worktree add -b feat/p4-architecture-switch .worktrees/p4-arch-switch <sha from Task 0>`
(pin the sha — a branch name cuts from wherever that branch is *now*). Then in the worktree:
`uv sync --extra dev`. Copy this plan file from the main checkout into the worktree at the same
path and commit it (`docs(plans): P4 one loop, two architectures`). Sanity check:
`rtk proxy uv run python -m pytest tests/coordinator -q -o addopts='' -p no:cacheprovider` → 0 failed.

## Task 2 — `CoordinatorBoundary` moves into `src/`

`scripts/rehearsal_run.py` imports the loop's port adapter from `tests.coordinator.harness`.
Production code cannot depend on `tests/`.

- Create `src/sentinel/coordinator/boundary.py` containing `CoordinatorBoundary` exactly as it is in
  `tests/coordinator/harness.py` (three methods: `submit`, `read_state`, `read_events`), with `__all__`.
- In `tests/coordinator/harness.py`, delete the class body and replace it with
  `from sentinel.coordinator.boundary import CoordinatorBoundary  # noqa: F401` so every existing
  import of `harness.CoordinatorBoundary` keeps working.
- Create `tests/coordinator/test_boundary.py`: `harness.CoordinatorBoundary is boundary.CoordinatorBoundary`;
  `submit` delegates to the gateway; `read_state` returns a dict with `state_version`.
- Run `tests/coordinator`. Commit `refactor(coordinator): CoordinatorBoundary lives in src`.

## Task 3 — The architecture switch

Create `src/sentinel/coordinator/architecture.py`:

```python
ARCHITECTURES = ("F", "E")
DEFAULT_ARCHITECTURE = "F"
ARCHITECTURE_ENV = "SENTINEL_ARCHITECTURE"

class ArchitectureUnavailable(RuntimeError): ...

def resolve_architecture(value: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    """Explicit value wins, then the env var, then "F". Case-insensitive, stripped.
    Unknown -> ValueError naming the allowed values. Never silently falls back on a typo."""

class ArchitectureRouter:
    """ContextualPolicy for architecture E. SELECT_PLAN goes to `proposer`; everything else, and
    every proposer failure, goes to `ladder` (architecture F)."""
    def __init__(self, ladder: ContextualPolicy, proposer: ContextualPolicy, sink: AuditSink) -> None
    def decide(self, request, context) -> dict
    def close(self) -> None      # closes the ladder if it has close()

def build_policy(architecture: str, *, advisors, sink, proposer: ContextualPolicy | None = None) -> ContextualPolicy:
    """F -> ShadowLadder(RulePolicy(), advisors, sink), returned bare.
    E -> ArchitectureRouter(that same ladder, proposer, sink); proposer None -> ArchitectureUnavailable
    whose message says the LLM proposer comes from plan P3b1 Task 2."""
```

`ArchitectureRouter.decide` rules — the router, not the proposer, is the boundary:

1. `context.legal.decision_point != SELECT_PLAN` → `return self.ladder.decide(request, context)`. No audit record of its own.
2. At `SELECT_PLAN`, call `self.proposer.decide(request, context)` inside `try/except Exception`.
3. Accept the result **only if** it has `status == "TOOL_CALLS"`, exactly one tool call, that call's
   `tool_name == "inspection.propose_plan"`, and its `arguments["catalogue_plan_id"]` is the label of a
   candidate in `context.legal.candidates` whose `tool_name == "inspection.propose_plan"`.
   (Read `legal_tools.Candidate` and `policies.RulePolicy` for the real field names before writing this.)
4. Accepted → write `{"record": "architecture_route", "architecture": "E", "run_id", "state_version",
   "decision_point", "route": "proposer", "label": <plan id>}` and return the proposer's result.
5. Anything else (exception, `finish`, wrong tool, unknown plan, malformed dict) → write the same record
   with `"route": "fallback_F"` and `"reason"` (`"exception:<TypeName>"`, `"proposer_escalated"`,
   `"illegal_tool:<name>"`, `"unknown_plan"`, `"malformed_result"`), then
   `return self.ladder.decide(request, context)`.

Create `tests/coordinator/test_architecture.py` (use `tests/coordinator/p3_helpers.py` and the patterns
in `tests/coordinator/test_shadow.py` to build a real `PolicyContext`; no mocks of `LegalSet`):

- `resolve_architecture`: default F; env var honoured; explicit beats env; `" e "` → `"E"`; `"G"` raises.
- `build_policy("F")` returns an instance whose `type(...) is ShadowLadder`.
- `build_policy("E")` with no proposer raises `ArchitectureUnavailable`.
- E at `SELECT_PLAN` with a legal stub proposal: proposer result returned, one `architecture_route`/`proposer`
  record, **no** `judgment` record.
- E, each of the five fallback reasons: the returned result equals what a bare ladder returns for the same
  context, a `judgment` record exists, and the `architecture_route` record names the reason.
- E at a non-`SELECT_PLAN` point: the proposer stub is never called (it raises if called).
- A stub proposer that returns a `task.create` call is refused (`illegal_tool:task.create`).

TDD: write the tests, watch them fail on the import, implement, watch them pass, run `tests/coordinator`.
Commit `feat(coordinator): architecture switch — F bare, E routes SELECT_PLAN to a proposer`.

## Task 4 — A driver in `src/`, and `--architecture` on the rehearsal

The run-driving loop lives inside `scripts/rehearsal_run.py::run_rehearsal`. Extract it.

Create `src/sentinel/coordinator/driver.py`:

```python
def drive_run(*, repo, gateway, policy, catalogue, run_id, agent_run_id, request_text,
              on_approval: Callable[[DecisionState], None],
              on_judgment: Callable[[dict], None],
              max_steps: int = 10, max_turns: int = 8) -> dict:
    """Runs AgentLoop until the run is terminal. WAITING_FOR_APPROVAL -> on_approval(state);
    NEEDS_REVIEW with a pending decision -> on_judgment(pending). Returns
    {"status", "steps", "approvals", "judgments"} (the last two are counts = operator touches).
    Raises RuntimeError on the same three conditions run_rehearsal raises on today."""
```

`policy` is a `ContextualPolicy`; `drive_run` does the `bind_context` itself. The callbacks are the
operator: `drive_run` never confirms anything on its own.

Refactor `scripts/rehearsal_run.py`:
- import `CoordinatorBoundary` from `sentinel.coordinator.boundary` (keep `FakeWorker`, `envelope` from the harness — they simulate the drone worker, which is test-side by nature);
- `run_rehearsal` gains `architecture: str | None = None` and `proposer: ContextualPolicy | None = None`,
  builds the policy with `build_policy(resolve_architecture(architecture), advisors=..., sink=..., proposer=...)`,
  and calls `drive_run` with `_confirm_plan`/`_confirm_task` inside `on_approval` and the existing
  `_resolve_label` + `submit_choice` inside `on_judgment`;
- CLI gains `--architecture {F,E}` (default: env, then F) and `--proposer scripted:<PLAN_ID>`, which builds a
  tiny `ScriptedProposer` (defined in the script) that returns `one_call(request, "inspection.propose_plan", {...})`.
  Read `RulePolicy` to see the exact arguments `inspection.propose_plan` needs. `--architecture E` without
  `--proposer` exits 2 with the `ArchitectureUnavailable` message. There is **no** live-LLM proposer option in this task.
- the summary dict gains `"architecture"`, `"approvals"`, `"judgments"`.
- advisors are still built **before** any side effect (the consent refusal test depends on it).

Tests, new file `tests/test_rehearsal_architecture.py`:
- F via `main([...])` completes; summary `architecture == "F"`.
- E with `--proposer scripted:<a legal plan for the default request>` reaches `COMPLETED` with
  `judgments == 0` and `approvals == 2` — the operator's "approvals only" target.
- E with a scripted plan id that is not in the catalogue completes through the F path: `judgments == 1`,
  and the audit JSONL has an `architecture_route` record with `route == "fallback_F"`.
- `--architecture E` with no `--proposer` returns 2 and creates no database file.

Existing `tests/test_rehearsal_run.py` and `tests/test_live_consent_gates.py` must pass **unedited**.
Commit `feat(coordinator): drive_run in src; rehearsal selects architecture F or E`.

## Task 5 — Full-suite gate

Run the full suite (constraint 6). 0 failed, or stop and report. Record the counts in the commit body of
an empty-tree-safe docs commit: append a "Results" section to this plan with the sha and counts.

## Task 6 — DEFERRED until Codex's P3b1 Task 2 is merged

Add `--proposer llm` to the rehearsal: lazy-import `sentinel.coordinator.proposer.LlmProposer`, build it
over `OpenAICompatibleAgentModel`, and require `require_live_consent("G-rehearsal-E")` first, because every
E run with a live proposer is a paid call. Wire `sentinel coordinate --architecture` into `cli.py` only after
Codex's Task 5 has landed there. Not part of this execution.

## Task 7 — Proposal document for the `event_list` schema defect (docs only)

Create `docs/superpowers/specs/2026-09-21_wp0-proposal-event-list-items.md`. Content:
- the defect: in `docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json`, the `event_list` branch of
  `AgentToolResultPayload` has `items: {"type": "object", "additionalProperties": false}` with no
  `properties`, so no populated event can validate; the workaround in `agent_loop._execute` returns
  `"events": []` and packs summaries into `safe_summary` (quote both, with line numbers);
- the consequence: `state.get_recent_events`, one of the model's two read tools, returns prose the model must parse;
- the proposed item shape: `sequence_no` (integer ≥ 0), `event_type` (string, maxLength 64), `summary`
  (string, maxLength 256), all required, `additionalProperties: false` — check `_event_summary` in
  `agent_loop.py` for what is actually available and propose exactly that;
- compatibility: an empty array stays valid, so the workaround keeps validating; `tool_policy_revision` bump;
- sequencing: this is a wire-schema edit, so it should ride in the **same schema revision** as the rest of the
  WP0 amendment (P3b1 Task 7, gate G-amend-rest) rather than as a separate edit to the same file;
- the gate: Coherence consent from the operator. **Do not edit the schema.**

Commit `docs(specs): proposal — event_list items need properties`.

---

## Operator gates this plan stops at

| Gate | What it opens |
|---|---|
| G-merge-astra | merge `fix/astra-evidence-and-coverage` into `master` |
| G-merge-p4 | merge `feat/p4-architecture-switch` into `master` (the operator may hold this until after the 2026-09-25 freeze; default-F makes it safe either way) |
| G-coherence-event-list | the schema edit Task 7 proposes |
| paid batches | the one-scenario live gate re-run, the Jev re-sweep, any live E run |

---

## Results

Task 5 (Full-suite gate) executed from `C:/coding/sentinel/.worktrees/p4-arch-switch` on branch
`feat/p4-architecture-switch`.

- Branch tip sha: `a5993acbeb63795a4be6b5b9a98015121be72e81`
- Command: `rtk proxy uv run python -m pytest tests/ -q -o addopts='' -p no:cacheprovider`
- Pytest summary line: `636 passed, 3 skipped, 3 warnings in 318.68s (0:05:18)`
- `git -C C:/coding/sentinel/.worktrees/p4-arch-switch diff --name-only 150978a..HEAD`:

```
docs/superpowers/plans/2026-09-20-p4-one-loop-two-architectures.md
docs/superpowers/specs/2026-09-21_wp0-proposal-event-list-items.md
scripts/rehearsal_run.py
src/sentinel/coordinator/architecture.py
src/sentinel/coordinator/boundary.py
src/sentinel/coordinator/driver.py
tests/coordinator/harness.py
tests/coordinator/test_architecture.py
tests/coordinator/test_boundary.py
tests/test_rehearsal_architecture.py
```
