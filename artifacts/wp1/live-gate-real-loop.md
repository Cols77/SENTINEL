# WP1 live gate on the real loop — P1 Task 7 Step 4

**Batch:** `2026-09-20-operator-grant-1` (operator consent recorded in
`docs/superpowers/specs/2026-09-20_operator-consent-record.md`, C-6).
**Model:** `z-ai/glm-5.3` via OpenRouter. **Command:**

    SENTINEL_RUN_LIVE_LLM_ACCEPTANCE=1 uv run python -m pytest \
        tests/agent/test_live_gate_real_loop.py -q -o addopts=''

This closes P1 Task 7. It is **not** a demo dependency: the demo path
(architecture F) uses no LLM in the decision loop.

## Why this run is not comparable to the six earlier failures

The earlier WP1 runs graded models that could not see the database: event
payloads were dropped (D1), ids were hand-smuggled into the scenario fixture
(D10), and the gate scored an exact call list, so any use of a read tool failed
the scenario by construction. Worse, `state.get_recent_events` *killed the run*
on any populated event log — a WP0 schema defect found only in this batch. Those
runs measured the harness.

## Result: 2 of 3 scenarios pass

| Scenario | Result | Model behaviour |
|---|---|---|
| `approval_wait` | **pass** | waited while approval was pending; proposed nothing |
| `initial_plan_one_station` | **pass** | `state.get_summary` → `state.get_recent_events` → `inspection.propose_plan` (correct plan) → `finish` |
| `qualifying_evidence_to_task` | **fail** | `task.create` (correct), then repeated `task.create` until the gateway correction budget was exhausted → `GATEWAY_REJECTED` |

Scoring is the operator's rule of 2026-09-20: the required deliverables must
appear **in order** within the turn budget; reads are legal and each read still
consumes a turn; any other unexpected call fails. `qualifying_evidence_to_task`
was given 6 turns (3 deliverables plus room for up to 3 reads); at its original
4 it was unwinnable for any model that inspected state before acting.

## Reading the failure

The model did not wander and did not pick wrong tools: it **repeated a mutating
call that had already succeeded**. The loop behaved exactly as P1 designed —
each repeat was rejected by the gateway, fed back as a typed `REJECTED` result,
and the run ended cleanly on a bounded budget instead of crashing.

**Open hypothesis (the most testable question left in WP1):** after
`task.create` succeeds, the payload the model receives is `{"kind": "task"}`
with **no `task_id`** — `AgentToolResultPayload` forbids extra fields — so the
id exists only inside the free-text `safe_summary`. The model may be repeating
because it cannot cleanly see the argument it needs for
`patroller.rank_for_task`. The typed-result-ids item of
`2026-09-19_wp0-amendment-decision-context-and-reads.md` would settle it, and it
is the same amendment that fixes the `event_list` defect below.

## Defects this run exposed (fixed in `376ec2f`, merged as `1c6d3d6`)

1. **`event_list` admits no properties** (`{"type": "object",
   "additionalProperties": false}`), so `state.get_recent_events` killed any run
   with events. Interim: empty array, summaries in `safe_summary`. **Still needs
   a WP0 amendment.**
2. **A raising policy was reported as `INVALID_AGENT_RESULT`**, hiding a missing
   `openai` package for two full scenarios. Now `AGENT_CALL_FAILED`.
3. **`LlmJudge.choice` rejected the `criteria` keyword**, so the shadow sweep's
   entire LLM arm errored.

## Cost

Whole batch (this gate plus both shadow arms, including the re-runs after each
fix): **$0.40**, against a consent-time estimate of "under $0.10". The estimate
was carried over from J5's much smaller payloads and was not refreshed before
the re-runs.
