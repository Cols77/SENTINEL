# P2 — Architecture F: Rules + Operator with Shadow Advisors (demo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. In Claude Code this plan is executed by the `Workflow` tool: each task runs dev → fresh review → fix, followed by parent verification.

**Goal:** Ship the demo decision path for the 2026-09-25 freeze.
- Rules execute everything that is deterministic.
- The operator chooses at the two judgment points (`SELECT_PLAN`, `EVIDENCE_GATE`).
- Jev and the LLM answer the same questions **in shadow**: they are recorded, never shown before the operator decides, and never acted on. Every run therefore produces evaluation data on real states.
- The LLM is the read-only operator chat.

**Architecture:** `ShadowLadder` wraps P1's `RulePolicy` as the executor and calls a list of advisor policies at judgment points only. The advisors are `JevPolicy(JevJudge)` and `JevPolicy(LlmJudge)`: the same chooser code with a different judge, so both see the identical packet, instruction and described options. Advisor failures and latency cannot affect execution. `operator_choice.submit_choice()` executes the operator's pick among legal candidates through the gateway as an `OPERATOR` actor. An append-only JSONL audit records the executor's decision, each advisor's decision and the operator's choice for every judgment point.

**Tech Stack:** Python 3.12+, pytest, `uv`, P1 modules (`decision_state`, `legal_tools`, `policy`, `policies`), `sentinel.judgment` (`JevJudge`, `LlmJudge`).

**Specs:** P1 `docs/superpowers/plans/2026-09-19-p1-agent-loop-foundations.md` (must be complete) and the specs copied by P1 Task 0.

## Global Constraints

- Requires P1 Tasks 0–5 to be merged on `feat/wp1-gate-fixes`. P1 Task 6 and Task 7 are not required.
- Advisors never change what is executed. A test must prove it (Task 2).
- No advisor pick or confidence may be shown to the operator, whether in the UI, the chat or the CLI output, **before** the operator has chosen at that decision point. This is the anti-automation-bias rule.
- No live calls except Task 5 Step 4 and Task 6, each with fresh consent (count and cost stated).
- Every mutation goes through `CommandGateway.submit`. The static veto is unchanged.
- Test command: `uv run python -m pytest tests/ -q -o addopts=''`, expecting 0 failures.

## Operator gates

| Gate | Task | Decision |
|---|---|---|
| G-labels | 1 | approve hand labels for the `SELECT_PLAN` and `EVIDENCE_GATE` turns |
| G-sweep | 5 | consent to the live shadow sweep of both advisors over the turn fixture |
| G-rehearsal | 6 | consent to live advisors during demo rehearsals |
| G-merge | final | local merge, then push |

## Schedule to the freeze

| Day | Work |
|---|---|
| Mon 09-21 | Task 1 → G-labels |
| Tue 09-22 | Tasks 2–4 |
| Wed 09-23 | Task 5 → G-sweep |
| Thu 09-24 | Task 6 rehearsals, final review, G-merge |
| Fri 09-25 | Freeze |

**Cut line if late:** Task 5's live step and Task 6 are optional. Tasks 1–4 alone give a working, deterministic demo with the shadow hooks switched off.

## Decisions recorded 2026-09-20

The operator reviewed the Task 1 label table (gate `G-labels`) and recorded the
following, quoted verbatim (also recorded in `fixtures/decisions/README.md`):

1. EVIDENCE_GATE: "Make it a rule, keep shadow" — the create_task/escalate
   policy is computable, so a deterministic rule decides it; advisors are
   still consulted in shadow at that point and recorded.
2. Score cut: "Escalate below 0.75" — a detection becomes a task only when
   it is inside the approved plan AND its score is >= 0.75; otherwise
   escalate.
3. Single-station requests: the operator observed "Seems like
   PLAN_ROUTE_ONLY_2_ZONE does not make sense if you're visiting one zone?"
   and chose "Escalate, and propose a catalogue entry" — `sp-001`..`sp-006`
   are labelled `escalate` because no catalogue plan covers exactly one
   station; a WP0 proposal for a 1-zone catalogue entry is drafted
   separately (Task C).
4. Catalogue fixture: "Proposal only, you consent" —
   `docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json`
   MUST NOT be edited by any task here.
5. "All other draft labels in fixtures/decisions/turns.v1.jsonl are APPROVED
   as drafted (gate G-labels), subject to the relabelling in decisions 2 and
   3."

**Shipped consequences (Tasks 1–3 below are updated to match):**
- Decision 1 is `legal_tools.legal_set()`'s `LegalSet.rule_choice`: at
  `EVIDENCE_GATE` a deterministic rule picks `create_task`/`escalate`, not
  the operator. `ADVISORY_POINTS` (`SELECT_PLAN`, `EVIDENCE_GATE` — where
  `ShadowLadder` still calls and records advisors) is now a strict superset
  of `JUDGMENT_POINTS` (`SELECT_PLAN` only — the single point
  `operator_choice.pending_decision`/`submit_choice` surface to the
  operator).
- Decision 2 is `legal_tools.EVIDENCE_TASK_SCORE = 0.75`.
- Decision 3 relabelled `sp-001`..`sp-006` to `escalate` in the committed
  fixture (`turn_generator.py` updated, fixture regenerated with
  `SENTINEL_REGEN_TURNS=1`, existing `turn_id`-matched labels for every
  other turn preserved).
- Because decisions 1 and 2 together make the fixture's `EVIDENCE_GATE` hand
  labels mirror the deterministic rule exactly, Task 5/6 scoring at
  `EVIDENCE_GATE` measures **rule agreement**, not independent judgment;
  `SELECT_PLAN` remains the judgment benchmark.

---

### Task 1: Representative decision-turn fixture

**Files:**
- Create: `tests/coordinator/turn_generator.py`, `fixtures/decisions/turns.v1.jsonl`, `fixtures/decisions/turns.v1.schema.json`, `fixtures/decisions/README.md`
- Test: `tests/coordinator/test_turn_fixture.py`

**Interfaces:**
- Consumes: `tests/coordinator/harness.py` (`open_harness`, `envelope`, `FakeWorker`, `perception_result`), `read_decision_state`, `legal_set`, `load_catalogue_plans`.
- Produces: `generate_turns() -> list[dict]` and the committed fixture. Record shape:

```json
{"turn_id": "sp-001", "decision_point": "SELECT_PLAN",
 "packet": {"context_version": "decision_packet.v1", "...": "DecisionState.to_packet()"},
 "candidates": [{"label": "PLAN_ROUTE_ONLY_2_ZONE", "description": "mode=ROUTE_ONLY_2_ZONE; stations=2 (...)"}],
 "label": "PLAN_ROUTE_ONLY_2_ZONE", "label_source": "hand",
 "rationale": "one station requested; route-only covers it without marker verification",
 "generator_revision": "turns.v1"}
```

**Variants.** Every turn is produced by driving the real gateway, never hand-written state.
- `SELECT_PLAN`, 24 request texts:
  - 6 explicit single-station ("inspect only the left alley middle station")
  - 6 full sweep or marker-verified ("full verified survey of all marked stations")
  - 4 ambiguous ("have a look around the alley")
  - 4 out of catalogue ("inspect the roof drains")
  - 4 contradictory ("one station only, but verify all six markers")
- `EVIDENCE_GATE`, 24 perception variants, all `DETECTED` and ≥ threshold:
  - `raw_score` ∈ {0.51, 0.55, 0.62, 0.78, 0.9, 0.99} × {station inside the plan, outside the plan}
  - × {no open task, duplicate of an open task at the same station}
  - Use `canonical_class="rock"` unless the class mapping in `docs/superpowers/specs/contracts/` lists more classes. If it does, add one variant per extra class.
- RULE points: one turn each (`WAIT_APPROVAL`, `WAIT_OBSERVATION`, `EVIDENCE_VETO`, `RANK`, `ASSIGN`), labelled automatically, `label_source: "rule"`.
- **Draft labels** (as first generated; see "Decisions recorded 2026-09-20" above for the operator's approval and relabelling that supersedes the two items marked below):
  - Explicit single-station → `PLAN_ROUTE_ONLY_2_ZONE` — **superseded (decision 3): → `escalate`.** No catalogue plan covers exactly one station, so `sp-001`..`sp-006` were relabelled `escalate` and a 1-zone catalogue entry proposed separately (Task C).
  - Full sweep or verified → `PLAN_MARKER_VERIFIED_6`
  - Ambiguous, out of catalogue or contradictory → `escalate`
  - Evidence inside the plan, no duplicate, score ≥ 0.62 → `create_task` — **superseded (decision 2): the cut is ≥ 0.75**, matching `legal_tools.EVIDENCE_TASK_SCORE`; this also flipped `eg-009`/`eg-010` (score 0.62, inside the plan) from `create_task` to `escalate`.
  - Outside the plan, or a duplicate → `escalate`
  - Score 0.51 or 0.55 and inside the plan → `escalate` (marginal band)

  Every hand label carries a one-sentence rationale. Every other draft label
  stands as drafted (decision 5) — approved, not superseded.
- README: state the known gaps, which are no pose or staleness facts (WP5 pending) and no weather source.

- [ ] **Step 1: Write the failing tests.**
  - Every line validates against the schema.
  - `label` ∈ the candidate labels.
  - RULE turns have exactly one candidate.
  - Every `label_source == "hand"` turn has a non-empty rationale.
  - There are ≥ 20 turns each for `SELECT_PLAN` and `EVIDENCE_GATE`.
  - No UUID appears in any `packet`.
  - Golden test: `generate_turns()` reproduces the committed `packet` and `candidates` for every `turn_id`. Labels are matched by `turn_id`, so relabelling does not break the golden test. Set `SENTINEL_REGEN_TURNS=1` to rewrite the file, keeping existing labels.
- [ ] **Step 2:** FAIL. **Step 3:** Implement the generator and write the draft labels. **Step 4:** PASS, plus the full suite.
- [ ] **Step 5:** Commit with the message `feat(fixtures): decision turns generated from the real gateway (draft labels)`.
- [x] **Step 6, gate G-labels (parent): DONE, approved 2026-09-20.** The operator was shown a table of `turn_id | request or variant | draft label | rationale` for the 48 judgment turns and approved all draft labels as drafted, subject to the relabelling recorded verbatim under "Decisions recorded 2026-09-20" above (decisions 2 and 3) and in `fixtures/decisions/README.md`. The fixture was regenerated (`SENTINEL_REGEN_TURNS=1`) with the relabelling applied and committed. Tasks 5–6 score only against these approved labels.

### Task 2: `ShadowLadder` — executor plus shadow advisors

**Files:**
- Create: `src/sentinel/coordinator/shadow.py`
- Test: `tests/coordinator/test_shadow.py`

**Interfaces:**
- Consumes: `ContextualPolicy`, `PolicyContext`, `bind_context`, `RulePolicy`, `JevPolicy`, `Decision`, `JUDGMENT_POINTS`.
- Produces:
  - `AuditSink(Protocol)`: `write(record: dict) -> None`
  - `JsonlAuditSink(path: Path)`
  - `MemoryAuditSink()`, with a `.records` attribute
  - `ShadowLadder(executor: ContextualPolicy, advisors: Sequence[tuple[str, ContextualPolicy]], sink: AuditSink, *, advisor_timeout_s: float = 5.0)`, which implements `ContextualPolicy`

**Behaviour** (shipped 2026-09-20: `JUDGMENT_POINTS` split from `ADVISORY_POINTS` — see "Decisions recorded 2026-09-20" above, decision 1):
1. The executor's `decide()` result is always what is returned.
2. At a decision point in `ADVISORY_POINTS` (`SELECT_PLAN` and `EVIDENCE_GATE` — a strict superset of `JUDGMENT_POINTS`, since `EVIDENCE_GATE` is now decided by a deterministic rule but is still shadowed), each advisor is called on the same `PolicyContext`, in a thread with a timeout. Its `.last` `Decision` is recorded. Any exception or timeout is recorded as `error` and swallowed.
3. Advisors are **not** called at RULE points.
4. Every advisory point writes one audit record, **before** the executor's result is returned, additionally carrying `rule_choice` (the label the deterministic rule would pick at this point, or `null` where none computes one, e.g. `SELECT_PLAN`):

```json
{"record": "judgment", "run_id": "...", "state_version": 7, "decision_point": "EVIDENCE_GATE",
 "packet_sha256": "...", "legal_labels": ["create_task", "escalate"], "rule_choice": "create_task",
 "executor": {"chooser": "RULE", "label": "create_task"},
 "advisors": {"jev": {"label": "create_task", "confidence": 0.91, "band": "HIGH", "model": "typesafe/jev-1.13-20260917", "latency_ms": 350, "error": null},
              "llm": {"label": "create_task", "confidence": 0.85, "band": "HIGH", "model": "z-ai/glm-5.3-flash", "latency_ms": 14000, "error": null}},
 "operator_choice": null}
```

(At `SELECT_PLAN`, which has no `rule_choice`, the executor escalates with `chooser: "OPERATOR"` when there is more than one candidate, exactly as before.)

5. RULE points write `{"record": "rule", "run_id", "state_version", "decision_point", "label"}`.

- [ ] **Step 1: Write the failing tests:**
  - (a) **Non-interference.** Run the one-station flow three times with advisors that respectively always pick the opposite label, raise `RuntimeError`, and sleep past the timeout. The event lists and returned results are identical to a run with no advisors.
  - (b) Advisors are called only at `SELECT_PLAN` and `EVIDENCE_GATE` (the `ADVISORY_POINTS`): the call count equals the number of advisory points, not the (smaller) number of `JUDGMENT_POINTS`.
  - (c) The audit has one `judgment` record per judgment point with both advisor entries, and `rule` records elsewhere.
  - (d) `packet_sha256` equals the sha256 of the canonical JSON of `to_packet()`.
  - (e) `JsonlAuditSink` appends valid JSON lines and never truncates.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, plus the full suite.
- [ ] **Step 5:** Commit with the message `feat(coordinator): shadow ladder — rules execute, advisors recorded, never acted on`.

### Task 3: Operator choice path (answering an escalation within the current schema)

**Files:**
- Create: `src/sentinel/coordinator/operator_choice.py`
- Modify: `src/sentinel/cli.py`, adding the subcommand `sentinel decide --db PATH --run-id UUID --label LABEL [--audit PATH]` and `sentinel pending --db PATH --run-id UUID`
- Test: `tests/coordinator/test_operator_choice.py`, `tests/test_cli_decide.py`

**Interfaces** (shipped 2026-09-20: `JUDGMENT_POINTS = {SELECT_PLAN}` only — decision 1 makes `EVIDENCE_GATE` a deterministic rule, so it is no longer a point this path asks the operator about, though it remains an `ADVISORY_POINT` shadowed by Task 2's `ShadowLadder`; `SELECT_PLAN` is now the single judgment point):
- `pending_decision(repository, run_id, catalogue) -> dict | None` returns `{"decision_point", "options": [{"label", "description"}]}` at a judgment point, or `None`. **It must not include any advisor output.**
- `submit_choice(repository, gateway, run_id, label, *, catalogue, sink, actor_id="OPERATOR_1") -> dict`:
  1. Recompute `legal_set`.
  2. Reject with `CoordinatorError("INVALID_OPERATOR_CHOICE")` unless the decision point is in `JUDGMENT_POINTS` and `label` is in its labels.
  3. Submit the candidate's command through `gateway.submit`, with `actor_type="OPERATOR"`, a deterministic idempotency key `f"operator-choice-{run_id}-{state_version}-{label}"` and `expected_state_version=state_version`. For `escalate`, submit nothing and just record it.
  4. Append `{"record": "operator_choice", "run_id", "state_version", "decision_point", "label", "actor_id"}` to the sink.
  5. Return the gateway result.

  Use the command mapping from `AgentLoop._execute` (the tool name to `command_type` table, including the `inspection.propose_plan` payload wrapper). Factor it into a shared function `command_for(tool_name, arguments, run_id, actor_type, actor_id, key)` in `agent_loop.py`, used by both callers so there is one source of truth.

- [ ] **Step 1: Write the failing tests:**
  - (a) At `SELECT_PLAN`, `submit_choice(..., "PLAN_ROUTE_ONLY_2_ZONE")` produces the `PLAN_PROPOSED` and `AGENT_INTENT_PROPOSED` events with `actor_type == "OPERATOR"`, and the run reaches `WAITING_FOR_APPROVAL`.
  - (b) A label not in the legal set raises `INVALID_OPERATOR_CHOICE`.
  - (c) At a non-judgment decision point — a RULE point, or `EVIDENCE_GATE` now that decision 1 makes it rule-decided (`JUDGMENT_POINTS = {SELECT_PLAN}` only) — any label raises `INVALID_OPERATOR_CHOICE`.
  - (d) The same choice submitted twice is idempotent.
  - (e) `pending_decision` output contains no key named `advisors`, `confidence` or `suggested`.
  - (f) `sentinel pending` prints the options, and `sentinel decide` exits 0 on success and 2 on an invalid label.
  - (g) End to end: a `RulePolicy` loop, plus one operator choice at the single judgment point (`SELECT_PLAN`), plus the operator approvals, reaches `COMPLETED` with the same event types as `test_one_station_flow.py`, except that the actor is `OPERATOR` at that one choice point; `EVIDENCE_GATE` is decided by the rule directly (`chooser: "RULE"`).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, plus the full suite.
- [ ] **Step 5:** Commit with the message `feat: operator choice at judgment points (CLI + service), advisor output withheld`.

### Task 4: Read-only operator chat (LLM narrates, never acts, never pre-empts the operator)

**Files:**
- Create: `src/sentinel/chat.py`
- Test: `tests/test_chat_boundary.py`

**Interfaces:**
- `OperatorChat(complete: Callable[[list[dict]], str], read_state: Callable[[str], DecisionState], read_audit: Callable[[str], list[dict]])`
- `.reply(run_id: str, message: str) -> str`, built as follows:
  - **System message:** "You are the SENTINEL operator assistant. You explain the current state and past decisions. You cannot take actions, approve, reject, or choose. If asked to act, tell the operator which UI/CLI action does it."
  - **User message:** the JSON of `to_packet()`, then the last 10 audit records, then the operator's text.
- **Audit filter (the anti-automation-bias rule):** for a `judgment` record whose `(run_id, state_version, decision_point)` has no matching `operator_choice` record yet, the `advisors` field is removed before the record reaches the prompt.
- `complete` for production: a thin function over `adapter.llm.config` (model and key env *names*) that posts chat messages with **no `tools` parameter**.

- [ ] **Step 1: Write the failing tests:**
  - (a) `reply()` returns the fake `complete` text.
  - (b) The prompt contains the packet and the audit.
  - (c) An unresolved judgment's advisor picks are absent from the prompt, and present once an `operator_choice` record exists.
  - (d) Static boundary: the source of `chat.py` contains none of `command_gateway`, `agent_loop`, `operator_choice`, `sqlite3` or `submit(`.
  - (e) The request built by the production `complete` has no `tools` key (checked against a fake transport).
  - (f) The message "approve the plan" leaves `state_version` unchanged.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, plus the full suite.
- [ ] **Step 5:** Commit with the message `feat: read-only operator chat with advisor output withheld until the operator decides`.

### Task 5: Shadow evaluation report and live shadow sweep

**Files:**
- Create: `src/sentinel/shadow_eval.py`, `scripts/shadow_sweep.py`, `artifacts/decisions/offline-shadow.md`
- Test: `tests/test_shadow_eval.py`

**Interfaces:**
- `score(records: list[dict], truth: Mapping[tuple, str]) -> dict`. Truth is either fixture labels keyed by `turn_id`, or operator choices keyed by `(run_id, state_version, decision_point)`.
- Per advisor and per decision point it reports:
  - `n`
  - `agreement` (the advisor's label equals the truth)
  - `coverage_high`
  - `selective_accuracy` (correct among HIGH picks)
  - `false_act` (HIGH, not `escalate`, while the truth is `escalate`) — **the safety metric**
  - `escalation_rate`
  - `error_rate`
  - `latency_ms_median`
  - `stability` (label flips across repeats)
  - `wilson_lower_95(selective_accuracy)`
- The report header states verbatim: "Shadow only. No advisor has authority. Promotion criteria live in P3 and require n ≥ 60 HIGH-band picks per decision point."
- `scripts/shadow_sweep.py --advisor {fake,static,jev,llm} --repeats 3` runs the advisors over the fixture's judgment turns. `jev` and `llm` refuse to run unless `SENTINEL_SHADOW_SWEEP_CONSENT=<batch-id>` is set.

- [ ] **Step 1: Write the failing tests** with synthetic records:
  - A perfect advisor gives agreement 1.0 and `false_act` 0.
  - An always-`create_task` advisor at 0.95 gives `false_act` equal to the number of escalate-labelled turns.
  - `wilson_lower_95` for 24/24 is ≈ 0.862 (tolerance 0.01), and for 60/60 is ≈ 0.940.
  - An advisor with errors gives the correct `error_rate`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, then generate `offline-shadow.md` with `fake` and `static`. Commit with the message `feat(eval): shadow advisor scoring with Wilson bounds`.
- [ ] **Step 5, gate G-sweep (parent):** state the count. There are 48 judgment turns × 3 repeats × 2 advisors = 288 calls: 144 Jev and 144 LLM. State the models (`typesafe/jev-1.13-20260917`, `z-ai/glm-5.3-flash`) and the estimate from J5 (42 mixed calls cost under $0.01, so under $0.10). With consent, run the sweep and commit `artifacts/decisions/live-shadow-sweep.{jsonl,md}`.

### Task 6: Rehearsal wiring (optional before the freeze)

**Files:**
- Create: `scripts/rehearsal_run.py`

The script runs one simulated demo run end to end:
1. Harness gateway.
2. `ShadowLadder(RulePolicy(), advisors, JsonlAuditSink("artifacts/shadow/<run_id>.jsonl"))`.
3. The operator choices are prompted interactively, or taken from `--choices sp=PLAN_ROUTE_ONLY_2_ZONE,eg=create_task`.
4. The approvals are submitted.

The `--advisors none|fake|live` flag selects the advisors, and `live` requires `SENTINEL_SHADOW_SWEEP_CONSENT`.

- [ ] **Step 1:** Write a smoke test for `--advisors fake --choices ...`: it completes the run and writes both the `judgment` and the `operator_choice` records. **Step 2:** Implement. **Step 3:** Commit with the message `feat: rehearsal runner with shadow audit`.
- [ ] **Step 4, gate G-rehearsal (parent):** with consent, run the rehearsals with live advisors. Append their audits to the shadow data, and rerun `shadow_eval` with operator choices as the truth.

### Final: review and land

- [ ] Fresh whole-branch review since the P1 merge. Check: the non-interference test exists and is meaningful; no advisor output reaches operator-facing surfaces before a choice is made; every mutation goes through the gateway; the lists of skipped tests and unrun live gates are stated.
- [ ] Run the full suite from the worktree and from the repo root, expecting 0 failures.
- [ ] **G-merge:** local `--no-ff` merge, then ask before pushing.

## Self-review

- **F requirements:** rules execute (T2 executor); the operator decides (T3); both advisors are measured on identical inputs (T2, where the same `JevPolicy` wraps both judges); shadow means non-interference (T2a) and no pre-emption (T3e, T4c); chat only (T4); data collection (T5, T6).
- **Deferred to P3:** promotion, the LLM proposer and investigator, Jev acting, typed reads and `operator.ask` (both need G-amend).
- **Known limitations:**
  - There is no demo UI in the repo. The CLI (T3) is the operator surface, and any UI calls `pending_decision` and `submit_choice`.
  - An operator plan choice needs two operator actions (choose, then approve). P3 can merge them once `operator.ask` lands.
