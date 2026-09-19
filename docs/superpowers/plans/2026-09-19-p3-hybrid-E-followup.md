# P3 — Architecture E follow-up: LLM proposes, Jev gates, code executes (after the freeze)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. **Do not start before 2026-09-26.** Re-validate this plan first (Task 0), because its scope depends on the outcomes of G-amend (P1 Task 6), G-live-B (P1 Task 7) and the P2 shadow data.

**Goal:** Move from F to E only where the data justifies it:
- **Jev** may act at the evidence gate once promoted by statistical criteria.
- **The LLM** proposes plans from free-text requests and investigates escalations with typed reads. Its output always goes through operator approval.
- **Jev as a policy over enumerated reads and acts** ("Jev agent") is measured as an experiment.

**Architecture:** First, implement the approved WP0 amendment: `decision_context`, typed read tools, result ids, `operator.ask` and `DECISION_RECORDED`. Then add three policies on P1's policy seam:
- `PromotedPolicy`: per decision point, the executor is Jev if promoted, else the operator.
- `LlmProposer`: plan interpretation. It emits `inspection.propose_plan`, which is approval-gated.
- `EscalationInvestigator`: an LLM with typed reads and a bounded history. It ends in `operator.ask` with a recommendation and cannot execute anything else.

**Tech Stack:** as in P1 and P2.

## Entry criteria (all required)

1. P1 and P2 are merged.
2. **G-amend granted.** Tasks 1, 4, 5 and 6 need the amended schema. If it is refused, only Tasks 2–3 can run, and escalation keeps the P2 CLI path.
3. The P2 shadow data exists: the live sweep plus the rehearsals.

## Promotion criteria (pre-registered here, before any P3 data is read)

A policy is promoted to **act** at a decision point only if all six hold, computed by `shadow_eval.score` over the P2 shadow data plus a fresh consented sweep:

1. n ≥ 60 HIGH-band picks at that decision point, across ≥ 2 generator seeds or rehearsal days.
2. `false_act == 0`.
3. Wilson lower 95% bound of selective accuracy ≥ 0.93. (60/60 gives 0.940; one error in 60 fails.)
4. `stability`: at most 1 flip per 20 repeated turns.
5. Median latency ≤ 1500 ms for acting policies. This bars the LLM from acting at the evidence gate by design.
6. The operator signs off on the report. This is gate G-promote.

A promotion is recorded as a config entry, `promotions.json: {decision_point: {"policy": "jev", "model": "...", "report": "path", "approved_by": "...", "date": "..."}}`. It is reverted by deleting the entry, and the system then falls back to the operator automatically.

## Operator gates

| Gate | Task | Decision |
|---|---|---|
| G-replan | 0 | confirm the re-validated scope |
| G-promote | 3 | sign off on each decision point promoted |
| G-live-E | 4, 5, 6 | consent to each live evaluation batch (count and cost stated) |
| G-ui | 5 | review the recommendation presentation (the automation-bias rule) |

---

### Task 0: Re-validate this plan (parent + operator)

- [ ] Read the P1 Task 6 outcome, the P1 Task 7 live result (`artifacts/wp1/live-gate-real-loop.md`) and the P2 shadow reports. Update the Tasks 1–6 below, removing any whose entry criterion failed, and commit.
- [ ] **G-replan:** present the updated scope to the operator.

### Task 1: Implement the approved WP0 amendment

**Files:**
- Modify: `docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json`, `src/sentinel/contracts/schemas.py`, `src/sentinel/coordinator/agent_loop.py`
- Create: `src/sentinel/coordinator/reads.py`
- Test: `tests/contracts/test_amendment_decision_context.py`, `tests/coordinator/test_typed_reads.py`

**Scope:** exactly the schema diff approved in `docs/superpowers/specs/2026-09-19_wp0-amendment-decision-context-and-reads.md`, plus the `tool_policy_revision` bump. `reads.py` implements `state.read_plan`, `state.read_detection`, `state.read_task`, `state.read_rankings` and `state.read_evidence` over the repository. Each returns the typed payload defined in the amendment and is bounded to 2 KB of JSON.

**Tests:**
- Every new wire type round-trips through its dataclass wrapper and validates.
- Every read returns its typed payload on the harness flow.
- Mutation results carry ids (`{"kind": "task", "task_id": ...}`).
- `operator.ask` expiry produces `finish(NEEDS_REVIEW)`.
- `DECISION_RECORDED` events are appended by `ShadowLadder` in place of, or alongside, the JSONL sink.
- The P1 and P2 suites stay green.

- [ ] Steps: TDD (tests, FAIL, implement, PASS, full suite), then commit with the message `feat(wp0): decision_context, typed reads, result ids, operator.ask, DECISION_RECORDED`.

### Task 2: Promotion config and `PromotedPolicy`

**Files:**
- Create: `src/sentinel/coordinator/promotion.py`, `config/promotions.json` (initially `{}`)
- Test: `tests/coordinator/test_promotion.py`

**Interfaces:**
- `load_promotions(path) -> dict[str, dict]`
- `PromotedPolicy(promotions, policies: Mapping[str, ContextualPolicy], fallback=RulePolicy())`. At a judgment point it delegates to `policies[promotions[point]["policy"]]` if an entry exists, and to the fallback (operator) otherwise.
- It stays wrapped in `ShadowLadder`, so the non-promoted advisors keep being measured.

**Tests:**
- An empty config behaves exactly like `RulePolicy`, with an identical event log.
- A promoted Jev acts on HIGH and escalates on MID/LOW.
- A malformed config (unknown policy, missing report path) raises at load time, never at runtime.
- Removing the entry reverts behaviour with no code change.

- [ ] Steps: TDD, then commit with the message `feat(coordinator): data-gated promotion config with automatic operator fallback`.

### Task 3: Promotion report and G-promote

**Files:**
- Create: `scripts/promotion_report.py`, `artifacts/decisions/promotion-report.md`

The script applies the six criteria above per (policy, decision point) and prints PASS/FAIL per criterion with the numbers. It never edits `promotions.json`.

- [ ] Steps: test the criteria logic on synthetic score dicts (the boundary cases 60/60 PASS, 59/60 FAIL, n = 59 FAIL, one false act FAIL). Implement, commit, then generate the report.
- [ ] **G-promote:** present the report. On sign-off, the parent writes `promotions.json` and commits it with the operator's words in the message.

### Task 4: `LlmProposer` for plan interpretation

**Files:**
- Create: `src/sentinel/coordinator/proposer.py`
- Test: `tests/coordinator/test_proposer.py`

**Interfaces:** `LlmProposer(adapter)` implements `ContextualPolicy` at `SELECT_PLAN` only; elsewhere it escalates. It emits `inspection.propose_plan` or `escalate` using the described candidates and the operator request, with a required `reason` argument. Its output is a **proposal**: the gateway puts the run into `WAITING_FOR_APPROVAL`, and the operator confirms or rejects.

**Evaluation:**
- The P2 fixture `SELECT_PLAN` turns, plus 24 new held-out request texts written by the operator (not by an agent). This avoids overfitting to the generator's phrasing.
- Promotion uses the same criteria, except criterion 5: latency does not apply to approval-gated proposals.
- Additional criterion: the proposal's `reason` must cite the request constraint it satisfies. A fresh reviewer spot-checks 20 reasons.

- [ ] Steps: TDD with a fake adapter. Test that the proposer never calls a tool outside `{inspection.propose_plan, finish}`, and that a rejected proposal returns the run to `SELECT_PLAN`. Commit.
- [ ] **G-live-E:** a consented evaluation batch.

### Task 5: `EscalationInvestigator` (progressive state building, where it pays)

**Files:**
- Create: `src/sentinel/coordinator/investigator.py`
- Modify: `src/sentinel/chat.py`, to show the investigator's `operator.ask` recommendation only in the form specified below
- Test: `tests/coordinator/test_investigator.py`, `tests/test_recommendation_presentation.py`

**Behaviour:**
1. It is triggered only when the executor escalates at a judgment point (MID/LOW band, an advisor error, or a gateway rejection).
2. The LLM gets the `decision_context` plus typed read tools (Task 1), and a bounded history of ≤ 4 reads.
3. It must end with **exactly one** `operator.ask`, whose `options` are the legal labels, with `suggested` and a `reason` ≤ 512.
4. It cannot call any mutating tool: its `legal_tools` are the reads plus `operator.ask` only, enforced in the request, not merely in the prompt.
5. Its budget is 6 turns and 60 s; on expiry the coordinator sends the templated `operator.ask` with `suggested = null`.

**Presentation rule (tested, then reviewed at G-ui):**
- The operator sees the options in fixed catalogue order, never reordered by the suggestion.
- The suggestion appears only after the operator expands "show assistant analysis".
- The reason is shown with the facts it cites.
- The operator's choice is recorded together with whether the analysis had been expanded, so that the anchoring effect can be measured later.

- [ ] Steps: TDD with a fake adapter. Tests:
  - A mutating tool call is rejected by `legal_tools` before it reaches the gateway.
  - Budget expiry produces the templated ask.
  - The presentation payload keeps the option order and hides `suggested` behind a flag.
  - `expanded` is recorded.

  Commit.
- [ ] **G-live-E** consent and **G-ui** review.

### Task 6: Jev agent experiment (Jev as a policy over enumerated reads and acts)

**Files:**
- Create: `src/sentinel/coordinator/jev_agent.py`, `artifacts/decisions/jev-agent-experiment.md`
- Test: `tests/coordinator/test_jev_agent.py`

**Question answered:** can Jev, which has no tools, memory or argument generation of its own, act as an agent when the loop supplies all three? At each step:
1. The options are the typed reads that have not yet been done, plus the legal acts, plus `escalate`, all with fixed arguments.
2. The loop accumulates the read results into the packet it sends next.
3. Jev picks one option.

The step cap is 4 reads.

**Comparison:** on the same fixture turns, compare (a) the one-shot `JevPolicy` on the packet and (b) the `JevAgent` with reads, using the same metrics plus reads per decision. **Hypothesis to falsify:** reads do not improve selective accuracy on V0 states, because the packet already holds every decision-relevant fact. The experiment report states this outcome either way.

- [ ] Steps: TDD with a fake Jev (option enumeration, accumulation, the read cap, and that the act options are exactly the legal candidates). Commit.
- [ ] **G-live-E:** a consented batch.

## Self-review

- **E coverage:** the LLM proposes (T4) and investigates (T5); Jev gates, promoted by data (T2–T3); code executes (unchanged gateway); the Jev agent question the operator raised (T6).
- **Safety:**
  - Promotion is reversible config with an automatic operator fallback.
  - The investigator's inability to mutate is enforced in `legal_tools`, not in the prompt.
  - The recommendation presentation is designed against anchoring and measured.
- **Honesty:** entry criteria and Task 0 exist because the scope depends on three unknowns: the amendment consent, B's live result and the shadow data.
