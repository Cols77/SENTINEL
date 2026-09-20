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

---

# Addendum — re-run of `qualifying_evidence_to_task` against typed result ids

**Batch:** `2026-09-20-operator-grant-2` (consent C-9). **Master:** `b8af3b9`. **Model:** `z-ai/glm-5.3`.
**Spend:** 4 model calls, one scenario. **Command:**

    SENTINEL_RUN_LIVE_LLM_ACCEPTANCE=1 uv run python -m pytest \
        tests/agent/test_live_gate_real_loop.py -k qualifying_evidence_to_task -q -o addopts=''

**Result: fail — and the hypothesis it was run to test was never reached.**

Calls: `task.create` → `state.get_recent_events` → `state.get_recent_events` → `task.create`;
final result `FAILED / INVALID_ARGUMENTS`.

What the run database shows (`artifacts/wp1/evidence/2026-09-20-grant-2-qualifying_evidence_to_task.sqlite3`):

- **The `tasks` table is empty and there is no `TASK_CREATED` event.** Neither `task.create` created
  anything. This run is *not* "a successful `task.create` repeated"; the model never got a task made.
- `INVALID_ARGUMENTS` is raised by the adapter's own argument parser
  (`src/sentinel/adapter/llm/parser.py`), before the gateway is involved.
- `task.create` requires three arguments: `perception_result_id` (uuid), `evidence_artifact_id` (uuid)
  and `section_id`.

> **CORRECTION, same day.** This addendum first said the model "cannot read the ids it is required to
> pass". A free offline replay of the scenario's first request shows that is wrong as stated: **all three
> ids were in the first request**, in `evidence_context` -- `artifact_id` as a typed field, but
> `perception_result_id` only as `result_id=...` inside a prose summary, and `section_id` as `station=...`.
> The model could have built a correct call on turn one. What is established is narrower:
>
> - no task was created, so the **first** `task.create` was rejected -- and *why* is unknown, because the
>   harness recorded tool names only, never the model's arguments nor the rejection it was shown;
> - the two reads that followed could not help it: `state.get_recent_events` returned `"events": []`, and
>   all nine events shared one 512-character `safe_summary`, which cut off `DETECTION_RECORDED` -- the
>   newest event and the only relevant one. Even uncut, the event summary's redaction keeps
>   `station_capture` and drops `result_id`;
> - the name mismatch (`result_id` vs `perception_result_id`, `station` vs `section_id`) is a plausible
>   cause of the first rejection. It is a hypothesis, not a finding.

**What this does and does not establish.**

- It does **not** test typed result ids: no id is returned until a task exists.
- It shows the recovery path was closed: once a call is rejected, the model's read tools gave it nothing.
- The first run's reading ("created the task correctly, then repeated it") was inferred from the call
  list and **was not checked against the database**. It may have been the same failure. That run's
  database was not kept, so this cannot be settled; treat the earlier sentence as unverified.

**A second defect found while reading the code (not exercised by this run, no paid call needed to see it):**
`prompt._bounded_previous` copies only `kind`, `run_status`, `phase` and `state_version` from a tool
result into what the model is shown. The typed ids that WP0 §5 added (`plan_id`, `task_id`, and the
ranking/assignment ids) are validated, returned by `AgentLoop` — and then dropped before the prompt is
built. The amendment shipped in the schema and the loop but never reached the model.

**Fixed on `fix/model-visible-results` (2026-09-20), all without a paid call:**

1. `event_list` items carry `sequence_no` / `event_type` / `summary` (wire schema; operator Coherence
   consent C-12) and the loop returns one typed item per event.
2. `_bounded_previous` renders the typed ids, `patroller_ids` and the event items.
3. The evidence summary names each id by the `task.create` argument it fills.
4. The live gate now writes every request, every model result **with its arguments**, and a copy of the
   run database to `artifacts/wp1/live-runs/<batch>-<scenario>.*` before it scores the run
   (`SENTINEL_LIVE_BATCH_ID` names the batch).

The next paid run of this scenario is therefore the first one whose failure, if any, can be read
rather than guessed. It still needs fresh consent.

---

# Addendum 2 -- third run, and the first one that could be read

**Batch:** `2026-09-20-operator-grant-3` (consent C-17). **Master:** `ab3097b`. **Model:** `z-ai/glm-5.3`.
**Spend:** 6 model calls. **Raw record:** `artifacts/wp1/live-runs/2026-09-20-operator-grant-3-qualifying_evidence_to_task.{json,sqlite3}`
-- every request, every model result with its arguments, and the run database.

**Result: fail on the turn budget -- and the cause is now a fact, not a guess.**

| Turn | Model call | Gateway |
|---|---|---|
| 1 | `task.create` -- all three ids correct, plus a `reason` | **REJECTED `INVALID_CONTRACT`** |
| 2 | `task.create` -- same, `reason` reworded | **REJECTED `INVALID_CONTRACT`** |
| 3 | `state.get_summary` | accepted |
| 4 | `state.get_recent_events` -- nine typed items returned | accepted |
| 5 | `task.create` -- same ids, **no `reason`** | **ACCEPTED**, task created |
| 6 | `patroller.rank_for_task` with the typed `task_id` it had just been given | turn budget exhausted |

**The defect.** The wire contract defines each mutating tool twice. `AgentTaskCreateArguments` lets the
model pass an optional `reason`; `CommandTaskCreatePayload` has `additionalProperties: false` and no
`reason`. `agent_loop.command_for` copied the arguments into the payload verbatim, so a model that used a
documented optional argument was told its correct call violated the contract. The same mismatch exists for
`patroller.rank_for_task` and `inspection.propose_plan`. The rejection text shown to the model is a dump of
the envelope truncated at 512 characters, which ends before the part that names the offending field -- the
model found the fix by trial.

**This very probably explains all three runs.** Run 1 ("repeated `task.create` until the correction budget
was exhausted") and run 2 ("`task.create`, two reads, `task.create`") both fit a model retrying a call that
was correct and being rejected for its `reason`. Neither kept its arguments, so this stays an inference for
those two; for run 3 it is recorded.

**What the earlier fixes did do.** Turn 6 used the typed `task_id` straight from the previous result, which
the model could not see before `_bounded_previous` was fixed; turn 4 returned real events. Both worked as
intended. Neither was the blocker.

**Fixed on `fix/command-payload-keys`, no gate involved** (no schema edit, no gateway edit): `command_for`
passes only the keys the command payload admits. A test derives that key list from the schema itself, so an
amendment cannot put the two definitions out of step again, and another replays this run's recorded turn-1
call through the real loop: three calls, no rejection, `WAITING_FOR_APPROVAL`.

**Still open, not fixed here:** the rejection message. A model should be told *which field* was refused.
