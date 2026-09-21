# Proposer escalation and bounded multi-turn design

**Status:** approved direction; written specification pending operator review  
**Target branch:** `feat/proposer-bounded-multiturn`, based on `integration/survey-demo` at `d354372`  
**Date:** 2026-09-21

## Goal

Make Jev and the LLM safe, comparable decision selectors over the same deterministic workflow.

The work lands in two sequential increments:

1. Both proposers can explicitly say that no catalogue plan fully satisfies the request. Prompts require complete constraint satisfaction rather than the nearest available plan.
2. Both proposers can make a bounded sequence of read decisions before selecting one prebuilt final action or escalating. Validation failures and rejected tool calls return as typed feedback to the next turn instead of being hidden by architecture-F fallback.

The gateway, persistent repository, legal-set computation, and operator approval remain authoritative. A model never constructs an unrestricted mutation and never executes an approved operation.

## Current defects

At `SELECT_PLAN`, `legal_set()` includes catalogue plans and a canonical `finish(NEEDS_REVIEW)` escalation candidate. `JevProposer` filters that set to `inspection.propose_plan`, then asks Jev for the plan that "best satisfies" the request. Unsupported, contradictory, or only partially satisfiable requests therefore force Jev to choose a plan.

`LlmProposer` advertises `finish`, but its acceptance function recognizes only `inspection.propose_plan`. `ArchitectureRouter` also accepts only a plan. A model-authored `finish(NEEDS_REVIEW)` is consequently treated as an illegal tool and replaced by architecture F.

The generic `AgentLoop` already feeds schema and gateway failures into `previous_result`, but router-level refusals never reach it. The router silently invokes F on the same turn. This masks model errors in evaluation and prevents the proposer from correcting its decision.

The LLM prompt builder also sends all approved tool definitions even when the request's legal-tool list is narrower. Provider-visible tools and deterministically legal tools can therefore disagree.

Survey has the same semantic gap. Its Jev proposer removes escalation and asks for
the sweep that "best answers" the request. Its LLM proposer has validation logic
but no production survey-specific provider prompt or adapter; current E/J CLI
modes use scripted stand-ins. The survey loop is multi-stage, but it automatically
injects site and result payloads rather than letting either selector dispatch the
advertised reads.

## Invariants

- The repository and gateway are the only authoritative workflow state.
- Every provider turn is bound to a `run_id`, `agent_run_id`, and observed `state_version`.
- Only the deterministic layer creates tool arguments that can mutate workflow state.
- A final proposal is revalidated against the current legal candidates before it reaches the gateway.
- Every plan or sweep remains pending until explicit operator approval.
- While approval is pending, the loop makes zero provider calls.
- Architecture F remains available as its own comparison arm. Architecture E does not silently use F to turn a rejected model decision into apparent success.
- Provider failures, malformed outputs, decisions, read attempts, deterministic refusals, retries, and terminal outcomes are all audited with sanitized categories.
- No live provider batch runs automatically.
- Every live path verifies manifest authorization and provider-specific consent
  before provider construction, output-directory creation, database creation, or
  any artifact write.

## Increment 1: explicit escalation and complete constraints

### Shared terminal meaning

The shared semantic outcomes at a proposal decision are:

- select one currently legal, prebuilt final candidate; or
- escalate to the operator because none fully satisfies the request or the decision cannot be made safely.

On the wire, escalation remains the existing single call:

```json
{
  "tool_name": "finish",
  "arguments": {
    "status": "NEEDS_REVIEW",
    "summary": "plan proposal needs operator review"
  }
}
```

`proposal_recommendation` remains plan-only so the pending-review display retains its meaning. `proposal_attempt` records both plan and escalation attempts, including the raw model choice, final policy label, confidence data where available, gate outcome, latency, and sanitized error category.

### Jev behavior

`JevProposer` receives every legal plan plus the canonical escalation candidate. Its fixed-choice instruction becomes:

> Choose a catalogue plan only when its described capabilities fully satisfy every explicit operator constraint and requested deliverable. Partial, approximate, or merely closest available coverage does not satisfy the request. Choose escalation when no candidate fully satisfies it, the constraints conflict, or a required capability or fact is unavailable. Use only the supplied facts.

An explicit, adequately confident escalation is recorded as an accepted Jev decision whose final policy label is `escalate`. Low confidence, a thin margin, malformed probability data, or provider failure still fails closed to escalation, but the audit distinguishes those gate-driven escalations from an explicit model choice.

### LLM behavior

`LlmProposer` receives only provider tool definitions that are legal for the current decision. It accepts either:

- one `inspection.propose_plan` call matching a current candidate; or
- one `finish` call with status `NEEDS_REVIEW`.

The system prompt uses the same complete-satisfaction rule as Jev. It must not describe `finish(NEEDS_REVIEW)` as inappropriate merely because an unfulfilled deliverable exists; it is correct when no legal action fully satisfies that deliverable.

The policy canonicalizes the escalation summary before returning it. Provider-authored text is retained only in bounded audit context, not used as executable input.

### Router behavior

`ArchitectureRouter` recognizes explicit escalation as a valid terminal proposer outcome and forwards the proposer's already-canonical `TOOL_CALLS/finish` result. `AgentLoop` translates that call into terminal `NEEDS_REVIEW`. The router writes an `architecture_route` record with a distinct `proposer_escalation` route.

During this first increment, all existing approval and plan validation stays in place. Invalid results still fail closed. Increment 2 replaces silent F fallback for correctable proposer decisions with typed feedback.

### Increment 1 acceptance cases

- Jev is offered escalation alongside every current plan.
- An unsupported computer-counting request can select escalation and cannot be counted as a successful plan proposal.
- Ambiguous, contradictory, incomplete-evidence, and only-partially-covered requests can select escalation.
- A supported request can still select each approved catalogue plan.
- The LLM's valid `finish(NEEDS_REVIEW)` is accepted rather than classified as an illegal tool.
- The router never invokes F after an explicit proposer escalation.
- Provider-visible LLM tools equal the legal proposer tool set.
- Existing confidence, finite-probability, legal-label, and margin gates continue to apply.
- Every attempt is audited; only accepted plans create `proposal_recommendation`.
- The scorer classifies an accepted explicit escalation as an escalation decision,
  not an accepted plan or unsupported-request false proposal.

## Increment 2: shared bounded decision session

Increment 2 is blocked on the live escalation gate below. Passing unit, scripted,
or recorded tests is necessary but does not authorize multi-turn implementation.

### Deterministic session, not duplicate world state

A `BoundedDecisionSession` is controller state for one `agent_run_id`. It does not copy workflow truth. On each turn it projects the current repository and legal set into a versioned decision packet:

```text
DecisionPacket
  run_id
  agent_run_id
  state_version
  operator_request
  decision_point
  final_candidates[]
  read_candidates[]
  transcript[]
  reads_remaining
  corrections_remaining
  provider_calls_remaining
```

The packet contains bounded descriptions and stable candidate IDs. A final candidate maps to an existing legal-set `Candidate`. A read candidate maps to one exact read tool with controller-owned arguments. The session transcript contains bounded typed results and validation feedback; provider payloads and credentials are excluded.

The session is cleared through the existing `forget(agent_run_id)` boundary on every terminal return, approval wait, deadline, emergency, or exhausted budget.

### Normalized candidate protocol

Both selectors operate over the same candidate registry:

```text
READ:<stable-id>     -> exact whitelisted read call
ACTION:<stable-id>   -> exact current legal final action
ESCALATE             -> canonical finish(NEEDS_REVIEW)
```

Jev receives those IDs as fixed-choice criteria and returns one ID with probabilities. It does not invent a tool name or arguments.

The LLM receives schemas only for tools represented by active candidates. Its tool call is normalized to an active candidate ID. Identity-bearing arguments must match the prebuilt candidate. A free-text rationale may be accepted, bounded, and audited, but it cannot change the candidate's executable arguments.

This makes transport different while authority remains equal:

- Jev transport: fixed-choice response to a deterministic candidate menu.
- LLM transport: tool call normalized to the same deterministic candidate menu.

### Shared read, act, or escalate instruction

The models are expected to know general tool-use patterns, but they cannot infer
Sentinel's local meaning of a legal candidate, approval boundary, result freshness,
or capability limit. Both selectors therefore receive the same short decision
rule rather than a catalogue of scenario-specific examples:

> Choose one currently legal final action only when the supplied current facts
> fully support it and it satisfies every stated constraint and requested
> deliverable. Otherwise choose one legal read only when its declared result could
> materially resolve a specific missing, stale, or contradictory fact required
> for a final decision. Otherwise escalate. Escalate when the request needs an
> unavailable capability or its constraints cannot all be satisfied. Do not choose
> a closest or partial match, do not read merely because a read is available, and
> do not repeat a read without a relevant state or version change. A read reveals
> facts; it does not create evidence or capability. After rejected or failed
> feedback, reassess the current facts and remaining candidates.

Candidate descriptions state what each read returns and its freshness/version.
The packet marks facts as current, stale, absent, or contradictory. This lets the
selector apply the general rule without guessing whether another read can help.
The deterministic candidate builder offers a read only when the packet declares
the gap it can resolve. Jev does not produce a free-form explanation of why a read
is useful, so confidence and margin alone are not treated as proof of materiality.

Prompt tests assert the decision semantics, not exact prose. Behavioral tests pin
the important distinctions:

- sufficient current facts select a final action without a redundant read;
- a useful read is selected before escalation when it can resolve the named gap;
- unsupported capability or irreconcilable constraints escalate even when reads
  remain available;
- after a read, the selector reassesses rather than repeating it;
- exhausted, failed, or irrelevant reads lead to escalation rather than an
  unsupported final action.

### Initial inspection reads

The first implementation exposes the two existing read tools:

- `state.get_summary` with `{}`;
- `state.get_recent_events` with controller-owned `after_sequence` and a bounded `limit`.

A read is offered only when legal and not already satisfied by the session transcript. Repeating an identical read without a state or cursor change is refused. Additional catalogue, evidence, site, or results reads require their own typed repository interfaces and scenarios proving that the initial packet omits decision-relevant information; they are not simulated by prompt text.

This means the first multi-turn implementation primarily handles reconciliation, concurrent state changes, and corrective feedback. It is not expected to improve cases where the initial packet already contains all relevant facts.

At the current inspection `SELECT_PLAN` point, `state.get_summary` and
`state.get_recent_events` normally repeat facts already present in the request.
They are therefore suppressed unless the packet identifies a stale, missing, or
contradictory state/event fact that the read can resolve. An unsupported request
such as computer counting cannot become supported through these reads and must
escalate immediately.

### Turn and budget rules

- At most two accepted reads occur before a terminal decision.
- A normal path therefore uses at most three productive provider calls: two reads and one action or escalation.
- The existing shared correction budget permits at most two retries, but retries
  are inside the provider-call ceiling rather than additional to it.
- The hard provider-call ceiling per decision session is three.
- Every malformed decision, deterministic refusal, and gateway rejection consumes
  one correction and one provider call. A rejected early call therefore reduces
  how many reads remain possible in that session.
- A refusal on provider call three is persisted and audited, but no fourth
  provider turn exists to consume it; the session terminates `NEEDS_REVIEW`.
- After two accepted reads, only final actions and escalation are offered.
- After the correction budget or hard call budget is exhausted, the session terminates as `NEEDS_REVIEW`; it does not invoke F.
- Provider unavailability and unusable Jev probability output escalate immediately because no actionable choice exists to correct.

### Typed deterministic feedback

Router and candidate-validation failures are returned through `AgentLoop` as a rejected previous result. Introduce `decision.validate` as a result-only `AgentToolResult.tool_name`. It is never legal as an `AgentToolCall`, never appears in `legal_tools`, and is never exposed to a provider as a callable function.

A rejection has this shape:

```json
{
  "wire_type": "agent_tool_result",
  "tool_name": "decision.validate",
  "status": "REJECTED",
  "error_code": "UNKNOWN_CANDIDATE",
  "state_version": 12,
  "event_cursor": 41,
  "legal_tools": ["state.get_summary", "finish"],
  "safe_summary": "selected action is not a current legal candidate",
  "result": {"kind": "empty"}
}
```

The public wire schema admits `decision.validate` only in tool results. Agent tool-call schemas and provider tool maps continue to reject it. This is an additive result-side compatibility change: existing `AgentRequest` and `AgentToolResult` documents continue to validate, and the bounded previous-result renderer preserves the feedback even though it never appears in callable-tool maps.

Sanitized deterministic error codes include:

- `INVALID_AGENT_RESULT`
- `ILLEGAL_TOOL`
- `UNKNOWN_CANDIDATE`
- `ARGUMENT_MISMATCH`
- `DUPLICATE_READ`
- `READ_BUDGET_EXHAUSTED`
- `STALE_STATE_VERSION`
- existing normalized gateway rejection codes

The loop catches a typed internal `DecisionRejected` signal separately from provider exceptions. It constructs the rejected result, assigns it to `previous_result`, increments the correction count, and starts the next turn. The proposer session appends that result to its transcript, rebuilds candidates from current state, and asks the same selector again.

Schema-invalid model output follows this same honest feedback path. It no longer pretends that a `state.get_summary` tool was rejected when no such call occurred.

### Read and action execution

For a read:

1. Revalidate the candidate against the current session and read budget.
2. Execute the existing typed read through `AgentLoop`.
3. Return the accepted `AgentToolResult` through `previous_result`.
4. Append it to the bounded session transcript.
5. Refresh repository state, legal candidates, cursor, and state version before the next selection.

For a final action:

1. Refresh authoritative state.
2. Recompute the legal set.
3. Require the candidate ID and identity-bearing arguments to match a current candidate.
4. Send the canonical call through the gateway.
5. If the gateway rejects it, feed the typed rejection into the next turn.
6. If accepted and approval is pending, stop immediately with zero further model calls.

If final revalidation adds an expected state version to a command, that version
also participates in the idempotency fingerprint. A refreshed retry must not
reuse an idempotency key derived from stale state.

For escalation, return the canonical `finish(NEEDS_REVIEW)` result without invoking the gateway or F.

### Architecture-router role

`ArchitectureRouter` remains the architecture-E boundary, but it no longer converts correctable model mistakes into architecture-F decisions. It performs protocol validation and either:

- forwards a legal read, final action, or escalation;
- raises the typed `DecisionRejected` signal for loop feedback; or
- returns canonical escalation for an unavailable provider or exhausted proposer gate.

Architecture F remains unchanged and separately measurable.

### Auditing

Each provider selection writes one attempt record with:

- session and run IDs;
- state version and event cursor;
- selector (`jev` or `llm`);
- active candidate IDs and legal tool names;
- raw selected label or tool name where safely representable;
- normalized candidate ID and final policy label;
- confidence, probabilities, and margin for Jev;
- gate outcome and sanitized error category;
- call index, reads used, corrections used, and latency.

The stable taxonomy uses `gate_outcome=accepted` with
`decision_kind=plan|read|escalation` for accepted decisions. Accepted-proposal
denominators include only plans. Gate-derived low-confidence, thin-margin,
malformed, and provider-failure escalations remain distinct from explicit model
escalation.

Each read writes an execution record linked to the attempt and resulting audit/event reference. Each deterministic refusal writes its error code and next-turn linkage. Accepted plan recommendations retain their existing record. Evaluation reports model-only outcomes separately from any deterministic or scripted fallback.

## Survey extension

The shared session and candidate protocol must be domain-neutral, but the first production connection is inspection. Survey integration follows after the inspection protocol passes its regression and evaluation gates.

Survey then maps its existing legal candidates into the same action/escalation registry and makes the existing `get_site` and `get_results` functions dispatchable typed reads. Their results must correspond to the selected run, sweep option, and current store state.

For bounded E/J sessions, a read is offered only when its full equivalent payload
is absent, stale, or contradictory. `survey.get_site` is useful at sweep selection
only when the visible option descriptions do not already contain the required
current site facts. `survey.get_results` is useful at result review when the packet
contains a results version/reference rather than the full payload. Selecting a
read executes the real function and places its typed payload in the next turn. F
uses the same dispatcher under its scripted policy without a provider call.

The site packet may already contain enough current option metadata to choose a
sweep; in that case `get_site` is not offered. After a completed sweep, the bounded
path advertises a results version but does not ask the model to infer counts from
lifecycle events. `survey.get_results` returns merged unique-object counts,
uncertainty bands, unanalysed-frame status, and remaining flight budget. The
deterministic validator requires a matching current results version in the session
transcript before accepting a report or follow-up.

The current survey loop already implements the required multi-stage lifecycle by
automatically injecting site and full results. Model-directed reads are not
required by the current implementation. The following is the proposed bounded
tool-using migration:

1. Select a prebuilt sweep, reading site detail first only if the supplied option
   facts do not resolve the request.
2. Present that exact sweep for operator approval.
3. After approval, deterministic flight and perception code captures frames,
   stores sightings, and merges repeated sightings into object hypotheses.
4. On the next policy turn, read the current merged results.
5. Submit a grounded report when the result supports it; propose an approved
   follow-up sweep when a resolvable uncertainty remains within budget; otherwise
   escalate.

The model does not directly dispatch the aircraft, and it does not answer from the
number of raw detections. Several detections may be sightings of the same computer.
The report is grounded in merged unique-object results and preserves lower-bound
or uncertainty semantics.

Once a real survey LLM adapter exists, its prompt adds two domain rules to the
shared instruction:

> A sweep is supported only when current site facts show that its coverage and
> resolvable classes can satisfy the requested survey; option availability alone
> is not proof of whole-room coverage. Submit a report only from current merged
> unique-object results, never a raw detection count. Choose a follow-up only when
> it can resolve a named uncertainty within the remaining flight budget; otherwise
> escalate.

Survey keeps its two-flight budget and requires approval before every sweep. A missing or mismatched replay observation remains unevaluable and cannot be repaired by substituting frames from another option.

## Evaluation environment upgrade for the integrated survey stack

This work targets the current aggregated checkout. The evaluator must not import
or execute the older `feat/survey-counting` worktree. In particular,
`tests/test_survey_sweep.py` and `scripts/survey_sweep.py` must resolve production
modules from the current checkout and record the current integrated commit.

### Canonical evaluator and scene manifest

`src/sentinel/survey_eval.py` becomes the canonical evaluator-owned scoring and
synthetic-geometry reference. `src/sentinel/survey/eval.py` becomes a compatibility
and execution helper that delegates scoring to the canonical implementation.
`scripts/survey_run.py` and `scripts/survey_sweep.py` must no longer select
different scoring semantics.

Introduce a versioned scene-v2 schema with:

- provenance (`synthetic` or `operator_labelled`) and label authority;
- independent truth object IDs, classes, positions, surface planes, physical
  width and height, plus optional explicit per-frame truth boxes;
- surveyed bounds;
- camera intrinsics and calibration hash;
- public sweep-option definitions containing the exact `SweepOption` fields;
- frames indexed by option, stop, and look, with status, pose, pose source,
  artifact/response paths, and hashes;
- occlusion truth, request text, and expected evaluability.

Public sweep candidates must come from the manifest or production option
generation, never from hidden truth. Changing or deleting a truth object must
not change the options shown to a policy. This removes the current bridge's
oracle leakage, where truth-object depth affects policy-visible range and
resolvability metadata.

### Strict integrated replay

`ReplayFlight` must require an option ID on every frame, verify unique
option/stop/look identities and artifact hashes, and raise a typed
`ReplayUnavailable` when the selected option has a missing or unanalysed frame.
It must never assign a missing option ID to the requested option or return an
empty selected sweep as success. Missing nonselected options remain allowed
until selected.

Recorded perception likewise verifies that each response is bound to the exact
selected frame and image hash. Neither flight nor detector replay may substitute
another option, frame, or response.

`ReplayUnavailable` belongs to the production replay boundary in
`sentinel.survey.flight` or a neutral survey error module. The evaluator catches
and classifies it as unevaluable; production flight code never imports evaluator
code.

### Reproducible modes and artifacts

The survey runner supports three explicit execution modes:

- **Scripted:** real loop, store, candidate policies, flight replay, and scorer;
  injected fake selector and detector transports; no quality claim.
- **Recorded:** exact selector and detector response replay with normalized
  request-digest checks; zero network calls; reproducibility claim only.
- **Live:** fresh selector and/or detector calls under manifest-bound consent;
  never automatic and initially repeated three times per labelled case.

`E/live` is blocked until a survey/shared-session provider adapter emits only the
active survey tools and validates normalized request digests. `J/live` is blocked
until the survey candidate/session integration exists. Live Moondream or Gemini
detector arms are blocked until provider endpoints, configuration, and factory
identity have been verified. A blocked configuration is rejected; the runner
never substitutes a scripted default while retaining a live label.

F, E, and J identify decision policies. They do not imply live providers.
Reports therefore use paired labels such as `E/recorded` or `J/live` and call
the existing CLI defaults scripted stand-ins.

Each run creates, without overwriting:

```text
manifest.json
raw.jsonl
summary.json
report.md
episodes/<case-id>/<arm>/<repeat>/episode.sqlite3
episodes/<case-id>/<arm>/<repeat>/snapshot.json
```

The manifest binds the revision and dirty/source hashes; every scene, option,
calibration, image, and response hash; arm and execution mode; selector and
detector providers; requested and resolved model identities; prompt and tool
policy revisions; Jev, observability, merge, and matching thresholds; repeats;
separate selector, detector, and flight budgets; approval script; replay versus
hardware mode; cost assumptions; and consent metadata. Missing resolved identity
is `unverified`.

A dry run validates scenes and recordings and prints the canonical manifest
digest, maximum selector calls,
detector calls, provider configuration, flight budget, and estimated cost. It
makes no provider call and creates no run artifact. Retries consume the relevant
manifest budget. Inspection approval-wait cases require zero calls. The current
synchronous survey loop instead guarantees that, once a proposal reaches its
approval callback, no further selector, detector, or flight call occurs before
the callback's decision. Persisted survey approval-wait/resume semantics are a
separate production feature.

Live execution checks manifest authorization and provider-specific consent before
provider construction, output-directory creation, database creation, or any
artifact write.

The append-only stream preserves episode starts/completions, model requests and
results, reads and results, deterministic refusals, approvals, selected options,
frames, detector requests/results, persistence references, reports, scores, and
sanitized failures. Secrets and image bytes are excluded.

### Stage-separated scoring

All metrics expose numerators, denominators, and `not_applicable` where the
necessary outcome does not exist.

- Workflow: completion, escalation, refusal, report presence, report validity,
  flights and budget use, approval violations, selector calls, retries, latency,
  illegal calls, and rejected calls. Report validity is not applicable when no
  report exists.
- Frame detection: visible-and-resolvable truth against sightings per frame,
  with one-to-one class and box matching, precision, and recall. Projected boxes
  use declared physical width/height and the camera model unless explicit truth
  boxes exist. Matching uses maximum-cardinality class-compatible assignment,
  with the manifest's centre-distance and/or IoU threshold as the deterministic
  acceptance rule and tie-break. Every threshold is recorded in the manifest.
- Merge: split/duplicate hypotheses, two-truth-to-one collapse, merge recall
  conditional on detector evidence, and depth-spread attribution.
- Count: class interval containment and lower-bound correctness. Exact error is
  reported only for exact counts; lower bounds are not silently treated as exact.
- Localisation: one-to-one distance errors (mean, median, p95) and threshold
  recall rather than a single value called localisation.
- Uncertainty: confirmed/uncertain counts, overconfident false hypotheses,
  unresolved visible truth, and correct lower-bound handling.
- Planning: distinct truth visible and resolvable in selected sweeps, additional
  objects revealed by follow-up, redundant follow-up, cumulative coverage, and
  whole-scene coverage within the flight budget. Accuracy on observed objects
  stays separate from whole-scene coverage.
- Attribution: unanalysed frames, class-map/provider misses, geometry disagreement,
  detector misses, merge misses, and policy misses.

When measured pose evidence exists, report pose availability, commanded-versus-
measured XY/heading error, and reprojection/quality bounds. Projection uses the
pose attached to the frame. Commanded-only scenes receive no measured-pose score.

### Integrated capability boundaries

The evaluation upgrade includes software bridge work only when backed by a
typed interface and replay evidence:

- a future `survey/cube_bridge.py` must implement `FlightPort` over
  `CubeMissionRunner` and bind each capture to the selected option, stop, look,
  artifact hash, timestamps, and commanded or measured pose;
- perception code currently living under `fixtures/perception` needs an explicit
  installed `DetectorPort` adapter before it counts as survey production behavior;
- Moondream and Gemini adapters require verified provider endpoints and identities
  before live results support detector-quality claims.

The provider documentation currently refers to an absent
`scripts/provider_spike.py`, so it supplies no live endpoint evidence. Synthetic
frames either reference materialized files with hashes or carry a canonical
synthetic-frame record hash. The evaluator never claims an image hash for a
nonexistent path.

The evaluator does not enable hardware. Hardware coverage remains blocked until
the bridge, calibration, measured independently labelled scene, location/filming
consent, and separate flight authorization exist. Synthetic scenes establish
harness, projection, replay, merge, and scoring behavior only.

### Evaluation-upgrade acceptance cases

- Tests import production code from integrated `HEAD`, not another worktree.
- Perturbing hidden truth cannot change policy-visible sweep candidates.
- Missing, wrong-option, duplicate, unanalysed, or hash-mismatched selected
  frames are unevaluable; no substitution occurs.
- Scripted and recorded F/E/J arms use fresh stores and identical scene/options.
- Recording request mismatch, exhaustion, and unused responses are reported.
- Selector and detector identities are verified, unverified, or mismatched
  explicitly.
- When a declared resolvable site or result gap exists, `survey.get_site` and
  `survey.get_results` dispatch through the bounded session and their current typed
  results reach the next decision. When equivalent facts are already current, the
  redundant read is not offered.
- Approval occurs before every flight, and the declared flight budget is enforced.
- Empty databases, missing raw records, rejected final decisions, exhausted
  budgets, and absent reports cannot become successes.
- Several predictions cannot match one truth object; merging two truth objects
  into one is reported as its own error.
- A separate survey-readiness report lists real-scene accuracy, live detector
  evidence, cube bridge, and hardware flight evidence as blocked until measured.

Existing callers of `sentinel.survey.eval.Scene`, `score_episode`, and
`compare_modes` are either preserved through explicit compatibility wrappers or
migrated together with their tests in one commit. The canonical evaluator is not
silently re-exported behind incompatible signatures.

## Testing and delivery order

Implementation follows test-driven, task-sized commits:

1. Add failing tests for explicit Jev and LLM escalation, complete-constraint prompts, exact provider-visible tools, and router acceptance of `NEEDS_REVIEW`.
2. Correct explicit-escalation scoring, implement increment 1, and run focused proposer, router, prompt, flow, and evaluation tests serially.
3. Run the full serial suite, create dry-run manifests, obtain manifest-bound consent, and execute the live escalation gate. Preserve the previous artifacts and publish the comparison.
4. If the live gate fails, stop, diagnose the retained attempts, revise increment 1 under a new prompt revision, and repeat its tests and gate. Do not begin multi-turn work.
5. After the live gate passes, add contract tests for result-only `decision.validate` and rejection rendering.
6. Add failing loop tests for router refusal, illegal tool, unknown candidate, argument mismatch, duplicate read, stale state, gateway rejection, correction recovery, and exhausted budgets.
7. Implement the shared session/candidate registry and Jev/LLM selector adapters.
8. Add end-to-end tests in which each proposer reads, receives the typed result, then chooses a different legal action or escalation.
9. Verify pending approval produces no additional provider call and no final action bypasses approval.
10. Extend the inspection evaluation manifest and raw record stream with turn, read, correction, and call-budget fields.
11. Run scripted and recorded one-shot versus bounded multi-turn inspection arms on identical cases. Cases must genuinely require a read before they support claims about multi-turn quality.
12. Upgrade the integrated survey evaluator, strict replay, scene manifest, execution modes, and stage-separated scoring; then connect survey reads and run its scripted/recorded acceptance baseline.
13. Run the full serial test suite using the repository's RTK-prefixed command.
14. Dispatch independent specification and quality reviews, fix every confirmed item, rerun verification, and only then prepare integration.

## Acceptance criteria

- Unsupported or contradictory inspection requests can be explicitly escalated by Jev and the LLM.
- Neither proposer is forced to choose the closest plan.
- The scorer treats an accepted explicit escalation as an escalation decision,
  never as an accepted plan or unsupported-request false proposal.
- The LLM sees exactly the currently legal provider tools.
- Jev and the LLM receive the same semantic candidate set for the same state.
- Both can perform up to two validated reads and then select a final candidate or
  escalate when no earlier rejection consumed the shared three-call budget.
- Deterministic refusals and gateway rejections appear as typed feedback on the next turn.
- A corrected next-turn decision can succeed within the shared correction budget.
- Exhausted read, correction, turn, or provider-call budgets end in `NEEDS_REVIEW`.
- No router fallback can convert a proposer failure into model success.
- Repeated or stale reads and stale final candidates cannot execute.
- Pending approval causes zero further provider calls.
- All attempts and reads are auditable and replayable without credentials.
- Architecture F behavior and historical evaluation artifacts remain unchanged.

## Evaluation interpretation

Increment 1 must be evaluated before increment 2 so the effect of explicit escalation is visible rather than attributed to multi-turn behavior.

### Live escalation gate before multi-turn

At the operator's explicit 2026-09-21 request, this is a development sequencing
gate before multi-turn work, not a general model-release threshold or broad
reliability claim.

After increment 1 passes the serial test suite, rerun the same 12 generated
synthetic inspection cases and three repeats used by the current baseline. Use the
same resolved Jev and LLM model identities when they remain available. Produce a
new dry-run manifest for each arm, bind consent to that manifest, and retain the
old live artifacts unchanged.

Multi-turn implementation may begin only after the new live report confirms all
of the following:

- all 18 supported-case trials still select the correct catalogue plan for each
  evaluated proposer;
- all 18 escalation-required trials finish as `NEEDS_REVIEW` without an accepted
  catalogue proposal;
- unsupported-request false proposals are 0/18;
- explicit model escalation, confidence-gate escalation, deterministic refusal,
  and architecture-F fallback are reported separately;
- architecture-F fallback is 0 for explicit proposer escalations and cannot be
  credited as proposer success;
- every expected provider attempt and terminal outcome exists in the raw stream;
- the requested and resolved model identities, revision, fixture hash, prompt
  revision, thresholds, call budget, latency, token usage, and cost are reported.

If either proposer misses this gate, stop before increment 2, inspect its raw
attempts, and revise only the escalation contract, candidate descriptions, or
complete-constraint prompt as supported by the evidence. Every revised prompt
gets a new revision and manifest. Do not tune against hidden operator labels or
change catalogue capabilities to make the evaluation pass.

These generated cases establish behavior on this fixed harness only. They are not
independently labelled held-out data and cannot support a general model-quality or
release-readiness claim.

The multi-turn arm is compared with the one-shot arm using identical model identities, prompts except for protocol instructions, cases, repeats, budgets, and legal candidates. Reports show provider calls, accepted reads, rejected decisions, corrections, latency, escalation, proposal accuracy, unsupported-request false proposals, and final workflow outcomes with denominators.

A successful correction after deterministic feedback is reported as a retry, not a first-pass success. A read that merely repeats already injected context does not count as evidence that multi-turn reasoning improved model quality.
