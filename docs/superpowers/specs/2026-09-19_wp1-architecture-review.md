# WP1 Agent-Loop Architecture Review (grilling session 1 of 3)

**Date:** 2026-09-19 · **Scope:** objective 1 (architecture review) + objective 3 (prior-art survey) of the WP1 handoff.
**Out of scope:** objective 2 (Jev vs LLM requirements decision) — the Jev-experiment session is still running and will deliver its own handoff; a comparison section here only frames the decision.

All code references are to `master` = `2193f59`.

---

## 1. Verdict up front

The bounded one-tool-call-per-turn coordinator is the **right shape for V0** and should not be replaced. It is the same skeleton every production harness uses (OpenAI cookbook's 4-step loop; Anthropic's "model in a loop, tools gated by code"; LangGraph supervisor). What is failing the live gate is not the shape — it is **four missing harness mechanics** that production systems all have and SENTINEL's hand-rolled loop does not:

1. **No `parallel_tool_calls: false`** sent to the provider (DeepSeek emitted 2 calls once; the loop then dies with `MULTIPLE_TOOL_CALLS` instead of preventing it).
2. **No corrective retry on malformed/wrong calls** — the first wrong tool call is terminal (`agent_loop.py:66`, evaluator `break`s at `WRONG_TOOL`). Production loops feed the validation error back as the next turn (smolagents `AgentError` → `ActionStep.error` → model fixes it; toolcall-guard's correction message pattern).
3. **No legal home for reasoning** — GLM pours reasoning into `content` and `finish.summary`; the domain tools have a `reason` arg on only 2 of 8 tools, and `task.create` has none.
4. **The scenario prose contradicts itself** (`live_scenarios.json:7`): "exclude any plan that covers more stations than requested" excludes `PLAN_ROUTE_ONLY_2_ZONE` (2 stations) from a 1-station survey by literal reading. The model's `finish(NEEDS_REVIEW)` was a *correct* escalation into a trap we built.

None of these require replacing the coordinator. They are harness and fixture defects.

## 2. Findings against the code (evidence)

### 2.1 The loop: `src/sentinel/coordinator/agent_loop.py`

- **One-call-per-turn is enforced punitively, not preventively.** `run()` checks `len(tool_calls) > 1` (line 54) and fails the whole run. The Chat Completions API has supported `parallel_tool_calls: false` since 2024-11 (OpenAI announcement; Anthropic/Agents SDK expose equivalents). `openai_compatible.py:120-125` never passes it. **Fix: send `parallel_tool_calls: false` in `propose()`** and keep the check as defense-in-depth. Cost: one line.
- **MALFORMED is terminal.** `agent_loop.py:60` — any contract-validation exception kills the run. Production pattern: classify the failure, append a bounded correction ("your call to `task.create` was missing required field `section_id`; re-issue"), retry once or twice inside the horizon, *then* fail. This alone likely flips several of the 6 failed live runs.
- **State inspection is discouraged in the prompt but the gate evaluator gives it for free** (evaluator lines 378-379: inspection calls "never count as deliverables" but *do* consume a turn of the horizon). That's a reasonable design, but the system prompt (`prompt.py:143-148`) tells the model to skip inspection while the *only* stable feedback channel the model has is the JSON context — which for GLM was evidently not enough. Tension, not defect.
- **`wait_for_event` is executed but nothing is ever waited for** (`agent_loop.py:93-94` returns `{"kind": "wait"}` immediately; the harness's `coordinator_feedback` is a static fixture). Fine for the gate, but it means `wait_for_event` vs `finish(NEEDS_REVIEW)` semantics are indistinguishable to the model except via prose. Relevant to finding 2.5 below.
- **Deadline sentinel `2999-01-01` in command envelopes** (`agent_loop.py:103`) and `issued_at` similarly fake — acceptable V0 scaffolding, flagged for the record.
- **Event summaries are nearly content-free**: `_event_summary` (line 84) truncates to `str(event_type)[:128]` — the model sees event *types*, not payloads. In the live gate the harness separately smuggles catalogue/task IDs into event summaries; in production `AgentLoop` would give the model nothing it can act on. **The bounded-summary mechanism needs real payloads before any production claim.**

### 2.2 The evaluator: `tests/agent/test_live_openai_compatible.py::run_bounded_horizon_scenario`

- **`break` on first wrong tool** (line 394). WRONG_TOOL at turn 0 of 4 ends the run — even though the horizon exists precisely to allow recovery. Production loops treat a wrong call as *feedback for the next turn*, not as terminal (toolcall-guard retry-loop; smolagents self-correction). Recommended: emit a WRONG_TOOL classification, feed it back as `previous_result` with the correction, continue the horizon; fail only on budget exhaustion.
- **Dead feedback on failure**: `_previous_result_for_next_turn` only builds feedback for the *expected* tool. When the model errs, the next turn (if any) receives the same static `coordinator_feedback` — no error signal. This is the single biggest divergence from production harness behavior.
- **Escalation classification gap**: `finish(NEEDS_REVIEW)` while deliverables are pending is classified `PREMATURE_FINISH` (hard fail). The handoff records the user's open question — and I'd argue the answer: **escalation on genuinely contradictory constraints should be a pass-class** (distinct from evasive finish). Production precedent: OpenAI Agents SDK models escalation as a handoff to a "human agent"; Anthropic patterns give the model an `escalate` tool as a graceful ceiling; smolagents distinguishes max-steps from parsing errors. Concretely: classify `finish(NEEDS_REVIEW)` as `ESCALATED` — pass iff the scenario is genuinely contradictory, fail if it's evasive. The evaluator can't judge intent, so the fixture must declare which scenarios are contradiction-traps (see 2.4).

### 2.3 The adapter: `openai_compatible.py` + `parser.py` + `prompt.py`

- Parser, tool-name bijection, refusal handling, bounded diagnostics: **solid, production-grade**. No changes needed beyond the `parallel_tool_calls` flag.
- **Reason-in-args**: only `inspection.propose_plan`, `assignment.propose`, and `wait_for_event` take `reason`. `task.create` — the tool GLM must call with 3 exact UUIDs — has no reasoning outlet. Production harnesses (and Anthropic's own guidance) give reasoning a legal home in tool args. **Add an optional `reason` to `task.create` and `patroller.rank_for_task`.**
- **System prompt is prose-only, one-shot** (`prompt.py:138-156`). No mention of `NEEDS_REVIEW` being a legitimate outcome, no description of what `wait_for_event` *does*. The model has to infer the escalation contract entirely from the `finish` enum. Cheap fix: 3-4 sentences.
- **No conversation history**: each turn is a fresh 2-message chat with `previous_result` embedded as JSON. This is a defensible stateless design (bounded, replayable), but it's why the model re-wanders: it never sees its own prior assistant turns. Frameworks keep the message list. For V0 the stateless + feedback design is fine *if* the feedback actually carries error information (finding 2.2).

### 2.4 The scenario fixture: `live_scenarios.json`

- **`initial_plan_one_station` is self-contradictory**: "Survey only the LEFT_ALLEY_MIDDLE station... exclude any plan that... covers more stations than requested" — with the only non-excluded plan being `PLAN_ROUTE_ONLY_2_ZONE` (2 stations). Literal reading excludes everything; the model's `finish(NEEDS_REVIEW)` was correct escalation. **Fix the prose**: either (a) "exclude any plan that requires marker verification; among the remaining plans choose the one with the fewest stations" (matches the intended answer), or (b) rename the plan to `PLAN_ROUTE_LEFT_ALLEY` so the station-vs-plan-zone confusion dies. I recommend (a) + keep the name: the scenario should test constraint-satisfying selection, not lexical traps.
- The `user_request` for `qualifying_evidence_to_task` says "then check the coordinator ranking" — but the event context (line 88) already says "Task created ... awaiting patrol ranking", which reads as if the task already exists. The model ranked first (latest failure: WRONG_TOOL rank-before-create). **Fix the fixture**: that RUN_TASK_CREATED event should not exist before the model creates the task — it's the *result* of deliverable 1. This is a fixture bug, not a model bug.
- **Recommendation on the user's open design question:** add a dedicated `escalation` classification in the evaluator: scenarios declare `"contradictory": true`; `finish(NEEDS_REVIEW)` on such scenarios is a PASS; on non-contradictory scenarios it remains PREMATURE_FINISH. This makes safe escalation a first-class pass without opening an evasion loophole.

### 2.5 Pre-existing env-overlay test failures (documented loose end)

`load_config` auto-loads the repo `.env` over the injected mapping (commit `3784690`). 4 hermetic tests break from the repo root. Recommendation: **make the injected mapping strictly authoritative when provided** — explicit beats ambient in a safety system; keep `.env` auto-load only when no mapping is injected. Alternative (opt-out var `SENTINEL_SKIP_ENV_FILE=1`) adds a second knob where a single rule suffices.

## 3. Prior-art survey (objective 3)

| System | One-call enforcement | Corrective retry | Reason-in-args | Escalation | Bounded horizon |
|---|---|---|---|---|---|
| OpenAI Chat Completions / cookbook | `parallel_tool_calls: false` request flag; strict schemas incompatible with parallel | developer-owned (cookbook appends tool errors as messages) | via tool descriptions/args | developer-owned | developer-owned |
| OpenAI Agents SDK | `ModelSettings.parallel_tool_calls`; per-tool `parallelizable=False` rewrites schema with "must be called alone" hint | errors return to model as tool results | `instructions` + tool descriptions | handoff to "human agent" (Temporal-backed) | `max_turns` |
| Anthropic tool-use | model may batch; guidance: "only batch independent calls"; client decides execution | tool errors returned as `tool_result` with `is_error` | interleaved thinking | approval gates per-tool via `tool_choice` | client loop |
| smolagents | CodeAgent one action per step | `AgentError` hierarchy → `ActionStep.error` → model self-corrects next step; no crash | code as action | ManagedAgent returns structured failure envelope | `max_steps` (`AgentMaxStepsError`) |
| LangGraph (supervisor) | ToolNode runs requested calls; per-agent "one tool at a time" prompts | checkpointer replays; error edges | prompt + state schema | `interrupt()` first-class human-in-the-loop | `recursion_limit` |
| toolcall-guard (npm) | n/a | schema-validate → coerce → **model-ready correction message appended for retry** | n/a | n/a | n/a |
| Production circuit breakers (Portkey etc.) | n/a | replan ≤2× per stage, then escalate | n/a | CLOSED→OPEN→HALF_OPEN on: ≥3 identical calls, ≥2 schema failures | cost/turn budgets |

Cross-cutting consensus relevant to WP1:
1. **Every production loop feeds failures back instead of terminating** — SENTINEL's terminal-on-first-error is the outlier.
2. **Disabling parallel calls at the request level is standard for mutating-only tool sets** — SENTINEL's tool set is entirely proposals (mutations behind an approval gate), so `parallel_tool_calls: false` is unambiguously correct.
3. **Bounded horizons are universal** (`max_turns`/`max_steps`/`recursion_limit`); SENTINEL's is fine.
4. **Human-in-the-loop escalation is a first-class primitive** (LangGraph `interrupt`, Agents SDK handoff) — not an error code. This strongly supports classifying `finish(NEEDS_REVIEW)` as a legitimate outcome channel.

## 4. Recommended change list (ordered, no live tokens needed for 1-5)

1. `parallel_tool_calls: false` in `openai_compatible.propose()` (+ unit test asserting the request payload).
2. Corrective retry: on MALFORMED/WRONG_TOOL/MISSING_ARGUMENTS inside the horizon, append a bounded correction to `previous_result` and continue; terminal only on repeated failure (≤2 corrections) or budget exhaustion. Applies to both `AgentLoop._execute` path and the gate evaluator.
3. Fixture fixes: rewrite `initial_plan_one_station.user_request` (a); remove the premature RUN_TASK_CREATED event from `qualifying_evidence_to_task` (or reword it as "evidence qualified, no task yet").
4. Escalation semantics: optional `reason` on `task.create`/`patroller.rank_for_task`; scenario flag `"contradictory"`; evaluator class `ESCALATED` (pass on contradictory scenarios, PREMATURE_FINISH otherwise); system-prompt sentences stating when NEEDS_REVIEW is correct.
5. Env-overlay contract: injected mapping authoritative when present (fixes the 4 root-run test failures).
6. (After 1-5, requires user go-ahead for token spend) Re-run the live gate with GLM-5.3; expectation: `approval_wait` still passes, `initial_plan_one_station` passes reliably once the prose is fixed, `qualifying_evidence_to_task` becomes the honest capability probe.

None of 1-5 touches the coordinator's architecture; all are harness/fixture mechanics with hermetic tests. This is deliberately scoped so it can land as one reviewed branch on the `sentinel-wp1-kanban` board.

## 5. Framing for the Jev-vs-LLM decision (objective 2, deferred)

When the Jev session's handoff lands, the comparison should be made against *this* fixed harness, not the current one — right now the LLM path is being graded by an evaluator that fails correct escalations and terminates on first error. What each path must prove, to be settled then:
- **Jev/BoundedJudge**: determinism and replayability are already proven (fc75d7b, 24c3199, 5fec9ba). Open question: coverage of novel/ambiguous requests without a scenario catalogue.
- **LLM coordinator**: plausibility of the decision-point executor role, with the corrected gate as the model-adequacy test. Open question: whether any affordable model passes `qualifying_evidence_to_task` reliably, or whether the LLM owns only PLAN-phase proposals with Jev judging the rest.
