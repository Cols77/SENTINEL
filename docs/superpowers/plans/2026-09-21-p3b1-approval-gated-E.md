# P3b1 Approval-Gated Architecture E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the non-acting half of architecture E first: an LLM may propose a survey plan and investigate an escalation, but every consequential choice remains operator-gated and every mutation still passes through the existing coordinator boundary.

**Architecture:** `LlmProposer` is a `ContextualPolicy` used only at `SELECT_PLAN`. It gives an `AgentModelPort` a narrowed request whose legal tools are exactly `inspection.propose_plan` and `finish`, validates the returned call against the current `LegalSet`, records the recommendation in the append-only audit, and returns the call to the unchanged `AgentLoop`/`CommandGateway` path. A read-only presentation projection keeps catalogue options in fixed order and withholds the recommendation until the operator explicitly expands it. `EscalationInvestigator` is a later, separately gated loop over typed reads plus `operator.ask`; it cannot be implemented until the remainder of the WP0 amendment is granted.

**Tech Stack:** Python 3.11+, dataclasses and protocols, SQLite coordinator repository, JSON Schema, pytest, existing `AgentModelPort`, `ContextualPolicy`, `AgentLoop`, `LegalSet`, and append-only audit sinks.

**Specs and evidence:**

- `docs/superpowers/plans/2026-09-19-p3-hybrid-E-followup.md` Tasks 4-6
- `docs/superpowers/plans/2026-09-20-p3a-decision-surface.md`
- `docs/superpowers/plans/HANDOFF-P3.md`
- `docs/superpowers/specs/2026-09-19_wp0-amendment-decision-context-and-reads.md`
- `docs/superpowers/specs/2026-09-20_wp0-proposal-p3-decision-surface-amendments.md`
- `artifacts/decisions/live-shadow-jev.md`
- `artifacts/decisions/live-shadow-llm.md`
- `artifacts/decisions/live-comparison-2026-09-20.md`
- `artifacts/p3/operator-load.md`

---

## Global Constraints

1. Do not modify `src/sentinel/coordinator/command_gateway.py`. If a task appears to require a gateway change, write a proposal and stop at an operator gate.
2. Do not modify an existing test to make it pass. Add tests. An existing failure is a finding and stops execution.
3. No schema, contract fixture, registered requirement, new hand-labelled fixture, paid model call, merge, push, or policy promotion occurs without the named operator gate.
4. The proposer and investigator are **not acting policies**. Their outputs reach an operator before any consequential choice. The acting-policy promotion criteria therefore do not gate Tasks 1-6. The load-bearing safety condition is the tested presentation rule in Tasks 4-5.
5. Approval-gated does not mean quality-free. Report held-out proposal acceptance and `operator_touches`; do not substitute acting-policy metrics such as HIGH-band coverage, `false_act`, or the 1500 ms latency ceiling.
6. The investigator's inability to mutate is enforced by the request's `legal_tools` and by coordinator-side validation. A prompt instruction is never treated as the boundary.
7. Existing catalogue order is authoritative. A recommendation never reorders options.
8. No live provider call is part of implementation verification. A later live command must state call count, model names, and estimated cost, then stop for consent.
9. The plan starts from `master` at or after `258b792`. Re-check the branch because concurrent sessions are active.

## Current-State Correction

The Codex brief says the WP0 amendment unblocks `EscalationInvestigator`. The repository is more precise:

- Amendment **section 5 only**, typed mutation result IDs, was granted and merged in `1881129`/`1c401e6`.
- Amendment sections 2-4 (`decision_context`, typed reads) and 6 (`operator.ask`) still say “remain proposals” in the authoritative amendment document.
- Therefore Tasks 1-6 below are unblocked, while Tasks 7-8 stop at **G-amend-rest** until the operator grants the remaining schema surface.

This plan does not reinterpret the partial grant as consent for the rest.

## Execution Reconciliation (2026-09-20, updated during execution)

- `master` baseline is now `52ae6eb`; P4, `fix/model-visible-results`, and `fix/command-payload-keys` are merged.
- Baseline verification in the P3b1 worktree: **662 passed, 3 skipped, 0 failed**.
- Task 1 complete and reviewed: `b16af13`.
- Task 2 complete after quality fixes and reviewed: `eb31517`, `2f47202`.
- Task 3 complete and reviewed: `ec36ab6`.
- Task 4 complete and independently spec/quality approved at `a492b47`.
- Task 5 implemented and spec-approved at `08096a0`; quality review requires retryable audit recovery plus hardened JSONL/terminal rendering before approval.
- Operator consent C-10 replaces the original hidden-analysis design in Tasks 4-5 with **propose, then confirm**. The pending plan is always shown; anchoring is measured by rejection rate and time-to-decision.
- The older Jev handoff's three-arm shadow sweep is superseded. The current design is mode `J` in `sentinel.survey`: `JevProposer` chooses among complete, pre-built legal tool calls and returns the chosen call through the shared policy seam. The run then parks at `WAITING_FOR_APPROVAL`; Jev is active as a proposer, but receives no authority to execute without the operator gate. This changes nothing in Tasks 1-8 and must not be reimplemented in the P3b1-owned files.

## File Map

| File | Responsibility |
|---|---|
| `src/sentinel/adapter/llm/openai_compatible.py` | Forward the already-supported contextual capability view into prompt rendering. |
| `src/sentinel/coordinator/proposer.py` | Narrow, validate, and audit the approval-gated LLM proposal. |
| `src/sentinel/coordinator/proposal_review.py` | Build an anti-anchoring operator view and submit an operator plan review through the existing gateway API. |
| `src/sentinel/proposal_eval.py` | Pure proposal-quality and operator-load measurements. |
| `scripts/proposal_eval.py` | Offline report CLI; no provider calls. |
| `scripts/proposal_sweep.py` | Explicitly consented proposer batch that preserves raw proposal records. |
| `src/sentinel/coordinator/reads.py` | Conditional typed-read implementations after G-amend-rest. |
| `src/sentinel/coordinator/investigator.py` | Conditional bounded read-only investigator after G-amend-rest. |
| `scripts/reband_shadow.py` | **Dropped historical item.** Do not create it for P3b1; active Jev mode `J` belongs to `sentinel.survey`. |

---

### Task 0: Create the execution worktree and pin the baseline

**Status:** Unblocked.

**Files:**

- No source files.
- Verify: repository status and the full test suite.

**Interfaces — Consumes:** this plan and current `master`.

**Interfaces — Produces:** isolated branch `feat/p3b1-approval-gated-e` and a recorded baseline commit/test count in the execution notes.

- [ ] **Step 1: Re-read branch state before creating anything**

Run:

```powershell
rtk git status --short --branch
rtk git log -1 --oneline
rtk git worktree list
```

Expected: `master` is not behind the commit named in Global Constraint 9. Preserve every unrelated untracked file.

- [ ] **Step 2: Create an isolated worktree**

Run the `using-git-worktrees` skill, then create `.worktrees/p3b1-approval-gated-e` on `feat/p3b1-approval-gated-e`. Do not reuse a worktree owned by another session.

- [ ] **Step 3: Establish the baseline**

Run:

```powershell
rtk uv sync --extra dev
rtk proxy uv run python -m pytest -q -o addopts='' -p no:cacheprovider
```

Expected: the full suite passes. If it does not, stop and report the exact pre-existing failures.

---

### Task 1: Forward `PolicyContext` through the existing LLM adapter seam

**Status:** Unblocked.

**Files:**

- Modify: `src/sentinel/adapter/llm/openai_compatible.py`
- Test: `tests/agent/test_prompt_context.py`

**Interfaces — Consumes:** `AgentModelPort.propose(request, read_tools=None)` and `build_chat_request(..., context=...)`.

**Interfaces — Produces:** when a coordinator passes `PolicyContext` as the second `propose` argument, the provider payload contains the bounded decision packet and described options; plain callers remain byte-for-byte compatible.

- [ ] **Step 1: Add a failing adapter test**

Add a test that creates an `OpenAICompatibleAgentModel` with an injected fake client, calls `adapter.propose(REQUEST, context)`, and asserts the JSON user message contains `decision_point == "SELECT_PLAN"`, the packet, and options in the supplied order. Also retain a call without context and assert those keys are absent.

```python
def test_adapter_forwards_optional_policy_context_to_prompt(fake_client_factory, context):
    adapter = OpenAICompatibleAgentModel(
        load_config(ENV), client_factory=fake_client_factory, environ=ENV
    )
    adapter.propose(REQUEST, context)
    rendered = json.loads(adapter.last_request["messages"][-1]["content"])
    assert rendered["decision_point"] == "SELECT_PLAN"
    assert [item["label"] for item in rendered["options"]] == list(context.legal.labels)
    assert rendered["decision_packet"] == context.state.to_packet()
```

- [ ] **Step 2: Run the test and verify the missing context**

Run:

```powershell
rtk proxy uv run python -m pytest tests/agent/test_prompt_context.py -q -o addopts='' -p no:cacheprovider
```

Expected: FAIL because `OpenAICompatibleAgentModel.propose()` currently omits `context=read_tools` when calling `build_chat_request`.

- [ ] **Step 3: Make the one-line production change**

Change the call in `OpenAICompatibleAgentModel.propose` to:

```python
payload = build_chat_request(
    request,
    prompt_revision=self.config.prompt_revision,
    tool_policy_revision=self.config.tool_policy_revision,
    context=read_tools,
    history=self._history.get(agent_run_id, ()),
)
```

- [ ] **Step 4: Verify focused and adapter tests**

Run:

```powershell
rtk proxy uv run python -m pytest tests/agent/test_prompt_context.py tests/agent/test_openai_compatible.py tests/agent/test_agent_runner_boundary.py -q -o addopts='' -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
rtk git add src/sentinel/adapter/llm/openai_compatible.py tests/agent/test_prompt_context.py
rtk git commit -m "feat(adapter): forward decision context to bounded prompts"
```

---

### Task 2: Implement the approval-gated `LlmProposer`

**Status:** Unblocked.

**Files:**

- Create: `src/sentinel/coordinator/proposer.py`
- Create: `tests/coordinator/test_proposer.py`
- Modify: `src/sentinel/coordinator/__init__.py`

**Interfaces — Consumes:** an `AgentModelPort`, `PolicyContext`, `AgentRequest`, current `LegalSet`, and an `AuditSink`.

**Interfaces — Produces:** `LlmProposer.decide(request, context) -> agent_result dict`; one `proposal_recommendation` audit record; only `inspection.propose_plan` or `finish(NEEDS_REVIEW)` can escape the class.

- [ ] **Step 1: Write failing tests for the hard boundary**

Cover all of these cases with a fake adapter that records the typed request it receives:

```python
def test_proposer_narrows_tools_and_accepts_a_current_catalogue_plan(): ...
def test_proposer_requires_a_nonblank_reason(): ...
def test_proposer_rejects_a_plan_not_in_the_current_legal_set(): ...
def test_proposer_turns_a_mutating_adapter_call_into_finish(): ...
def test_proposer_escalates_without_calling_adapter_outside_select_plan(): ...
def test_proposer_audits_request_candidate_reason_and_facts(): ...
```

The central assertion is:

```python
assert fake.requests[0].legal_tools == ["inspection.propose_plan", "finish"]
assert result["tool_calls"][0]["tool_name"] in {"inspection.propose_plan", "finish"}
assert sink.records == [{
    "record": "proposal_recommendation",
    "run_id": RUN_ID,
    "state_version": 2,
    "decision_point": "SELECT_PLAN",
    "label": "PLAN_ROUTE_ONLY_2_ZONE",
    "reason": "Request asks for both front zones; this is the smallest covering plan.",
    "facts": {
        "operator_request": "inspect both front zones",
        "candidate_description": "mode=ROUTE_ONLY; stations=2 (...)"
    },
}]
```

- [ ] **Step 2: Run and confirm the import failure**

Run:

```powershell
rtk proxy uv run python -m pytest tests/coordinator/test_proposer.py -q -o addopts='' -p no:cacheprovider
```

Expected: FAIL because `sentinel.coordinator.proposer` does not exist.

- [ ] **Step 3: Implement the class with coordinator-side validation**

Use this complete public shape:

```python
from __future__ import annotations

from typing import Any

from sentinel.contracts import AgentModelResult, AgentRequest

from .legal_tools import SELECT_PLAN
from .policy import PolicyContext, one_call


class LlmProposer:
    def __init__(self, adapter: Any, sink: Any) -> None:
        self.adapter = adapter
        self.sink = sink

    def _escalate(self, request: dict[str, Any], summary: str) -> dict[str, Any]:
        return one_call(request, "finish", {
            "status": "NEEDS_REVIEW", "summary": summary[:256]
        })

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        if context.legal.decision_point != SELECT_PLAN:
            return self._escalate(request, "LLM proposer is restricted to SELECT_PLAN")

        scoped = dict(request)
        scoped["legal_tools"] = ["inspection.propose_plan", "finish"]
        typed_request = AgentRequest.from_dict(scoped)
        raw = self.adapter.propose(typed_request, context)
        result = raw if isinstance(raw, AgentModelResult) else AgentModelResult.from_dict(raw)
        if result.status != "TOOL_CALLS" or len(result.tool_calls) != 1:
            return self._escalate(request, "plan proposer returned no single legal proposal")
        call = result.tool_calls[0]
        if call.tool_name == "finish":
            return result.to_dict()
        if call.tool_name != "inspection.propose_plan":
            return self._escalate(request, "plan proposer attempted a forbidden tool")

        plan_id = call.arguments.get("catalogue_plan_id")
        reason = call.arguments.get("reason")
        legal_plans = {
            candidate.label: candidate
            for candidate in context.legal.candidates
            if candidate.tool_name == "inspection.propose_plan"
        }
        if plan_id not in legal_plans or not isinstance(reason, str) or not reason.strip():
            return self._escalate(request, "plan proposer returned an invalid plan or reason")

        candidate = legal_plans[plan_id]
        self.sink.write({
            "record": "proposal_recommendation",
            "run_id": context.state.run_id,
            "state_version": context.state.state_version,
            "decision_point": SELECT_PLAN,
            "label": plan_id,
            "reason": reason.strip()[:512],
            "facts": {
                "operator_request": request["user_request"],
                "candidate_description": candidate.description,
            },
        })
        return result.to_dict()

    def forget(self, agent_run_id: str) -> None:
        forget = getattr(self.adapter, "forget", None)
        if callable(forget):
            forget(agent_run_id)
```

Do not trust the prompt or the adapter's own tool filtering. The checks above are the boundary.

- [ ] **Step 4: Export and verify**

Export `LlmProposer` from `src/sentinel/coordinator/__init__.py`, then run:

```powershell
rtk proxy uv run python -m pytest tests/coordinator/test_proposer.py tests/coordinator/test_policy.py tests/agent/test_prompt_context.py -q -o addopts='' -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
rtk git add src/sentinel/coordinator/proposer.py src/sentinel/coordinator/__init__.py tests/coordinator/test_proposer.py
rtk git commit -m "feat(coordinator): add approval-gated LLM plan proposer"
```

---

### Task 3: Prove the proposer lifecycle through the real loop

**Status:** Unblocked.

**Files:**

- Create: `tests/coordinator/test_proposer_flow.py`
- No production file is expected to change. If the test exposes a production gap, stop and report it before editing another component.

**Interfaces — Consumes:** `bind_context(LlmProposer(...))`, `AgentLoop`, the harness `CommandGateway`, and the catalogue fixture.

**Interfaces — Produces:** proof that the proposal reaches `WAITING_FOR_APPROVAL`, no non-proposal mutation occurs, rejection returns to `SELECT_PLAN`, and the rejected catalogue entry is not offered again.

- [ ] **Step 1: Add the end-to-end test**

```python
def test_llm_proposal_waits_for_operator_and_rejection_returns_to_select_plan(tmp_path):
    run = P3Run(tmp_path)
    run.create()
    sink = MemoryAuditSink()
    adapter = ScriptedProposalAdapter([
        ("PLAN_ROUTE_ONLY_2_ZONE", "covers the two requested zones"),
        ("PLAN_MARKER_VERIFIED_6", "uses marker verification after rejection"),
    ])
    policy = bind_context(LlmProposer(adapter, sink), run.repo, CATALOGUE)

    first = AgentLoop(run.gateway, policy, repository=run.repo, catalogue=CATALOGUE).run(
        run.run_id, AGENT_RUN_ID, "inspect both zones"
    )
    assert first["error_code"] == "WAITING_FOR_APPROVAL"
    assert run.state().run_status == "WAITING_FOR_APPROVAL"
    assert run.event_types().count("PLAN_PROPOSED") == 1
    assert "TASK_CREATED" not in run.event_types()

    run.review_plan("REJECT")
    assert run.state().plan_id is None
    assert run.legal().decision_point == "SELECT_PLAN"
    assert "PLAN_ROUTE_ONLY_2_ZONE" not in run.legal().labels
```

- [ ] **Step 2: Run it**

```powershell
rtk proxy uv run python -m pytest tests/coordinator/test_proposer_flow.py -q -o addopts='' -p no:cacheprovider
```

Expected: PASS using existing P3a rejection/re-proposal behavior. A failure is a plan finding, not permission to touch `CommandGateway`.

- [ ] **Step 3: Run the coordinator regression set**

```powershell
rtk proxy uv run python -m pytest tests/coordinator -q -o addopts='' -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 4: Commit**

```powershell
rtk git add tests/coordinator/test_proposer_flow.py
rtk git commit -m "test(coordinator): prove approval-gated proposer lifecycle"
```

---

### Task 4: Build the propose-then-confirm presentation projection

**Status:** Unblocked and required before the proposer is accepted.

**Files:**

- Create: `src/sentinel/coordinator/proposal_review.py`
- Create: `tests/coordinator/test_proposal_review.py`

**Interfaces — Consumes:** persisted pending-plan state, the catalogue in fixture order, and pre-gateway `proposal_recommendation` audit records.

**Interfaces — Produces:** `pending_proposal_review(repository, run_id, catalogue, audit_records) -> dict | None`; the actually pending plan is explicit, its matching reason and cited facts are adjacent, and other plans are listed separately in fixture order.

- [ ] **Step 1: Write failing presentation tests**

```python
def test_review_identifies_the_actual_persisted_pending_plan(): ...
def test_review_lists_other_plans_in_fixture_order(): ...
def test_review_shows_matching_reason_beside_cited_facts(): ...
def test_review_ignores_stale_rejected_and_unrelated_recommendations(): ...
def test_review_is_none_when_no_plan_approval_is_pending(): ...
```

Pin the returned shapes:

```python
view = pending_proposal_review(repo, RUN_ID, CATALOGUE, audit)
assert view["proposed_plan"]["label"] == "PLAN_ROUTE_ONLY_2_ZONE"
assert view["proposed_plan"]["recommendation"] == {
    "reason": "marker verification matches the request",
    "facts": {
        "operator_request": "verify markers in both zones",
        "candidate_description": "mode=MARKER_VERIFIED; stations=2 (...)"
    },
}
assert [item["label"] for item in view["other_plans"]] == [
    plan.plan_id for plan in CATALOGUE if plan.plan_id != view["proposed_plan"]["label"]
]
assert "analysis_expanded" not in view
```

- [ ] **Step 2: Run and confirm the missing module**

```powershell
rtk proxy uv run python -m pytest tests/coordinator/test_proposal_review.py -q -o addopts='' -p no:cacheprovider
```

Expected: FAIL because the projection does not exist.

- [ ] **Step 3: Implement the pure projection**

Implement these rules in `proposal_review.py`:

The implementation reads the pending plan and matching `PLAN_PROPOSED` event from persisted state. It may attach audit reason/facts only when the record matches the run, `SELECT_PLAN`, pending catalogue label, and the state version immediately preceding `PLAN_PROPOSED`. The pending plan remains visible with `recommendation: null` when no audit matches. A rejected or gateway-rejected recommendation record is never presented merely because it exists.

- [ ] **Step 4: Verify presentation and existing anti-bias tests**

```powershell
rtk proxy uv run python -m pytest tests/coordinator/test_proposal_review.py tests/coordinator/test_proposer_flow.py -q -o addopts='' -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
rtk git add src/sentinel/coordinator/proposal_review.py tests/coordinator/test_proposal_review.py
rtk git commit -m "feat(operator): add anti-anchoring proposal presentation"
```

---

### Task 5: Expose proposal review and measure anchoring signals

**Status:** Unblocked and required before the proposer is accepted.

**Files:**

- Modify: `src/sentinel/cli.py`
- Modify: `src/sentinel/coordinator/proposal_review.py`
- Test: `tests/test_cli_proposal_review.py`
- Test: `tests/coordinator/test_proposal_review.py`

**Interfaces — Consumes:** `sentinel pending`, current plan approval commands, `JsonlAuditSink`, and `CommandGateway.submit` as-is.

**Interfaces — Produces:** `pending --audit PATH` including a stable review token; `review-plan --plan-id ID --expected-state-version N --decision CONFIRM|REJECT --audit PATH`; a `plan_review` audit record containing `decision`, `proposed_label`, and `seconds_to_decision`.

- [ ] **Step 1: Write failing CLI and audit tests**

Assert all of the following:

- `pending --audit PATH` prints the actual proposed plan, its matching reason and cited facts, then the other plans in fixture order.
- `review-plan` requires an audit path plus the pending view's `plan_id` and `expected_state_version`; it submits `OPERATOR_DECIDE` as `actor_type=OPERATOR` through the injected gateway.
- The audit record contains `record`, `run_id`, `state_version`, `decision`, `actor_id`, `proposed_label`, and `seconds_to_decision`.
- A gateway success followed by audit-sink failure is recoverable: retry identifies the exact already-resolved decision, recovers the prior result, and writes/deduplicates the missing audit. The opposite decision cannot reuse that result.
- Malformed/truncated or non-object audit JSONL produces a controlled line-numbered warning; valid records remain usable and recovery may append a valid record without rewriting the append-only log.
- Audit-controlled text is rendered with terminal control characters escaped.
- Neither module imports or edits `CommandGateway` internals.

```python
assert sink.records[-1] == {
    "record": "plan_review",
    "run_id": RUN_ID,
    "state_version": pending_version,
    "decision": "CONFIRM",
    "actor_id": "OPERATOR_1",
    "proposed_label": "PLAN_ROUTE_ONLY_2_ZONE",
    "seconds_to_decision": 12.5,
}
```

- [ ] **Step 2: Run and verify failure**

```powershell
rtk proxy uv run python -m pytest tests/test_cli_proposal_review.py tests/coordinator/test_proposal_review.py -q -o addopts='' -p no:cacheprovider
```

Expected: FAIL because the flags and review wrapper do not exist.

- [ ] **Step 3: Add the read-only proposal display**

Add to the `pending` parser:

```python
pending.add_argument("--audit", type=Path)
```

When the run is waiting on a plan, load JSONL audit records, call `pending_proposal_review`, print `proposed_plan` with its recommendation first, then print `other_plans` in their provided order. Do not add an analysis-expansion flag.

- [ ] **Step 4: Add the operator review wrapper without changing the gateway**

Add this public function to `proposal_review.py`:

```python
def submit_plan_review(repository, gateway, run_id, plan_id, expected_state_version,
                       decision, *, sink, clock=time.time, actor_id="OPERATOR_1"):
    if decision not in {"CONFIRM", "REJECT"}:
        raise CoordinatorError("INVALID_OPERATOR_CHOICE", "decision must be CONFIRM or REJECT")
    snapshot = repository.read_state(run_id).to_dict()
    if snapshot["run_status"] != "WAITING_FOR_APPROVAL":
        raise CoordinatorError("INVALID_STATE_TRANSITION", "no plan approval is pending")
    plan_id = snapshot["pending_approval_id"]
    row = repository.connection.execute(
        "SELECT plan_sha256, catalogue_id, catalogue_revision, station_ids_json "
        "FROM plans WHERE run_id = ? AND plan_id = ?", (run_id, plan_id)
    ).fetchone()
    if row is None:
        raise CoordinatorError("INVALID_STATE_TRANSITION", "pending plan is missing")
    reference = {
        "plan_id": plan_id,
        "catalogue_id": row["catalogue_id"],
        "catalogue_revision": row["catalogue_revision"],
        "plan_sha256": row["plan_sha256"],
        "station_ids": json.loads(row["station_ids_json"]),
    }
    command = operator_decision_envelope(
        run_id, snapshot["state_version"], actor_id, decision, reference
    )
    result = gateway.submit(command)
    proposed_at = read_plan_proposed_time(repository, run_id, plan_id)
    sink.write({
        "record": "plan_review", "run_id": run_id,
        "state_version": snapshot["state_version"], "decision": decision,
        "actor_id": actor_id, "proposed_label": row["catalogue_id"],
        "seconds_to_decision": max(0.0, clock() - proposed_at),
    })
    return result
```

`operator_decision_envelope` must construct the already-valid `command_envelope` deterministically, with `command_type="OPERATOR_DECIDE"`, `actor_type="OPERATOR"`, and the existing plan-reference payload. It must call no mutation except `gateway.submit(command)`.

Because the append-only audit sink is outside the gateway transaction, `submit_plan_review` also implements explicit recovery semantics. The caller retains `(plan_id, expected_state_version)` from the pending view and supplies it on every attempt. Recovery uses that stable identity plus run, actor, and decision to find the exact committed operator decision, recover its gateway result, and idempotently complete the audit record—even if later plans have been reviewed. A retry with the opposite decision is rejected. JSONL scanning skips malformed/non-object rows with line-numbered warnings; invalid rows can never satisfy deduplication, and the log is never rewritten.

- [ ] **Step 5: Add `review-plan` and verify**

```powershell
rtk proxy uv run python -m pytest tests/test_cli_proposal_review.py tests/coordinator/test_proposal_review.py tests/coordinator/test_command_guardrails.py -q -o addopts='' -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
rtk git add src/sentinel/cli.py src/sentinel/coordinator/proposal_review.py tests/test_cli_proposal_review.py tests/coordinator/test_proposal_review.py
rtk git commit -m "feat(operator): expose proposal review without anchoring"
```

---

### Task 6: Measure proposer quality without applying acting-policy promotion criteria

**Status:** Implementation unblocked. Final held-out score is gated on **G-labels-v2** and **G-quality-threshold**.

**Files:**

- Create: `src/sentinel/proposal_eval.py`
- Create: `scripts/proposal_eval.py`
- Create: `scripts/proposal_sweep.py`
- Create: `tests/test_proposal_eval.py`
- Create: `tests/test_proposal_sweep.py`
- Create after gates: `artifacts/p3b1/proposal-quality.md`

**Interfaces — Consumes:** `proposal_recommendation` and `plan_review` audit records, fixture truth keyed by turn ID, run events, and P3a's `operator_touches`.

**Interfaces — Produces:** exact-match held-out acceptance, runtime approval acceptance, reason completeness, and per-run operator touches. It produces no promotion decision.

- [ ] **Step 1: Write failing pure-function tests**

```python
def test_proposal_quality_counts_exact_match_as_fixture_acceptance(): ...
def test_proposal_quality_counts_confirmed_and_rejected_runtime_proposals(): ...
def test_proposal_quality_requires_nonblank_reason_and_source_facts(): ...
def test_proposal_quality_reuses_operator_touches(): ...
def test_proposal_sweep_refuses_to_call_provider_without_exact_consent(): ...
```

Use the public result shape:

```python
{
    "fixture": {"proposed": 3, "accepted": 2, "acceptance_rate": 2 / 3},
    "runtime": {"reviewed": 2, "confirmed": 1, "acceptance_rate": 0.5},
    "reason_complete": 3,
    "operator_touches": {"approvals": 2, "choices": 0, "escalations": 0,
                         "total": 2, "approvals_only": True, "broken_by": []},
}
```

- [ ] **Step 2: Run and verify failure**

```powershell
rtk proxy uv run python -m pytest tests/test_proposal_eval.py -q -o addopts='' -p no:cacheprovider
```

Expected: FAIL because `sentinel.proposal_eval` does not exist.

- [ ] **Step 3: Implement pure scoring and an offline CLI**

The scorer must not import an LLM adapter. The CLI accepts existing JSONL records and fixture paths, then writes Markdown only when `--output` is supplied. It filters v2 to the 56 `SELECT_PLAN` turns and reports their `label_source` distribution.

`scripts/proposal_sweep.py` is the separate provider-facing driver. It loads only `SELECT_PLAN` turns, runs `LlmProposer` once per turn, and writes the full raw `proposal_recommendation` records before any aggregate. Its live branch must require `SENTINEL_PROPOSAL_SWEEP_CONSENT=I_ACCEPT_80_PAID_CALLS`; the exact value changes if the final fixture count changes.

- [ ] **Step 4: Verify the 56-turn existing-fixture report path**

```powershell
rtk proxy uv run python -m pytest tests/test_proposal_eval.py tests/test_operator_load.py -q -o addopts='' -p no:cacheprovider
rtk proxy uv run python -m pytest tests/test_proposal_sweep.py -q -o addopts='' -p no:cacheprovider
rtk uv run python scripts/proposal_eval.py --help
```

Expected: tests PASS; help exits 0; no provider call occurs.

- [ ] **Step 5: Stop at the held-out gates**

**G-labels-v2:** the operator supplies 24 request texts and labels. Do not generate them and do not add them to a fixture on the operator's behalf.

**G-quality-threshold:** before revealing held-out results, the operator records the minimum acceptable proposal acceptance rate. `operator_touches` acceptance is already fixed: a clean run must remain approvals-only with two touches.

**G-live-proposer:** after the held-out fixture exists, state the final call count (currently 56 existing plus 24 held-out = 80), model/revision, provider, and estimated cost. Stop for consent before setting the consent variable or invoking `scripts/proposal_sweep.py`.

- [ ] **Step 6: After both gates, add the new fixture without editing the old one**

Create a separately versioned held-out fixture and tests. Do not modify `fixtures/decisions_v2/turns.v2.jsonl`.

- [ ] **Step 7: Commit implementation, then gated evidence separately**

```powershell
rtk git add src/sentinel/proposal_eval.py scripts/proposal_eval.py scripts/proposal_sweep.py tests/test_proposal_eval.py tests/test_proposal_sweep.py
rtk git commit -m "feat(eval): measure approval-gated proposal quality"
```

After the two gates:

```powershell
rtk git add fixtures/decisions_p3b1 tests artifacts/p3b1/proposal-quality.md
rtk git commit -m "docs(eval): record held-out proposer quality"
```

---

### Task 7: Implement the remaining WP0 amendment

**Status:** **BLOCKED on G-amend-rest.** This is a schema/contract-fixture/requirement change. Stop here until the operator explicitly grants amendment sections 2-4, 6, and the corresponding event/requirement changes.

**Files:**

- Modify: `docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json`
- Modify: `src/sentinel/contracts/schemas.py`
- Modify: `src/sentinel/coordinator/agent_loop.py`
- Create: `src/sentinel/coordinator/reads.py`
- Create: `tests/contracts/test_amendment_decision_context.py`
- Create: `tests/coordinator/test_typed_reads.py`
- Modify after consent: `requirements/index.json`
- Create after consent: `requirements/SR-SENTINEL-V0-A04.md`

**Interfaces — Consumes:** the exact additive shapes in amendment §§3, 4, 6, and 7. Section 5 is already implemented and must not be reimplemented.

**Interfaces — Produces:** optional `AgentRequest.decision_context`; five read-only tools; `operator.ask`; bounded typed results; registered A04 requirement.

- [ ] **Step 1: Present G-amend-rest and stop**

The consent request must name:

- five tools: `state.read_plan`, `state.read_detection`, `state.read_task`, `state.read_rankings`, `state.read_evidence`;
- one operator-mediated tool: `operator.ask`;
- `legal_tools.maxItems` rising from 8 to 16;
- three events: `OPERATOR_QUERY_REQUESTED`, `OPERATOR_QUERY_ANSWERED`, `DECISION_RECORDED`;
- registration of SR-SENTINEL-V0-A04;
- no `CommandGateway` changes.

- [ ] **Step 2: After consent, write failing schema round-trip tests**

Tests must prove old documents remain valid, each new typed payload round-trips without loss, populated event lists validate, and a mutating tool is not in the investigator allowlist.

- [ ] **Step 3: Apply the approved schema diff exactly**

Use the definitions in the amendment document. Do not expand the enum or payloads beyond that consent. Update `AgentRequest` with an optional `decision_context: dict[str, Any] | None = None`, preserving old caller compatibility.

- [ ] **Step 4: Implement bounded typed reads**

Expose one dispatcher:

```python
READ_TOOL_NAMES = frozenset({
    "state.read_plan", "state.read_detection", "state.read_task",
    "state.read_rankings", "state.read_evidence",
})


class TypedReads:
    def __init__(self, repository) -> None:
        self.repository = repository

    def execute(self, tool_name: str, arguments: dict, run_id: str) -> dict:
        if tool_name not in READ_TOOL_NAMES:
            raise CoordinatorError("UNKNOWN_TOOL", f"unknown read tool: {tool_name}")
        # Dispatch to one private, id-bound query per approved amendment table.
        result = self._dispatch(tool_name, arguments, run_id)
        if len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()) > 2048:
            raise CoordinatorError("RESULT_TOO_LARGE", "typed read exceeds 2 KB")
        return result
```

Every query is run-scoped and either argument-free or accepts the single typed ID specified in the amendment. Unknown IDs return `REJECTED/NOT_FOUND`; no SQL fragment or free-text filter is accepted.

- [ ] **Step 5: Add decision context and read execution to `AgentLoop`**

Build `decision_context` from the same `DecisionState`/`LegalSet` already used by `bind_context`. Route only `READ_TOOL_NAMES` to `TypedReads`; keep every mutating tool on the existing execution path. Do not touch the gateway.

- [ ] **Step 6: Verify contract, coordinator, and full suites**

```powershell
rtk proxy uv run python -m pytest tests/contracts/test_amendment_decision_context.py tests/coordinator/test_typed_reads.py -q -o addopts='' -p no:cacheprovider
rtk proxy uv run python -m pytest tests/contracts tests/coordinator -q -o addopts='' -p no:cacheprovider
rtk proxy uv run python -m pytest -q -o addopts='' -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 7: Commit with the operator grant in the message body**

```powershell
rtk git add docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json src/sentinel/contracts/schemas.py src/sentinel/coordinator/agent_loop.py src/sentinel/coordinator/reads.py tests/contracts/test_amendment_decision_context.py tests/coordinator/test_typed_reads.py requirements/index.json requirements/SR-SENTINEL-V0-A04.md
rtk git commit -m "feat(wp0): add consented typed reads and operator ask"
```

---

### Task 8: Implement the bounded `EscalationInvestigator`

**Status:** **BLOCKED on Task 7/G-amend-rest.** It also requires **G-ui** before acceptance.

**Files:**

- Create: `src/sentinel/coordinator/investigator.py`
- Create: `tests/coordinator/test_investigator.py`
- Modify: `src/sentinel/coordinator/proposal_review.py`
- Test: `tests/coordinator/test_proposal_review.py`

**Interfaces — Consumes:** `AgentModelPort`, `TypedReads`, `decision_context`, current legal labels, an injected monotonic clock, and an audit sink.

**Interfaces — Produces:** exactly one `operator.ask`; at most four reads, six model turns, and 60 seconds; a templated `suggested=null` ask on any exhaustion path.

- [ ] **Step 1: Write failing boundary tests**

```python
def test_investigator_request_contains_only_typed_reads_and_operator_ask(): ...
def test_mutating_call_is_rejected_before_gateway_and_becomes_templated_ask(): ...
def test_fifth_read_becomes_templated_ask(): ...
def test_sixth_turn_or_sixty_seconds_becomes_templated_ask(): ...
def test_success_ends_in_exactly_one_operator_ask(): ...
def test_suggestion_must_be_one_of_fixed_order_legal_labels(): ...
```

Use this allowlist; it is the security boundary:

```python
INVESTIGATOR_TOOLS = (
    "state.read_plan", "state.read_detection", "state.read_task",
    "state.read_rankings", "state.read_evidence", "operator.ask",
)
```

- [ ] **Step 2: Implement a separate loop, not a gateway-capable policy**

```python
class EscalationInvestigator:
    def __init__(self, adapter, reads, sink, *, clock=time.monotonic,
                 max_reads=4, max_turns=6, timeout_s=60.0): ...

    def investigate(self, request, context):
        started = self.clock()
        read_count = 0
        previous = None
        for _ in range(self.max_turns):
            if self.clock() - started >= self.timeout_s:
                return self._templated_ask(request, context, "investigation timed out")
            scoped = self._request(request, context, previous, INVESTIGATOR_TOOLS)
            result = self.adapter.propose(scoped, self.reads)
            call = self._one_valid_call(result)
            if call.tool_name == "operator.ask":
                return self._validate_and_template_ask(call, request, context)
            if call.tool_name not in READ_TOOL_NAMES or read_count >= self.max_reads:
                return self._templated_ask(request, context, "read budget exhausted")
            previous = self.reads.execute(call.tool_name, call.arguments, request.run_id)
            read_count += 1
        return self._templated_ask(request, context, "turn budget exhausted")
```

`_validate_and_template_ask` discards model-supplied option ordering, rebuilds `options` from `context.legal.labels`, bounds the reason to 512 characters, and sets `suggested=None` unless the supplied label is legal.

- [ ] **Step 3: Reuse the Task 4 presentation rule**

Give investigator recommendations their own G-ui-reviewed operator view: fixed-order legal options, recommendation reason beside cited typed-read facts, and an explicit presentation record chosen at G-ui. Do not assume the proposer view's propose-then-confirm shape applies to `operator.ask`.

- [ ] **Step 4: Verify no gateway reachability**

Add a source-boundary test that `investigator.py` imports neither `CommandGateway` nor `command_for`, and an injected port test proving a mutating fake call never reaches `gateway.submit`.

- [ ] **Step 5: Run focused and full tests**

```powershell
rtk proxy uv run python -m pytest tests/coordinator/test_investigator.py tests/coordinator/test_proposal_review.py -q -o addopts='' -p no:cacheprovider
rtk proxy uv run python -m pytest -q -o addopts='' -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 6: Stop at G-ui**

Present collapsed and expanded CLI output to the operator. Acceptance requires confirmation that options remain fixed, analysis is opt-in, and reason plus cited facts are legible together.

- [ ] **Step 7: Commit after G-ui**

```powershell
rtk git add src/sentinel/coordinator/investigator.py src/sentinel/coordinator/proposal_review.py tests/coordinator/test_investigator.py tests/coordinator/test_proposal_review.py
rtk git commit -m "feat(coordinator): add bounded escalation investigator"
```

---

## Jev execution amendment (2026-09-20)

The old Tasks 9-11 shadow/re-banding path is dropped. Do not implement `proposal_fixture.py`, `reband_shadow.py`, `jev_batched_sweep.py`, a paid shadow batch, or a P3b1 Jev promotion write from the historical steps below.

Jev's current integration is owned by `docs/superpowers/specs/2026-09-20-survey-counting-design.md` and `docs/superpowers/plans/2026-09-20-survey-counting.md` in the survey worktree. There, mode `J` is a real proposer: code constructs complete legal candidates, Jev selects one, code derives the justification and returns the corresponding tool call, and the survey loop stops for operator approval before execution. Unknown labels, provider errors, low confidence, and thin margins fail closed to escalation. P3b1 must not duplicate or compete with that implementation.

### Task 9: DROPPED — historical acting-policy fixture

**Status:** **Superseded; do not execute.** Retained below only as historical context for the abandoned shadow/promotion design.

**Files:**

- Create: `scripts/build_acting_fixture.py`
- Create: `tests/test_build_acting_fixture.py`
- Create after gate: `fixtures/decisions_acting/turns.v1.jsonl`
- Create after gate: `fixtures/decisions_acting/turns.v1.schema.json`

**Interfaces — Consumes:** reachable distinct decision packets and operator-authored labels.

**Interfaces — Produces:** a versioned fixture whose per-point label distribution is reported and is not 75% `escalate`.

- [ ] **Step 1: Write a failing distribution test for the builder**

The builder must reject a batch when one label exceeds 60% at a decision point and must report distinct packet hashes, label counts, and source counts. It must never invent or relabel an operator answer.

- [ ] **Step 2: Implement validation-only tooling**

```python
def validate_balance(turns):
    by_point = collections.defaultdict(collections.Counter)
    for turn in turns:
        by_point[turn["decision_point"]][turn["label"]] += 1
    failures = {
        point: dict(counts) for point, counts in by_point.items()
        if max(counts.values()) / sum(counts.values()) > 0.60
    }
    if failures:
        raise ValueError(f"label imbalance exceeds 60%: {failures}")
    return by_point
```

- [ ] **Step 3: Verify offline tooling**

```powershell
rtk proxy uv run python -m pytest tests/test_build_acting_fixture.py -q -o addopts='' -p no:cacheprovider
```

Expected: PASS; no fixture is written without input labels.

- [ ] **Step 4: Stop at G-acting-labels**

The operator must approve/provide every new hand label. Do not treat generator text or deterministic-rule output as independent judgment truth.

- [ ] **Step 5: Commit tooling separately from gated data**

```powershell
rtk git add scripts/build_acting_fixture.py tests/test_build_acting_fixture.py
rtk git commit -m "feat(eval): validate balanced acting-policy fixtures"
```

---

### Task 10: DROPPED — historical Jev shadow re-sweep

**Status:** **Superseded; do not execute and do not request the paid-run gate.** Mode `J` is exercised as an approval-gated proposer in the survey package, not as a P3b1 shadow arm.

**Files:**

- Create: `scripts/reband_shadow.py`
- Create: `scripts/jev_batched_sweep.py`
- Create: `tests/test_reband_shadow.py`
- Create: `tests/test_jev_batched_sweep.py`
- Create after consent: `artifacts/decisions/p3b1-live-jev.jsonl`
- Create after consent: `artifacts/decisions/p3b1-live-jev.md`

**Interfaces — Consumes:** the Task 9 balanced fixture and raw rows containing arm, question name, `judge_label`, the full probability vector, confidence, resolved model, truth label, repeat, latency, and decision point.

**Interfaces — Produces:** one batched Jev call per turn/repeat containing three distinguishable shadow arms; offline confidence/margin/entropy gates; three-tier act/shortlist/escalate metrics without another provider call.

- [ ] **Step 1: Write threshold-boundary tests**

```python
def test_reband_uses_raw_judge_label_not_fallback_policy_label(): ...
def test_reband_reports_false_act_at_each_threshold(): ...
def test_reband_reports_high_count_wilson_stability_and_latency(): ...
def test_reband_rejects_aggregate_only_input(): ...
def test_reband_gates_on_confidence_margin_and_normalised_entropy(): ...
def test_reband_reports_act_shortlist_and_escalate_per_arm(): ...
def test_batch_has_three_select_plan_arms_but_evidence_gate_control_only(): ...
def test_batch_records_full_probabilities_and_resolved_model_per_row(): ...
def test_batch_fails_when_resolved_model_is_not_the_pinned_revision(): ...
```

- [ ] **Step 2: Define the three shadow arms and one-call batching**

For each `SELECT_PLAN` turn, one provider request contains:

- **Arm A — control:** the existing composite choice over `{candidate..., escalate}`.
- **Arm B — decomposed:** `side` choice over `{left,right,both,unclear}`, `depth` choice over `{front,middle,back,all,unclear}`, `markers_required` Noul, and `answerable` Noul. Coordinator-owned code combines these answers into a plan ID for scoring only; unclear/low-gate components derive escalation in code.
- **Arm C — no free abstention:** the composite candidate choice without `escalate`; code derives abstention from the gates in Step 3.

`EVIDENCE_GATE` retains Arm A only as a drift measurement because its action is deterministic. No arm submits a command or acts. Each raw row records `arm: A|B|C`; Arm B also records `question_name`. The driver batches every applicable question for a turn into one call rather than one call per arm/question.

- [ ] **Step 3: Pin and verify the answering model before preserving a row**

Require the provider-resolved response model to equal `typesafe/jev-1.13-20260917`. Write that resolved identifier into every raw row and fail the batch on a mismatch or on the known-invalid value `static`. Preserve the full per-option probability vector, not only scalar confidence. Add an offline fake-response test proving both requirements before any consent request.

- [ ] **Step 4: Implement offline threshold enumeration**

Per decision point and arm, enumerate:

- confidence thresholds `0.00, 0.05, ..., 1.00` plus every observed value;
- margin `P(top-1) - P(top-2)` thresholds `0.00, 0.05, ..., 0.50`;
- normalised entropy ceilings `0.50, 0.55, ..., 1.00`;
- the conjunction of all three.

Never infer the judge's pick from `JevPolicy`'s fallback label. For each setting report act/shortlist-to-LLM/escalate counts, HIGH count, coverage, selective accuracy, `false_act`, Wilson lower 95%, repeat flips, and median latency. Keep arms separate in raw data and reports.

- [ ] **Step 5: Verify against synthetic raw rows and batched fake responses**

```powershell
rtk proxy uv run python -m pytest tests/test_reband_shadow.py tests/test_jev_batched_sweep.py tests/test_shadow_eval.py tests/test_shadow_sweep_records.py -q -o addopts='' -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 6: Commit the offline tools**

```powershell
rtk git add scripts/reband_shadow.py scripts/jev_batched_sweep.py tests/test_reband_shadow.py tests/test_jev_batched_sweep.py
rtk git commit -m "feat(eval): compare batched Jev question shapes offline"
```

- [ ] **Step 7: Stop at G-live-jev-re-sweep**

Before the operator decides, report turns x repeats and make explicit that total calls equal that product—not arms x questions—because each turn is batched into one call. State the pinned model `typesafe/jev-1.13-20260917`, provider, and estimated token cost, plainly labelled as an estimate. The command must require the existing explicit consent environment variable. Do not run the LLM arm unless separately requested.

- [ ] **Step 8: After consent, run once and preserve raw rows**

Use the balanced fixture from Task 9. Commit raw JSONL and the rendered report together so future thresholds never require repaying for the batch.

---

### Task 11: DROPPED — historical autonomous-promotion analysis

**Status:** **Superseded; do not execute.** Jev's survey tool-call proposals remain operator-gated and therefore make no autonomous-promotion claim.

**Files:**

- Create after evidence exists: `artifacts/decisions/p3b1-promotion-assessment.md`
- Modify only after sign-off: `promotions.json`
- Test only if config changes: `tests/coordinator/test_promotions.py`

**Interfaces — Consumes:** balanced raw-row report and the pre-registered acting-policy criteria.

**Interfaces — Produces:** an evidence-backed “promote” or “do not promote” decision for each eligible point.

- [ ] **Step 1: State eligibility before looking at scores**

The report must state:

- `EVIDENCE_GATE`'s ordinary create/escalate choice is deterministic and is not eligible for model promotion.
- `SELECT_PLAN` is served by the approval-gated proposer in P3b1; its quality is evaluated by Task 6, not acting-policy promotion.
- Duplicate-detection judgment has only 8 distinct coarse packets today.
- Near-tie assignment has only 6 distinct coarse packets today.
- Neither information-thin point can support a distinct-state study with `n >= 60` under the current packet (blocker A12).
- Pose quality under approved post-P3 proposal P-9 is the first information-rich future point: continuous reprojection error, tag geometry, and staleness. P-7 and P-9 require one combined revisit design after WP5 Test B.
- Jev's 1/144 acting-band coverage was measured once with a composite act-or-escalate question, abstention offered as a candidate, one unvalidated 0.80 threshold, and a model revision that could not later be confirmed. Until Task 10 Arms B and C are read, that result is not proof that Jev itself cannot reach an acting band.

- [ ] **Step 2: Apply all acting criteria without weakening them**

For an eligible future point, require `n >= 60` HIGH picks across at least two seeds/days, `false_act == 0`, Wilson lower 95% at least 0.93, no more than one flip per 20 repeats, median latency at most 1500 ms, and operator sign-off.

Deriving `act_at`, margin, or entropy gates from raw data does not waive any of those outcome criteria. It only replaces unvalidated defaults.

- [ ] **Step 3: Stop at G-promote**

Do not create or modify `promotions.json` unless the operator signs off on a named point, model, threshold, and report. Deleting an entry remains the rollback.

---

### Task 12: Full verification and handoff

**Status:** Runs after the last implemented, ungated task; repeat after each later gate opens.

**Files:**

- Create: `docs/handoffs/2026-09-21-p3b1-approval-gated-E.md`

**Interfaces — Consumes:** all completed task commits and gate decisions.

**Interfaces — Produces:** reproducible verification evidence and an explicit list of remaining gates.

- [ ] **Step 1: Run formatting-free static checks used by the repository**

Run the repository's existing checks only; do not introduce a formatter or rewrite unrelated files.

- [ ] **Step 2: Run the full suite**

```powershell
rtk proxy uv run python -m pytest -q -o addopts='' -p no:cacheprovider
```

Expected: all collected tests pass, with only the repository's documented skips.

- [ ] **Step 3: Review the diff and forbidden boundary**

```powershell
rtk git diff master...HEAD --stat
rtk git diff master...HEAD -- src/sentinel/coordinator/command_gateway.py
rtk git status --short
```

Expected: no gateway diff; only task-scoped files; no unrelated user file included.

- [ ] **Step 4: Write the handoff**

Record:

- proposer test and held-out measurement status;
- presentation rule evidence;
- operator touches for clean runs;
- whether G-amend-rest, G-labels-v2, G-quality-threshold, G-live-proposer, G-ui, G-acting-labels, G-live-jev-re-sweep, and G-promote are open or granted;
- exact commits and test output;
- explicit statement that no provider call, merge, or push occurred unless separately consented.

- [ ] **Step 5: Commit the handoff**

```powershell
rtk git add docs/handoffs/2026-09-21-p3b1-approval-gated-E.md
rtk git commit -m "docs(p3b1): hand off approval-gated architecture E"
```

---

## Why Acting-Policy Promotion Does Not Gate Tasks 1-8

The promotion criteria bound the cost of a wrong **autonomous action**. `LlmProposer` cannot approve its proposed plan; the existing coordinator moves the run to `WAITING_FOR_APPROVAL`. `EscalationInvestigator` has no mutating tool and ends in `operator.ask`. A wrong output can anchor or burden the operator, but it cannot cross the operator gate by itself.

That changes the acceptance evidence, not the need for evidence. The proposer must pass the hard presentation rule and report proposal acceptance plus operator touches. The investigator must prove its legal-tool boundary, budgets, and presentation behavior. Neither is judged by HIGH-band coverage, `false_act`, or acting-policy latency.

## What Architecture E Will Not Be in P3b1

P3b1 will not replace deterministic `EVIDENCE_GATE` logic with a model. It will not claim that repeated copies of 8 duplicate-detection packets or 6 near-tie packets constitute 60 distinct decisions. Jev's active proposer role is implemented in the sibling survey package: it may return a pre-built legal tool call, but that proposal still parks for operator approval and makes no autonomous-promotion claim.

The first plausible information-rich autonomous gate is pose-quality recovery under P-9, designed jointly with P-7 after WP5 Test B. Until that surface exists and passes the complete promotion criteria, architecture E means: the LLM proposes and investigates, the operator decides, and code executes.

## Task and Gate Summary

| Tasks | State today | Gate |
|---|---|---|
| 0-5 | Unblocked | No merge/push without later consent |
| 6 implementation | Unblocked | Held-out fixture: G-labels-v2; threshold: G-quality-threshold; batch: G-live-proposer |
| 7 | Blocked | G-amend-rest |
| 8 | Blocked | Task 7, then G-ui |
| 9 historical fixture tooling | Dropped; superseded by survey mode `J` | None; do not request G-acting-labels for P3b1 |
| 10 historical three-arm shadow tooling | Dropped; superseded by survey mode `J` | None; do not request G-live-jev-re-sweep |
| 11 historical promotion analysis | Dropped; mode `J` remains approval-gated | None; no P3b1 Jev promotion write |
| 12 | Unblocked for completed slice | Merge/push remains a separate operator decision |

There are **13 numbered task slots (0-12)**, but historical Tasks 9-11 are superseded and must not execute. The active P3b1 slice is Tasks 0-6; Tasks 7-8 remain behind their stated gates, and Task 12 closes the completed slice. Jev mode `J` proceeds only in the separately owned survey plan.
