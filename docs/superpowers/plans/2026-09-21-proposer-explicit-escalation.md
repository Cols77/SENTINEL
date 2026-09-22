# Proposer Explicit Escalation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the real Jev and LLM inspection proposers deliberately reject unsupported or partially satisfiable requests, preserve complete attempt evidence, and prove the behavior with the existing 12-case live benchmark before any multi-turn work begins.

**Architecture:** Keep the current one-shot `SELECT_PLAN` path. Both proposers receive the same legal catalogue plans plus the canonical `finish(NEEDS_REVIEW)` candidate; the router accepts either a legal plan or explicit escalation and never turns explicit escalation into architecture F. The existing evaluation runner remains the source of the gate report and separates explicit model escalation, confidence-gate escalation, deterministic refusal, and F fallback.

**Tech Stack:** Python 3.11, dataclasses, SQLite, pytest, OpenAI-compatible adapter, Jev fixed-choice API, JSONL evaluation artifacts.

**Dependency:** Approved design `docs/superpowers/specs/2026-09-21-proposer-escalation-and-bounded-multiturn-design.md` at or after commit `2261f12`.

---

## File map

- Modify `src/sentinel/coordinator/proposer.py`: shared escalation identity, Jev criteria/instruction, LLM result normalization, attempt taxonomy.
- Create `src/sentinel/decision_prompt.py`: neutral code-owned SELECT_PLAN instruction and semantic revision shared by Jev and the LLM adapter.
- Modify `src/sentinel/coordinator/architecture.py`: accept canonical explicit escalation and record `proposer_escalation` without F fallback.
- Modify `src/sentinel/adapter/llm/prompt.py`: complete-constraint rule and provider-visible tool filtering.
- Modify `src/sentinel/proposal_eval.py`: score accepted explicit escalation correctly and expose disjoint route metrics.
- Modify `scripts/proposal_sweep.py`: bind prompt revisions and report token/cost/route evidence required by the live gate.
- Modify `src/sentinel/adapter/llm/openai_compatible.py` and `src/sentinel/judgment.py`: emit sanitized provider identity/usage observations before normalized results discard transport metadata.
- Modify `.env.example`: document manifest authorization and explicit expected-resolved-identity inputs without credentials.
- Modify `docs/evaluation.md`: document semantic prompt revision, identity matching, and exact live-gate invocation.
- Modify `tests/coordinator/test_jev_proposer.py`, `tests/coordinator/test_jev_proposer_flow.py`, `tests/coordinator/test_proposer.py`, `tests/coordinator/test_architecture.py`, `tests/coordinator/test_proposer_flow.py`, `tests/agent/test_prompt_context.py`, `tests/test_proposal_eval.py`, and `tests/test_proposal_sweep.py`: focused regression coverage.
- Update `docs/reports/inspection-capability-baseline.md` only after a successful new run; retain every prior artifact unchanged.

### Task 1: Pin the shared escalation contract in proposer tests

**Files:**
- Modify: `tests/coordinator/test_jev_proposer.py`
- Modify: `tests/coordinator/test_jev_proposer_flow.py`
- Modify: `tests/coordinator/test_proposer.py`
- Modify: `tests/agent/test_prompt_context.py`

- [ ] **Step 1: Add failing Jev tests for deliberate escalation and complete constraints**

Add a test whose legal set contains at least two plan candidates and the existing `ESCALATE_LABEL` candidate. Use a fake judge returning `value="escalate"`, complete finite probabilities, and a passing margin. Assert the returned call is exactly:

```python
assert result["status"] == "TOOL_CALLS"
assert result["tool_calls"] == [{
    "contract_version": "sentinel.v0",
    "wire_type": "agent_tool_call",
    "tool_name": "finish",
    "arguments": {
        "status": "NEEDS_REVIEW",
        "summary": "plan proposal needs operator review",
    },
}]
assert judge.criteria["escalate"] == context.legal.by_label("escalate").description
assert "fully satisfy every explicit operator constraint" in judge.instruction
assert "closest" in judge.instruction
```

Also assert the attempt has `raw_choice == "escalate"`, `policy_label == "escalate"`, `gate_outcome == "accepted"`, and that no `proposal_recommendation` exists.

Update every default fake Jev probability map in both Jev test files to include `ESCALATE_LABEL` because Jev validates the exact label set. Preserve dedicated missing-label and extra-label cases by constructing their maps explicitly.

- [ ] **Step 2: Add failing LLM tests for valid and invalid finish calls**

Cover one valid canonicalizable escalation and three invalid raw mappings. `_result()` returns an already schema-validated `AgentModelResult`, so use it only for the valid result and convert it with `.to_dict()` when extracting a call:

```python
valid = _result("finish", {"status": "NEEDS_REVIEW", "summary": "no complete match"})
wrong_status = _result("finish", {"status": "DONE", "summary": "done"})
missing_summary = _raw_result("finish", {"status": "NEEDS_REVIEW"})
valid_call = valid.to_dict()["tool_calls"][0]
extra_call = _raw_result_with_calls([valid_call, valid_call])
```

Assert only `valid` produces an accepted attempt with `policy_label == "escalate"`; the returned summary is the controller constant rather than provider text. The bounded audit retains the provider summary truncated to 512 characters, while executable output never uses it. Assert every invalid result fails closed, is audited, and creates no recommendation. Add a schema-valid result carrying a different `agent_run_id`; it must fail closed as `invalid_answer` instead of being silently rewritten to the current run.

- [ ] **Step 3: Add a failing prompt/tool-filtering test**

Construct one `AgentRequest` with `legal_tools=["inspection.propose_plan", "finish"]` and a finish-only request after all plans have been rejected. Assert provider-visible names use the adapter wire encoding:

```python
request = build_chat_request(
    agent_request,
    context=context,
    prompt_revision="prompt-v4-complete-constraints",
    tool_policy_revision="legal-tools-v2",
)
assert [tool["function"]["name"] for tool in request["tools"]] == [
    wire_tool_name("inspection.propose_plan"), wire_tool_name("finish")
]
assert [tool["function"]["name"] for tool in finish_only["tools"]] == [
    wire_tool_name("finish")
]
system = request["messages"][0]["content"]
assert "fully satisfies every explicit" in system
assert "Partial, approximate" in system
assert "NEEDS_REVIEW" in system
```

- [ ] **Step 4: Run the focused tests and confirm they fail for the intended reasons**

Run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_jev_proposer.py tests/coordinator/test_jev_proposer_flow.py tests/coordinator/test_proposer.py tests/agent/test_prompt_context.py
```

Expected: new Jev assertion fails because escalation is filtered out; new LLM assertion fails because `finish` is rejected; prompt test fails because all approved tools are still exposed.

### Task 2: Implement explicit escalation in both proposers

**Files:**
- Modify: `src/sentinel/coordinator/proposer.py`
- Modify: `src/sentinel/adapter/llm/prompt.py`
- Create: `src/sentinel/decision_prompt.py`
- Test: `tests/coordinator/test_jev_proposer.py`
- Test: `tests/coordinator/test_proposer.py`
- Test: `tests/agent/test_prompt_context.py`

- [ ] **Step 1: Define one canonical escalation recognizer**

Add separate provider and controller boundary helpers beside `_ESCALATION_SUMMARY`:

```python
def parse_provider_escalation(result: object, *, agent_run_id: str) -> str | None:
    document = LlmProposer._as_dict(result)
    if (document is None or document.get("status") != "TOOL_CALLS"
            or document.get("agent_run_id") != agent_run_id):
        return None
    calls = document.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        return None
    call = calls[0]
    arguments = call.get("arguments") if isinstance(call, Mapping) else None
    accepted = (
        call.get("tool_name") == "finish"
        and isinstance(arguments, Mapping)
        and arguments.get("status") == "NEEDS_REVIEW"
        and isinstance(arguments.get("summary"), str)
        and bool(arguments["summary"].strip())
    )
    return arguments["summary"].strip()[:512] if accepted else None

def is_canonical_escalation(result: object, *, agent_run_id: str) -> bool:
    summary = parse_provider_escalation(result, agent_run_id=agent_run_id)
    return summary == _ESCALATION_SUMMARY
```

Export both in `__all__`. `LlmProposer` uses the parser, audits the bounded provider summary, and emits a fresh canonical result. `ArchitectureRouter` accepts only `is_canonical_escalation`; arbitrary provider text can never cross the router unchanged.

- [ ] **Step 2: Offer Jev every legal plan and the existing escalation candidate**

Build candidates from `context.legal.candidates` using tool names `inspection.propose_plan` and `finish`. Move the approved SELECT_PLAN rule and `DECISION_PROMPT_REVISION = "select_plan.complete_constraints.v2"` into neutral `sentinel.decision_prompt`, then import it from both proposer and adapter code. Append it only when `context.legal.decision_point == SELECT_PLAN`; the generic adapter prompt still serves other decision points. The rule is:

```python
instruction = (
    "Choose a catalogue plan only when its described capabilities fully satisfy "
    "every explicit operator constraint and requested deliverable. Partial, "
    "approximate, or merely closest available coverage does not satisfy the "
    "request. Choose escalation when no candidate fully satisfies it, the "
    "constraints conflict, or a required capability or fact is unavailable. "
    "Use only the supplied facts."
)
```

After the existing confidence, finite-probability, legal-label, top-label, and margin checks, return `_escalate(request)` when the accepted label is `ESCALATE_LABEL`; do not write a recommendation.

- [ ] **Step 3: Normalize accepted LLM escalation separately from a plan**

In `LlmProposer.decide`, resolve exactly one of these branches:

```python
if (provider_summary := parse_provider_escalation(
        model_result, agent_run_id=typed_request.agent_run_id)) is not None:
    normalized = self._escalate(request)
    raw_choice = "escalate"
    policy_label = "escalate"
elif (proposal := self._accepted_proposal(
        model_result, context,
        expected_agent_run_id=typed_request.agent_run_id)) is not None:
    normalized = one_call(request, "inspection.propose_plan", {
        "catalogue_plan_id": proposal[0], "reason": proposal[1],
    })
    raw_choice = proposal[0]
    policy_label = proposal[0]
else:
    normalized = self._escalate(request)
    policy_label = "escalate"
```

Extend `_write_attempt` with an explicit `policy_label` argument so an accepted escalation is distinguishable from gate-derived escalation. Write `proposal_recommendation` only in the accepted plan branch.

Change `_accepted_proposal` to require `expected_agent_run_id` and reject a parsed document whose ID differs before reading its plan arguments. Both accepted branches therefore bind to `typed_request.agent_run_id`. Add `provider_summary` to the attempt only for a valid provider escalation, truncated to 512 characters.

Derive `legal_tools` from `context.legal.candidates` instead of the hard-coded `_PROPOSER_TOOLS` list:

```python
legal_tools = list(dict.fromkeys(candidate.tool_name
                                 for candidate in context.legal.candidates))
if set(legal_tools) - {"inspection.propose_plan", "finish"}:
    raise ValueError("SELECT_PLAN contains an unexpected proposer tool")
narrowed = {**request, "legal_tools": legal_tools}
```

This makes a finish-only legal set visible as finish-only to the provider.

- [ ] **Step 4: Filter the OpenAI-compatible tool schemas by `request.legal_tools`**

Add a canonical-name-to-definition map and replace the unconditional deep copy. Definitions store encoded provider names, so construct the map through `wire_tool_name` rather than indexing encoded names with dotted names:

```python
definitions = {
    canonical_name: item
    for canonical_name in APPROVED_TOOL_NAMES
    for item in _TOOL_DEFINITIONS
    if item["function"]["name"] == wire_tool_name(canonical_name)
}
unknown = set(request.legal_tools) - set(definitions)
if unknown:
    raise ValueError(f"no provider tool definition for legal tools: {sorted(unknown)}")
visible_tools = [copy.deepcopy(definitions[name]) for name in request.legal_tools]
```

Return `visible_tools`. Replace the old “never call finish while unfulfilled” wording with the approved complete-satisfaction rule while retaining exact one-call, legal-tool, and no-invented-ID constraints.

- [ ] **Step 5: Run proposer and prompt tests**

Update the existing task-create/rank provider-schema test so its request explicitly lists those tools; exact filtering means a request that does not list them must not see them. Run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_jev_proposer.py tests/coordinator/test_jev_proposer_flow.py tests/coordinator/test_proposer.py tests/agent/test_prompt_context.py tests/agent/test_history.py tests/agent/test_tool_name_transport.py tests/agent/test_openai_compatible.py
```

Expected: all pass.

- [ ] **Step 6: Commit the proposer boundary**

```powershell
rtk git add src/sentinel/decision_prompt.py src/sentinel/coordinator/proposer.py src/sentinel/adapter/llm/prompt.py tests/coordinator/test_jev_proposer.py tests/coordinator/test_jev_proposer_flow.py tests/coordinator/test_proposer.py tests/agent/test_prompt_context.py
rtk git commit -m "fix: let inspection proposers explicitly escalate"
```

### Task 3: Accept explicit escalation at the architecture boundary

**Files:**
- Modify: `tests/coordinator/test_architecture.py`
- Modify: `tests/coordinator/test_proposer_flow.py`
- Modify: `tests/coordinator/test_jev_proposer_flow.py`
- Modify: `src/sentinel/coordinator/architecture.py`

- [ ] **Step 1: Add failing router tests**

Add a proposer that returns canonical `finish(NEEDS_REVIEW)`. Assert the result is returned unchanged, the ladder call count is zero, and the only route record is:

```python
{
    "record": "architecture_route",
    "architecture": "E",
    "run_id": RUN_ID,
    "state_version": 1,
    "decision_point": "SELECT_PLAN",
    "route": "proposer_escalation",
    "label": "escalate",
}
```

Retain tests proving malformed output, illegal tools, unknown plans, and exceptions still fail closed during Increment 1.

Add a real Jev + router + `AgentLoop` flow using an unsupported request. Assert terminal `NEEDS_REVIEW`, no plan/task/recommendation mutation after the pre-existing run creation, one accepted explicit-escalation attempt, and one `proposer_escalation` route. Add the equivalent LLM adapter-to-proposer-to-router-to-loop flow, or document the existing generic router flow as its exact substitute if it exercises the same normalized result.

- [ ] **Step 2: Run the new router tests and confirm F fallback occurs**

Run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_architecture.py tests/coordinator/test_proposer_flow.py tests/coordinator/test_jev_proposer_flow.py
```

Expected: the new explicit-escalation test fails because the router records `fallback_F`.

- [ ] **Step 3: Add a three-way router classification**

Import `is_canonical_escalation`. In `decide`, classify in this order:

```python
if is_canonical_escalation(result, agent_run_id=request["agent_run_id"]):
    self._write_route(context, route="proposer_escalation", label="escalate")
    return result
reason = self._reject_reason(result, context)
if reason is not None:
    return self._fallback(request, context, reason)
self._write_route(context, route="proposer", label=plan_id)
return result
```

Extract `_write_route` to keep the audit fields identical. Keep `_reject_reason` plan-specific after the explicit escalation branch.

- [ ] **Step 4: Run router and end-to-end flow tests**

Run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_architecture.py tests/coordinator/test_proposer_flow.py tests/coordinator/test_jev_proposer_flow.py tests/coordinator/test_agent_loop.py
```

Expected: all pass; explicit escalation ends `NEEDS_REVIEW` and F is not called.

- [ ] **Step 5: Commit the router change**

```powershell
rtk git add src/sentinel/coordinator/architecture.py tests/coordinator/test_architecture.py tests/coordinator/test_proposer_flow.py tests/coordinator/test_jev_proposer_flow.py
rtk git commit -m "fix: preserve proposer escalation through architecture E"
```

### Task 4: Correct evaluation scoring and route denominators

**Files:**
- Modify: `tests/test_proposal_eval.py`
- Modify: `src/sentinel/proposal_eval.py`

- [ ] **Step 1: Add scorer regressions for explicit escalation**

Build a complete escalation record with one accepted attempt whose `raw_choice` and `policy_label` are `escalate`, one `proposer_escalation` route, no plans/tasks/recommendations, repository `final_state.run_status == "RUNNING"`, and a terminal `loop_results` row with `status == "NEEDS_REVIEW"`. Assert:

```python
score = score_outcome(case, record)
assert score["outcome_success"] is True
assert score["proposal_quality_success"] is None
assert score["escalation_success"] is True
assert score["explicit_escalations"] == 1
assert score["confidence_gate_escalations"] == 0
assert score["architecture_f_fallbacks"] == 0
assert score["false_proposals"] == 0
```

Add separate records for low confidence, deterministic refusal, provider failure, and `fallback_F`; assert all counters are disjoint and F fallback never supplies proposal-quality credit. Update existing recovery-path expectations: provider failure, malformed provider output, and illegal reference now stop safely at `NEEDS_REVIEW` and are negative supported-case quality outcomes; marker, rejection/reproposal, and duplicate recovery remain successful recovery cases.

- [ ] **Step 2: Run the scorer tests and confirm the old `None` quality behavior**

Run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_proposal_eval.py
```

Expected: explicit escalation is currently classified as unmeasured or false.

- [ ] **Step 3: Implement disjoint attempt and route classification**

Use these definitions:

```python
explicit = [a for a in proposals
            if a.get("gate_outcome") == "accepted"
            and a.get("raw_choice") == "escalate"
            and a.get("policy_label") == "escalate"]
gate_escalations = [a for a in proposals
                    if a.get("gate_outcome") in {"low_confidence", "low_margin"}]
deterministic_refusals = [a for a in proposals
                          if a.get("gate_outcome") == "invalid_answer"]
provider_failures = [a for a in proposals
                     if a.get("gate_outcome") == "provider_error"]
routes = [a for a in record.get("audit", [])
          if a.get("record") == "architecture_route"]
f_fallbacks = [a for a in routes if a.get("route") == "fallback_F"]
```

Split `accepted_plan_attempts` from `accepted_escalation_attempts` before computing false proposals, accepted/correct proposals, accuracy, or coverage. An accepted escalation never enters a plan denominator. For every expected-escalation case, keep proposal quality not applicable and compute `escalation_success` separately. Accepted explicit escalation earns deliberate-escalation quality only when there is no persisted proposal/recommendation and no F fallback. Confidence-gate, malformed, and provider-failure safe terminals remain visible without deliberate-choice credit.

Define `safe_escalation_trial` as: at least one provider attempt exists; the loop terminates `NEEDS_REVIEW`; there is no accepted plan attempt, recommendation, persisted plan, or accepted `AGENT_INTENT_PROPOSE` gateway command; and no F fallback receives credit. Define `correct_supported_trial` as: an accepted proposer plan belongs to `acceptable_plans`, the expected persisted outcome follows, and no F fallback contributes. `aggregate()` derives every applicable denominator from the actual terminal case records. The live-gate predicate separately requires exactly 18 supported and 18 escalation-required trials for the fixed fixture and three repeats.

- [ ] **Step 4: Add the counters and denominators to `aggregate`**

Return explicit counts for `explicit_escalations`, `confidence_gate_escalations`, `deterministic_refusals`, `provider_failures`, `architecture_f_fallbacks`, `unsupported_false_proposals`, accepted-plan calls, and their applicable denominators. Preserve existing keys used by historical report readers.

- [ ] **Step 5: Run proposal evaluation tests**

Run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_proposal_eval.py tests/test_evaluation_run.py
```

Expected: all pass.

- [ ] **Step 6: Commit scoring changes**

```powershell
rtk git add src/sentinel/proposal_eval.py tests/test_proposal_eval.py
rtk git commit -m "fix: score explicit proposer escalation honestly"
```

### Task 5: Bind live-gate telemetry and prompt revisions

**Files:**
- Modify: `tests/test_proposal_sweep.py`
- Modify: `scripts/proposal_sweep.py`
- Modify: `src/sentinel/adapter/llm/openai_compatible.py`
- Modify: `src/sentinel/judgment.py`
- Modify: `tests/agent/test_openai_compatible.py`
- Modify: `tests/test_judgment.py`
- Modify: `.env.example`
- Modify: `docs/evaluation.md`

- [ ] **Step 1: Add failing manifest and report tests**

Assert a dry-run manifest records one code-owned semantic decision-prompt revision shared by Jev and LLM, the LLM adapter prompt/tool-policy revisions separately, Jev thresholds, the fixed 12 case IDs, three repeats for live mode, maximum calls, token/cost accounting fields, and requested/expected/resolved identity status. Assert the report shows explicit escalation, confidence-gate escalation, deterministic refusal, provider failure, and F fallback as disjoint counters with denominators.

- [ ] **Step 2: Run the CLI tests and confirm missing telemetry**

Run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_proposal_sweep.py tests/test_live_consent_gates.py
```

Expected: new revision/telemetry assertions fail.

- [ ] **Step 3: Add immutable revision, identity, and usage fields**

Import `DECISION_PROMPT_REVISION` from neutral `sentinel.decision_prompt` into the runner. Both Jev and the LLM SELECT_PLAN prompt use that constant. For this gate require semantic revision `select_plan.complete_constraints.v2`, configured LLM prompt revision `prompt-v4-complete-constraints`, and configured tool-policy revision `legal-tools-v2`; reject any mismatch in dry run. Record those separately from Jev `act_at` and `min_margin`.

Add repeatable `--expected-resolved-model ARM=MODEL_ID` and `--identity-decision-note ARM=TEXT` inputs. The immutable pre-call manifest binds requested and expected identities plus the operator-reviewed comparison note; provider-observed identities go to the append-only raw stream, case outcomes, and summary/report. Compare them as `verified`, `partially_verified`, `unverified`, or `mismatched`; mismatch makes the live gate fail without rewriting the manifest. Do not silently bless the historically observed Jev alias `~typesafe/jev-latest` as equivalent to requested `typesafe/jev-1.13-20260917`; execution requires an explicit operator decision in a newly generated manifest if the endpoint still resolves an alias. Record that the current LLM request `z-ai/glm-5.3` differs from retained `z-ai/glm-5.3-flash` evidence and therefore cannot be pooled as an identity-matched before/after run.

Add a sanitized observation callback to `OpenAICompatibleAgentModel` and `JevJudge`. Invoke it at the transport boundary with provider model identity and bounded numeric usage before returning the normalized agent result/choice. The live runner supplies a callback that writes these fields to the raw stream. Aggregate `prompt_tokens`, `completion_tokens`, and `total_tokens` per arm; compute `estimated_cost_usd` from the manifest’s reviewed per-call estimate while also showing raw token totals. Add fixtures for the Jev HTTP dictionary and OpenAI `model_dump()` usage shapes. Missing usage remains `null`, never zero.

- [ ] **Step 4: Render the gate table**

Render supported correctness as `correct/18`, escalation correctness as `safe NEEDS_REVIEW with no accepted plan/18`, unsupported false proposals as `count/18`, plus provider calls, token usage, latency, cost, explicit escalation, confidence-gate escalation, deterministic refusal, provider failure, and F fallback. The gate permits safe confidence-driven escalation while reporting it separately from deliberate model escalation.

Compute `live_gate_passed` only when mode is live, the fixture hash equals `generated-requests-v1.json`, repeats equal 3, exactly 36 trials exist for the single arm, and every trial has exactly one top-level `provider_request`/`provider_result` pair, one per-case `proposal_attempt`, and one terminal `case_outcome`. Return a nonzero process status for an applicable gate failure. In scripted, recorded, partial, wrong-fixture, or multi-arm runs, emit `live_gate_applicable=false` and never show a passing gate.

- [ ] **Step 5: Run sweep tests**

Run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_proposal_sweep.py tests/test_live_consent_gates.py tests/test_proposer_factory.py tests/test_evaluation_run.py tests/agent/test_openai_compatible.py tests/test_judgment.py
```

Expected: all pass and dry run creates no provider or artifact.

- [ ] **Step 6: Commit telemetry changes**

```powershell
rtk git add src/sentinel/adapter/llm/openai_compatible.py src/sentinel/judgment.py scripts/proposal_sweep.py tests/agent/test_openai_compatible.py tests/test_judgment.py tests/test_proposal_sweep.py .env.example docs/evaluation.md
rtk git commit -m "feat: report the explicit escalation live gate"
```

### Task 6: Serial verification, live gate, and independent reviews

**Files:**
- Update after successful run: `docs/reports/inspection-capability-baseline.md`
- Create at execution time: a new non-overwriting directory under `artifacts/proposal-eval/`

- [ ] **Step 1: Run the focused Increment 1 suite serially**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_jev_proposer.py tests/coordinator/test_jev_proposer_flow.py tests/coordinator/test_proposer.py tests/coordinator/test_architecture.py tests/coordinator/test_proposer_flow.py tests/agent/test_prompt_context.py tests/agent/test_openai_compatible.py tests/test_judgment.py tests/test_proposal_eval.py tests/test_proposal_sweep.py tests/test_live_consent_gates.py tests/test_proposer_factory.py tests/test_evaluation_run.py
```

Expected: all pass.

- [ ] **Step 2: Run the full serial suite**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short
```

Expected: exit code 0. Record counts, skips, warnings, duration, and the exact commit.

- [ ] **Step 3: Generate separate Jev and LLM live dry-run manifests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/proposal_sweep.py --mode live --arms jev --fixtures fixtures/proposal_eval/generated-requests-v1.json --repeats 3 --max-calls 36 --cost-per-call-usd 0.01 --expected-resolved-model jev=typesafe/jev-1.13-20260917 --identity-decision-note "jev=pinned requested identity; provider alias is a blocking mismatch" --dry-run
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/proposal_sweep.py --mode live --arms llm --fixtures fixtures/proposal_eval/generated-requests-v1.json --repeats 3 --max-calls 36 --cost-per-call-usd 0.01 --expected-resolved-model llm=z-ai/glm-5.3 --identity-decision-note "llm=current env identity; retained flash evidence is non-comparable" --dry-run
```

Expected: each prints its canonical manifest and authorization digest, makes zero provider calls, and creates no artifact directory.

- [ ] **Step 4: Obtain manifest-bound authorization and execute each live arm**

Set the existing provider-specific consent variable and the exact authorization digest printed in Step 3. Use a fresh output directory for each arm. Do not reuse an authorization after any code, fixture, threshold, prompt, model, repeat, or budget change.

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/proposal_sweep.py --mode live --arms jev --fixtures fixtures/proposal_eval/generated-requests-v1.json --repeats 3 --max-calls 36 --cost-per-call-usd 0.01 --expected-resolved-model jev=typesafe/jev-1.13-20260917 --identity-decision-note "jev=pinned requested identity; provider alias is a blocking mismatch" --execute --output artifacts/proposal-eval/explicit-escalation-jev-20260921
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/proposal_sweep.py --mode live --arms llm --fixtures fixtures/proposal_eval/generated-requests-v1.json --repeats 3 --max-calls 36 --cost-per-call-usd 0.01 --expected-resolved-model llm=z-ai/glm-5.3 --identity-decision-note "llm=current env identity; retained flash evidence is non-comparable" --execute --output artifacts/proposal-eval/explicit-escalation-llm-20260921
```

Expected: each run stays within its bound call budget and writes manifest, append-only raw stream, per-case database snapshots, summary, and report.

- [ ] **Step 5: Enforce the hard gate**

Verify both reports show `18/18` correct supported trials, `18/18` correct escalation-required trials, `0/18` unsupported false proposals, zero F fallback credit, complete raw attempts/outcomes, and recorded identity/revision/hash/threshold/budget/latency/token/cost fields. If either misses, stop this plan at Increment 1, diagnose retained raw attempts, revise only candidate descriptions/escalation contract/complete-constraint prompt under a new revision, and repeat Tasks 1–6. Do not start the bounded multi-turn plan.

- [ ] **Step 6: Update the inspection report without changing historical artifacts**

Add a dated section linking the two new artifact directories, fixed-harness gate result, denominators, identities, and limitations. Include a before/after table against retained prior artifacts wherever fixture and identity are comparable; mark every fixture, prompt, or identity difference explicitly rather than pooling it. State that the generated cases are synthetic and do not establish broad model reliability.

- [ ] **Step 7: Dispatch independent specification and quality reviews**

Give the specification reviewer the approved design, this plan, diff, and live reports. Give a separate quality reviewer the production diff, tests, raw-stream schema, and manifests. Fix every confirmed important issue in a task-sized commit, rerun the affected focused tests and the full serial suite, then repeat review until neither reviewer reports an important unresolved item.

- [ ] **Step 8: Commit the verified report update**

```powershell
rtk git add docs/reports/inspection-capability-baseline.md
rtk git commit -m "docs: publish explicit escalation baseline"
```

The next plan, `docs/superpowers/plans/2026-09-21-bounded-inspection-decision-session.md`, is authorized to begin only when Step 5 passes for both evaluated proposers.
