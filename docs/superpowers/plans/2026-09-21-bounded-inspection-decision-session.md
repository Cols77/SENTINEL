# Bounded Inspection Decision Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Jev and the LLM the same bounded inspection read/propose/escalate protocol with typed correction feedback and honest trajectory evaluation.

**Architecture:** A deterministic `BoundedDecisionSession` projects authoritative inspection state into versioned candidate packets. Jev selects stable candidate IDs and the LLM uses one adapter-internal `decision.select` tool over those same IDs. A typed internal `NormalizedDecision` crosses from the selector/router to `AgentLoop`; public read/proposal/escalation calls remain schema-validated, and validation/gateway failures return as typed next-turn feedback within two-read, two-correction, three-call limits.

**Tech Stack:** Python 3.11, dataclasses, JSON Schema, pytest, existing coordinator AgentLoop/gateway and inspection evaluation runner.

**Hard dependency:** `docs/superpowers/plans/2026-09-21-proposer-explicit-escalation.md` Task 6 must have passed for both live proposer arms. Record the two passing manifest digests in the implementation PR or branch notes before Task 1.

---

## File map

- Create `src/sentinel/coordinator/decision_session.py`: session budgets, packet hashing, transcript, candidate registry, normalization errors.
- Create `src/sentinel/coordinator/inspection_decisions.py`: inspection proposal/escalation candidates and genuinely useful state/event reads.
- Modify `src/sentinel/coordinator/proposer.py`: shared bounded Jev/LLM selector adapters while retaining the one-shot classes for comparison.
- Modify `src/sentinel/decision_prompt.py`: shared bounded read/act/escalate instruction and protocol revision.
- Modify `src/sentinel/coordinator/architecture.py`: validate/forward normalized decisions and raise typed `DecisionRejected`; no F fallback inside bounded architecture E.
- Modify `src/sentinel/coordinator/agent_loop.py`: internal normalized-decision branch, three-call session, typed feedback, read execution, current-candidate revalidation.
- Modify `src/sentinel/coordinator/policy.py`: pass session-aware context and preserve `forget` lifecycle.
- Modify `docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json`: result-only `decision.validate`; the Python wire wrappers continue to validate through this schema.
- Modify `src/sentinel/adapter/llm/prompt.py` and `src/sentinel/adapter/llm/openai_compatible.py`: candidate packet, bounded history, active-tool schemas, normalized provider history.
- Create `src/sentinel/adapter/llm/decision_selector.py`: internal `decision.select(candidate_id)` transport that never reaches `AgentLoop` as a callable workflow tool.
- Modify `src/sentinel/coordinator/command_gateway.py`: include current state version in final command-envelope identity.
- Modify `src/sentinel/proposal_eval.py` and `scripts/proposal_sweep.py`: one-shot versus bounded trajectories and response scoring.
- Add focused tests under `tests/contracts`, `tests/coordinator`, and `tests/agent` as named below.

### Task 1: Add result-only deterministic validation feedback

**Files:**
- Modify: `docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json`
- Modify: `tests/contracts/test_schema_validation.py`
- Modify: `tests/agent/test_model_visible_results.py`

- [ ] **Step 1: Add failing contract tests**

Create a valid `agent_tool_result` with `tool_name="decision.validate"`, `status="REJECTED"`, an approved error code, current state/cursor, bounded `legal_tools`, safe summary, and `{"kind":"empty"}`. Assert schema validation succeeds. Create an `agent_tool_call` with the same tool name and assert schema validation fails. Assert the prompt’s previous-result renderer preserves the result while provider tool definitions never include `decision.validate`.

- [ ] **Step 2: Run contract tests and verify result validation fails**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/contracts/test_schema_validation.py tests/agent/test_model_visible_results.py
```

Expected: result-side validation fails because `decision.validate` is absent from the result tool-name enum.

- [ ] **Step 3: Add `decision.validate` only to the result schema**

Split the JSON Schema’s currently shared tool-name definition so `AgentToolCall.tool_name` continues to use the existing eight-name enum while `AgentToolResult.tool_name` uses those eight names plus `decision.validate`:

```json
{
  "$defs": {
    "AgentResultToolName": {
      "enum": [
        "state.get_summary", "state.get_recent_events",
        "inspection.propose_plan", "task.create",
        "patroller.rank_for_task", "assignment.propose",
        "wait_for_event", "finish", "decision.validate"
      ]
    }
  }
}
```

Reference this new definition only from `AgentToolResult`. Do not add it to `AgentToolCall`, `APPROVED_TOOL_NAMES`, `AgentRequest.legal_tools`, or a provider function definition.

- [ ] **Step 4: Run contract and adapter tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/contracts/test_schema_validation.py tests/contracts/test_agent_contract.py tests/agent/test_model_visible_results.py tests/agent/test_tool_name_transport.py
```

Expected: all pass.

- [ ] **Step 5: Commit the additive wire change**

```powershell
rtk git add docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json tests/contracts/test_schema_validation.py tests/agent/test_model_visible_results.py
rtk git commit -m "feat: add result-only decision validation feedback"
```


### Task 2: Implement the deterministic session and candidate registry

**Files:**
- Create: `src/sentinel/coordinator/decision_session.py`
- Create: `src/sentinel/coordinator/inspection_decisions.py`
- Create: `tests/coordinator/test_decision_session.py`
- Create: `tests/coordinator/test_inspection_decisions.py`

- [ ] **Step 1: Write failing model, hash, and budget tests**

Cover stable candidate IDs, canonical packet hashes, call/read/correction consumption, duplicate-read refusal, call-three terminal behavior, and transcript bounds. Use these public types:

```python
DecisionKind = Literal["read", "answer", "suggestion", "clarification",
                       "report", "proposal", "escalation"]

@dataclass(frozen=True)
class DecisionCandidate:
    candidate_id: str
    kind: DecisionKind
    tool_name: str | None
    arguments: dict[str, Any]
    description: str
    source_refs: Sequence[dict[str, Any]] = ()
    unmet_constraints: Sequence[str] = ()

@dataclass(frozen=True)
class DecisionPacket:
    run_id: str
    agent_run_id: str
    call_index: int
    conversation_id: str
    root_request_id: str
    parent_agent_run_id: str | None
    request_revision: int
    clarification_round: int
    state_version: int
    event_cursor: int
    operator_request: str
    decision_point: str
    context_sha256: str | None
    candidates: Sequence[DecisionCandidate]
    transcript: Sequence[dict[str, Any]]
    reads_remaining: int
    corrections_remaining: int
    provider_calls_remaining: int

@dataclass(frozen=True)
class NormalizedDecision:
    agent_run_id: str
    call_index: int
    candidate_id: str
    kind: DecisionKind
    packet_sha256: str

@dataclass(frozen=True)
class DecisionResolution:
    packet: DecisionPacket
    candidate: DecisionCandidate

@dataclass(frozen=True)
class DecisionFinalization:
    disposition: Literal["ANSWERED", "SUGGESTED", "AWAITING_RESPONSE", "REPORTED"]
    response_id: str
    response: Mapping[str, Any]
```

Assert the canonical hash excludes credentials/provider payloads and changes when state version, candidate identity, or transcript result version changes. `NormalizedDecision` carries only the provider's normalized selection; it does not carry a trusted candidate object. Assert it can represent non-callable future response kinds without making their internal names legal public tool calls. All persisted/API `DecisionKind` values and kind-derived candidate ID prefixes use the declared lowercase literals; uppercase words elsewhere are terminal disposition or error-code values, not decision kinds.

Before the durable conversation tables exist, inspection-only sessions derive non-authoritative lineage deterministically from the existing run identity: `conversation_id=f"inspection:{agent_run_id}"`, `root_request_id=agent_run_id`, `parent_agent_run_id=None`, `request_revision=1`, and `clarification_round=0`. These values provide stable packet identity only and are never persisted as a demo conversation. The dependent demo plan replaces them with repository-backed lineage. Tests assert the defaults are stable, unique by agent run, and cannot be supplied by the provider.

- [ ] **Step 2: Run the session tests and verify import failure**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_decision_session.py
```

Expected: collection fails because the module does not exist.

- [ ] **Step 3: Implement the registry and typed rejection**

Implement `DecisionRejected(error_code, safe_summary)` so it stores a normalized code and a 512-character summary. Implement `CandidateRegistry(candidates)` with `by_id`, `normalize_call`, and `visible_tool_names`; duplicate IDs fail construction, unknown IDs raise `UNKNOWN_CANDIDATE`, mismatched identity arguments raise `ARGUMENT_MISMATCH`, and visible names preserve first-candidate order without duplicates. `visible_tool_names` includes only non-null public tool names and never exposes `decision.select` or the internal `answer`, `suggestion`, `clarification`, or `report` kinds. Implement `BoundedDecisionSession` with constants `MAX_PROVIDER_CALLS = 3`, `MAX_READS = 2`, and `MAX_CORRECTIONS = 2`, plus `prepare_call`, `accept_read`, `reject`, and `packet` methods matching the `DecisionPacket` fields above.

The session retains every controller-built packet and registry for the active call index. Define this exact lifecycle: `prepare_call(current_context)` first consumes one call, then builds and seals the packet using the new `call_index` and post-consumption `provider_calls_remaining`, stores its hash/registry under `(agent_run_id, call_index)`, and returns that immutable packet to the provider. Providers select only a candidate; they never supply call identity or packet hashes. After the transport returns, the controller-owned adapter attaches `call_index` and `packet_sha256` from the sealed invocation when it constructs `NormalizedDecision`; it rejects any provider-authored attempt to supply or override those identity fields. `resolve_decision(agent_run_id, normalized, current_context) -> DecisionResolution` first compares those adapter-attached values with the stored sealed packet and resolves the selected candidate from that stored registry. It then compares freshly read repository state/cursor with the sealed packet, rebuilds current legality, and returns the controller-owned current packet/candidate only if identity and legality still match. A hash that does not match the sealed packet raises `PACKET_HASH_MISMATCH`; a repository version/cursor change raises `STALE_STATE_VERSION`; a removed or changed candidate raises `STALE_CANDIDATE`. Task 4 forwards this method through `bind_context` and `ArchitectureRouter`. The returned `DecisionResolution.packet` is the only packet passed to a finalizer.

Tests cover valid calls 1, 2, and 3, prove each call is consumed exactly once before sealing, distinguish a forged hash from a valid response made stale by a later state change, reject an attempted provider override of call index/hash as malformed extra fields, and prove a fourth `prepare_call` is refused.

`normalize_call` requires exact identity-bearing arguments and ignores only a bounded free-text `reason`. A repeated read with the same tool, arguments, state version, and cursor raises `DUPLICATE_READ`. `prepare_call` refuses a fourth call with `CALL_BUDGET_EXHAUSTED`.

- [ ] **Step 4: Build inspection candidates from current legal state**

`build_inspection_registry(request, context, session)` maps each legal catalogue plan to kind `proposal`, the canonical legal finish candidate to kind `escalation`, and exposes `read:state.get_summary` or `read:state.get_recent_events` candidates only when the request/context state version or event cursor is explicitly stale, missing, or contradictory. A normal current `SELECT_PLAN` packet offers no read. An unsupported computer-count request offers immediate escalation because neither inspection read can create survey evidence.

- [ ] **Step 5: Run the session tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_decision_session.py tests/coordinator/test_inspection_decisions.py
```

Expected: all pass.

- [ ] **Step 6: Commit the session core**

```powershell
rtk git add src/sentinel/coordinator/decision_session.py src/sentinel/coordinator/inspection_decisions.py tests/coordinator/test_decision_session.py tests/coordinator/test_inspection_decisions.py
rtk git commit -m "feat: add bounded deterministic decision sessions"
```


### Task 3: Normalize Jev and LLM over the same candidates

**Files:**
- Modify: `src/sentinel/coordinator/proposer.py`
- Modify: `src/sentinel/decision_prompt.py`
- Modify: `src/sentinel/adapter/llm/prompt.py`
- Modify: `src/sentinel/adapter/llm/openai_compatible.py`
- Create: `src/sentinel/adapter/llm/decision_selector.py`
- Create: `tests/coordinator/test_bounded_proposers.py`
- Modify: `tests/agent/test_prompt_context.py`
- Modify: `tests/agent/test_history.py`

- [ ] **Step 1: Add failing parity and validation tests**

Feed identical registries to both selectors and assert they expose the same candidate IDs and semantics. For Jev, cover every kind, scalar-confidence fallback, invalid probability set, low confidence, thin margin, explicit escalation, and low-confidence recovery subset. For LLM, cover exact call normalization, argument mismatch, unknown candidate, illegal tool, stale candidate ID, and active-tool schema filtering.

Assert both selectors receive the same `BOUNDED_DECISION_INSTRUCTION_V1`: answer only from sufficient authoritative facts; propose only for explicit action intent and complete constraints; read only for a declared resolvable gap; clarify one material ambiguity; suggest only legal alternatives with every unmet constraint; report only from current matching results; otherwise escalate. It forbids closest-match proposals, redundant reads, and treating reads as evidence creation.

The LLM-facing tool set for a bounded decision is exactly one internal transport function:

```json
{
  "name": "decision.select",
  "parameters": {
    "type": "object",
    "properties": {
      "candidate_id": {"type": "string", "enum": ["<active IDs only>"]},
      "reason": {"type": "string", "maxLength": 512}
    },
    "required": ["candidate_id"],
    "additionalProperties": false
  }
}
```

`decision.select` is an adapter-internal provider schema: it is absent from the public wire contract, `AgentRequest.legal_tools`, and `AgentLoop` dispatch. The adapter resolves the selected ID to the controller-owned candidate and returns `NormalizedDecision`; it does not manufacture an `AgentModelResult` for internal answer/suggestion/clarification/report selections.

- [ ] **Step 2: Add failing call-budget tests**

Assert every provider attempt consumes one call. A low-confidence selection on call 1 can receive a restricted recovery menu; on call 3 it records `recovery_offered=false`, `reason=CALL_BUDGET_EXHAUSTED`, and returns terminal escalation without another adapter invocation. Provider failure or malformed Jev probabilities escalates immediately.

- [ ] **Step 3: Run tests and verify the one-shot classes cannot satisfy them**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_bounded_proposers.py tests/agent/test_prompt_context.py tests/agent/test_history.py
```

- [ ] **Step 4: Implement `BoundedJevSelector`**

Add `BOUNDED_DECISION_INSTRUCTION_V1` and its revision in neutral `sentinel.decision_prompt`. `BoundedJevSelector` calls `judge.choice` with `{candidate_id: description}`, the packet as state, that instruction, and a revisioned name. It applies the existing finite-probability/top-label/confidence/margin gates to every kind. It writes one `decision_attempt` per provider call with candidate IDs, legal tools, raw/normalized choice, kind, confidence/probabilities/margin, call/read/correction indices, latency, gate result, recovery flag, and sanitized category.

- [ ] **Step 5: Implement `BoundedLlmSelector`**

It passes the packet through `decision_selector.py`, exposes the one `decision.select` schema whose enum is the active candidate IDs, resolves exactly one returned ID through `CandidateRegistry`, and returns `NormalizedDecision`. It writes the same `decision_attempt` fields except probability metrics are null and never invents a confidence score. Only a later loop branch converts a selected read/proposal/escalation candidate to its public call.

- [ ] **Step 6: Bound adapter history by normalized decisions/results**

Retain at most the session’s three calls and corresponding typed results. Store stable candidate/tool IDs and bounded summaries rather than raw provider payloads. `forget(agent_run_id)` clears this transient history while durable clarification records remain.

- [ ] **Step 7: Run selector and adapter tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_bounded_proposers.py tests/agent/test_prompt_context.py tests/agent/test_history.py tests/agent/test_openai_compatible.py
```

Expected: all pass.

- [ ] **Step 8: Commit selector parity**

```powershell
rtk git add src/sentinel/decision_prompt.py src/sentinel/coordinator/proposer.py src/sentinel/adapter/llm/prompt.py src/sentinel/adapter/llm/openai_compatible.py src/sentinel/adapter/llm/decision_selector.py tests/coordinator/test_bounded_proposers.py tests/agent/test_prompt_context.py tests/agent/test_history.py
rtk git commit -m "feat: normalize Jev and LLM bounded decisions"
```


### Task 4: Feed deterministic refusals and gateway errors through AgentLoop

**Files:**
- Modify: `src/sentinel/coordinator/architecture.py`
- Modify: `src/sentinel/coordinator/agent_loop.py`
- Modify: `src/sentinel/coordinator/policy.py`
- Modify: `src/sentinel/coordinator/command_gateway.py`
- Create: `tests/coordinator/test_bounded_agent_loop.py`
- Modify: `tests/coordinator/test_multi_tool_rejection.py`
- Modify: `tests/coordinator/test_agent_loop_context.py`
- Modify: `tests/coordinator/test_idempotency.py`

- [ ] **Step 1: Add failing recovery tests**

Cover invalid public result, invalid internal normalized decision, mismatched agent run, forged/stale packet hash, changed candidate registry, illegal tool, unknown candidate, argument mismatch, duplicate read, read budget exhausted, stale state version, and gateway rejection. Each first refusal must produce a `decision.validate` previous result with the exact sanitized code and refreshed legal tools; a corrected second decision may succeed. A refusal on call 3 ends `NEEDS_REVIEW`. Architecture F must not run.

- [ ] **Step 2: Add pending-approval and read tests**

Assert an accepted `read` decision executes through the existing typed read path, reaches the next request as `previous_result`, refreshes state/cursor/candidates, and cannot repeat unchanged. Assert an accepted `proposal` that enters `WAITING_FOR_APPROVAL` makes zero further provider calls. Assert internal `decision.select` and response-kind names never enter `AgentToolCall`, `legal_tools`, provider workflow maps, or the gateway.

- [ ] **Step 3: Run loop tests and verify router fallback/current fake feedback**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_bounded_agent_loop.py tests/coordinator/test_multi_tool_rejection.py tests/coordinator/test_agent_loop_context.py
```

- [ ] **Step 4: Make router refusals typed**

During bounded mode, `ArchitectureRouter` validates normalized run identity and either returns `NormalizedDecision`, returns canonical escalation for provider unavailability/exhausted gate, or raises `DecisionRejected`. It forwards `resolve_decision` to the bound selector/session so the loop can obtain the current controller-owned packet and candidate. Retain one-shot mode for the evaluation comparison arm. Remove same-turn F fallback only from bounded mode; architecture F remains unchanged as its own policy.

If a read refresh changes the decision point, rebuild the registry. If approval is now pending, return the approval wait without a provider call. Otherwise send `STALE_STATE_VERSION` feedback with the refreshed candidates; never continue the bounded E trajectory through F.

- [ ] **Step 5: Branch on `NormalizedDecision` before public wire parsing**

Immediately after the policy returns, test `isinstance(raw_result, NormalizedDecision)` before calling `.get` or `AgentModelResult.from_dict`. Require matching agent identity, then call the policy's `resolve_decision` with freshly loaded repository context. That hook verifies the sealed packet before checking current state/legality and returns a trusted `DecisionResolution`; never use provider-carried candidate data. Convert only resolved `read`, `proposal`, and `escalation` candidates to controller-owned public calls. Route resolved `answer`, `suggestion`, `clarification`, and `report` candidates to an optional injected `decision_finalizer(DecisionResolution) -> DecisionFinalization`; `AgentLoop` returns that finalization immediately without gateway dispatch. If no finalizer is configured, raise `DecisionRejected` with code `ILLEGAL_DECISION_KIND` and summary `no deterministic finalizer is configured for this decision kind`. No internal kind or `decision.select` name enters the public wire parser.

- [ ] **Step 6: Handle `DecisionRejected` separately in AgentLoop**

Construct feedback with:

```python
AgentToolResult.from_dict({
    "contract_version": "sentinel.v0",
    "wire_type": "agent_tool_result",
    "tool_name": "decision.validate",
    "status": "REJECTED",
    "error_code": rejection.error_code,
    "state_version": current_state_version,
    "event_cursor": cursor,
    "legal_tools": active_registry.visible_tool_names(),
    "safe_summary": rejection.safe_summary,
    "result": {"kind": "empty"},
})
```

Use the same correction counter as schema and gateway failures and the same three-call ceiling. Schema-invalid output must use `decision.validate`, not fake `state.get_summary` feedback.

Add an optional policy lifecycle hook and forward it through `bind_context` and `ArchitectureRouter`:

```python
def observe_result(agent_run_id: str, result: Mapping[str, Any]) -> None:
    session = self._sessions.get(agent_run_id)
    if session is not None:
        session.observe_result(result)
```

Call it immediately after every read, validation refusal, and gateway result, including a rejection on provider call three. Then decide whether another provider call is allowed. This preserves the final failure in session audit/transcript even when no fourth turn exists.

- [ ] **Step 7: Revalidate reads and proposals immediately before execution**

Treat the Step 5 `resolve_decision` call as the immediate pre-execution repository/registry refresh: require the candidate ID and identity-bearing arguments to match that returned resolution. Change `command_for` to accept `expected_state_version=resolution.packet.state_version`. Put that value in the top-level command envelope, where the gateway already reads it, and include it in idempotency-key derivation; the gateway's version guard closes the race after resolution. Tests inspect the submitted envelope and prove a refreshed retry gets a different key. Return gateway rejection as typed feedback when budget remains.

- [ ] **Step 8: Run loop, router, and gateway tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_bounded_agent_loop.py tests/coordinator/test_architecture.py tests/coordinator/test_agent_loop_context.py tests/coordinator/test_multi_tool_rejection.py tests/coordinator/test_command_guardrails.py tests/coordinator/test_idempotency.py tests/coordinator/test_policy.py
```

Expected: all pass.

- [ ] **Step 9: Commit honest multi-turn feedback**

```powershell
rtk git add src/sentinel/coordinator/architecture.py src/sentinel/coordinator/agent_loop.py src/sentinel/coordinator/policy.py src/sentinel/coordinator/command_gateway.py tests/coordinator/test_bounded_agent_loop.py tests/coordinator/test_multi_tool_rejection.py tests/coordinator/test_agent_loop_context.py tests/coordinator/test_idempotency.py
rtk git commit -m "feat: return decision refusals to bounded selectors"
```


### Task 5: Extend inspection evaluation for trajectories

**Files:**
- Modify: `src/sentinel/proposal_eval.py`
- Modify: `scripts/proposal_sweep.py`
- Create: `fixtures/proposal_eval/bounded-inspection-v1.json`
- Create: `docs/superpowers/specs/contracts/proposal-recording-v1.schema.json`
- Modify: `docs/evaluation.md`
- Modify: `tests/test_proposal_eval.py`
- Modify: `tests/test_proposal_sweep.py`

- [ ] **Step 1: Add failing scorer cases**

Prove these cannot score as success: irrelevant/redundant read, correction counted as first-pass success, fourth provider call, missing decision-attempt/read audit, F fallback credited to E, and gateway/approval bypass. Add positive cases where a declared stale state or event-cursor gap is resolved by one read and changes the final proposal or escalation.

- [ ] **Step 2: Run evaluator tests and verify missing trajectory metrics**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_proposal_eval.py tests/test_proposal_sweep.py
```

- [ ] **Step 3: Record bounded-mode manifest fields**

Add protocol revision, decision prompt revision, maximum provider calls/session, read/correction limits, and one-shot/bounded arm labels. Make call-budget computation use three calls per non-approval-wait bounded episode; retries consume the same budget.

- [ ] **Step 4: Add separate trajectory metrics**

Report read relevance/redundancy, evidence changes, state/cursor version grounding, first-pass success, recovered success, provider calls, accepted reads, corrections, latency, escalation, proposal accuracy, unsupported false proposals, and approval safety with explicit denominators. Demo answer, clarification, suggestion, and report metrics are added only in the dependent demo plan.

- [ ] **Step 5: Run scripted and recorded comparisons**

Use cases that genuinely omit a needed fact before the read. Run one-shot and bounded modes with identical model identity, base semantic prompt, cases, repeats, budgets, and legal final candidates. Label scripted/recorded results as harness/reproducibility evidence only.

Create the fixed 12-case `bounded-inspection-v1` fixture. It covers a current packet requiring no read, stale summary and stale-event gaps resolved by one read, a read that changes the final decision, duplicate read refusal, unknown candidate recovery, argument mismatch recovery, gateway rejection followed by a different legal proposal, unsupported request escalation, provider failure, malformed response, and pending approval. The pending-approval case has a zero-call expectation; no case permits a fourth call.

Define a versioned recording manifest schema containing source run/manifest hashes, source code revision/dirty state, arm, requested/resolved model identity and verification state, prompt/tool-policy revisions, capture time, sanitization revision, ordered case/call indices, request digest, response digest, and archive hash. Add a capture command that accepts an existing completed source artifact, verifies every request/case/call against its source manifest, removes credentials and transport headers, writes a new immutable sanitized archive plus recording manifest, and refuses missing, reordered, invented, or unused responses. Never hand-author provider responses or create a recorded archive merely to satisfy this gate. If no validated source recording exists, report recorded comparison as blocked coverage.

Add the `--gate-profile bounded-inspection-v1` path to `proposal_sweep.py`. It runs the bounded `llm` and `jev` arms through architecture E over the exact fixture once in scripted mode, writes manifests/raw streams/database snapshots/rendered reports under `artifacts/proposal_eval/bounded-inspection-gate/<manifest_sha256>/`, and produces one `gate.json` containing `gate_applicable`, `gate_passed`, fixture hash, component-manifest hashes, and failure reasons. `gate_applicable` is true only for the exact scripted profile. `gate_passed` is true only when every declared terminal outcome and accepted effect matches, all required audits exist, the approval-wait case makes zero calls, and totals show zero fourth calls, redundant accepted reads, F fallback credit, approval bypass, unsupported false proposals, missing terminal records, and unhandled timeouts. A non-applicable or failed gate exits nonzero when `--require-gate` is present. Recorded comparison has separate `recorded_applicable`/`recorded_passed` fields and cannot authorize the phase unless its source manifest validates; its absence does not turn into a pass.

Run and retain the exact authorizing artifact:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/proposal_sweep.py --gate-profile bounded-inspection-v1 --fixture fixtures/proposal_eval/bounded-inspection-v1.json --mode scripted --arms llm jev --architecture E --protocol bounded --repeats 1 --output-root artifacts/proposal_eval/bounded-inspection-gate --require-gate
```

If a validated captured archive exists, run recorded mode only with the exact archive and recording-manifest paths emitted by the capture command and retain its separate report. Do not place a placeholder recorded command in the authorizing instructions.

- [ ] **Step 6: Commit trajectory evaluation**

```powershell
rtk git add src/sentinel/proposal_eval.py scripts/proposal_sweep.py fixtures/proposal_eval/bounded-inspection-v1.json docs/superpowers/specs/contracts/proposal-recording-v1.schema.json docs/evaluation.md tests/test_proposal_eval.py tests/test_proposal_sweep.py
rtk git commit -m "feat: evaluate bounded decision trajectories"
```


### Task 6: Serial verification and iterative independent review

**Files:**
- Update after evidence exists: `docs/reports/inspection-capability-baseline.md`
- Authorizes on success: `docs/superpowers/plans/2026-09-21-bounded-multiturn-demo-qa.md`

- [ ] **Step 1: Run all focused bounded-protocol tests serially**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/contracts/test_schema_validation.py tests/agent/test_model_visible_results.py tests/coordinator/test_decision_session.py tests/coordinator/test_inspection_decisions.py tests/coordinator/test_bounded_proposers.py tests/coordinator/test_bounded_agent_loop.py tests/coordinator/test_architecture.py tests/coordinator/test_agent_loop_context.py tests/coordinator/test_idempotency.py tests/test_proposal_eval.py tests/test_proposal_sweep.py
```

Expected: all pass.

- [ ] **Step 2: Run the entire serial suite**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short
```

Expected: exit code 0; retain counts, skips, warnings, duration, and exact revision.

- [ ] **Step 3: Dispatch a specification reviewer**

Ask a fresh reviewer to map the bounded inspection/session requirements to code and a meaningful test, with special attention to run identity, internal/public transport separation, read suppression, low-confidence call-three behavior, typed refusal recovery, state-version idempotency, approval waits, and no F masking. Fix each confirmed important gap and rerun focused tests.

- [ ] **Step 4: Dispatch a separate quality reviewer**

Ask a fresh reviewer to inspect state/version races, idempotency, schema compatibility, audit completeness, secret leakage, transcript bounds, migration behavior, and scorer gaming. Fix every confirmed important issue in a new commit and rerun focused plus full tests.

- [ ] **Step 5: Repeat reviews until clear**

After fixes, send the updated diff and test evidence to fresh review/fix agents. Stop only when neither review reports an important unresolved issue. Record minor deferred observations with a reason; never classify a correctness or safety gap as minor.

- [ ] **Step 6: Publish bounded inspection evidence**

Update the inspection report with one-shot versus bounded scripted/recorded results, explicit limitations, and links to retained artifacts. Do not claim live bounded quality until a separately authorized live manifest is executed. Record the passing `gate.json` path, manifest digest, and exact command that authorize the dependent demo-conversation plan; integrated survey evidence remains separate.
