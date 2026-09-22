# Trajectory-first inspection evaluation design

**Status:** approved for specification; implementation requires a separate plan  
**Target branch:** `integration/survey-demo`  
**Date:** 2026-09-22  
**Base reviewed:** `63ed991` on `integration/survey-demo` (`66ee3a5` is an ancestor)

## Decision

Sentinel will expand its inspection evaluation before implementing a bounded multi-turn proposer runtime. The new benchmark measures the ordered decisions, reads, deterministic feedback, state transitions, and terminal outcome of a decision session. It is not evidence that multi-turn behavior should ship by itself.

The current one-shot results answer a narrower question. Architecture F completed all 18 supported synthetic requests. Jev, used as the proposer inside architecture E, made the correct raw semantic choice on all 36 trials but five supported choices did not pass its confidence gate. The LLM arm completed all 18 supported trials but made two unsafe unsupported proposals. None of those failures required a state read, and the current fixtures do not contain a decision-relevant fact that becomes available on a later turn. This evidence does not justify adding runtime turns.

The trajectory benchmark therefore lands in two evaluation stages:

1. A reference executor, scripted fixtures, scorer, and recorded-replay path prove that the contract detects correct and incorrect trajectories without changing production behavior.
2. Only if those fixtures demonstrate useful trajectories does a later, separately approved implementation plan connect the contract to a bounded runtime and run fresh providers.

This design phase changes no production files, prompts, tool policy, schemas, or provider configuration. It performs no provider calls.

## Scope

The first benchmark covers inspection decisions over the existing repository, approved inspection catalogue, `AgentLoop`, architecture router, and gateway harness. It tests only the two reads already exposed by the inspection agent contract:

- `state.get_summary` with `{}`;
- `state.get_recent_events` with controller-owned `after_sequence` and `limit` arguments.

It does not add catalogue reads, free-form database queries, survey result reads, room-question answering, clarification, suggestion, or report-finalization behavior. Those capabilities need their own fixtures and typed interfaces.

The benchmark distinguishes architecture from proposer transport:

- **F** is the deterministic baseline with a declared scripted operator policy. It makes zero provider calls.
- **E/Jev** is architecture E with `JevProposer` as its selector. There is no separate inspection architecture J.
- **E/LLM** is architecture E with `LlmProposer` as its selector.

The arm identity is recorded as two fields, `architecture` and `selector`, so a report cannot call E/Jev “architecture J.” Survey may compare F/E/J under its own architecture contract; that naming does not alter the inspection arms.

## Benchmark unit

The benchmark introduces `TrajectoryCase` alongside the current one-shot `Case`. A trajectory case declares initial repository state, the knowledge intentionally presented to the selector, deterministic changes that occur at named boundaries, the exact sequence of accepted or refused decisions, and the final persisted outcome.

The fixture bundle version is `inspection-trajectories.v1`. Unknown versions, unknown fields, duplicate case IDs, duplicate step ordinals, or unrecognized enum values invalidate the entire bundle before any case runs.

```jsonc
{
  "version": "inspection-trajectories.v1",
  "catalogue": {
    "id": "SURVEY_CATALOGUE_V1",
    "revision": "REV_1",
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "cases": [
    {
      "case_id": "current_supported_route",
      "request": "Inspect the two route-only alley stations without marker verification.",
      "label_status": "synthetic_declared",
      "provenance": "trajectory_contract_v1",
      "applicable_arms": ["F", "E_JEV", "E_LLM"],
      "setup": {
        "scenario": "new_run",
        "state_version": 1,
        "event_cursor": 1,
        "authoritative_facts": {
          "run_status": "RUNNING",
          "decision_point": "SELECT_PLAN",
          "request_revision": 1
        },
        "presented_state_version": 1,
        "presented_event_cursor": 1,
        "mutations": []
      },
      "expected_by_arm": {
        "E_JEV": {
          "budgets": {
            "provider_calls": 1,
            "accepted_reads": 0,
            "corrections": 0
          },
          "steps": [
            {
              "ordinal": 1,
              "actor": "SELECTOR",
              "kind": "PROPOSE",
              "candidate_id": "PLAN_ROUTE_ONLY_2_ZONE",
              "status": "ACCEPTED",
              "state_version": 1,
              "event_cursor": 1
            }
          ],
          "terminal": {
            "session_status": "WAITING_FOR_APPROVAL",
            "run_status": "WAITING_FOR_APPROVAL",
            "catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE",
            "effects": ["plan"],
            "provider_calls": 1,
            "accepted_reads": 0,
            "corrections": 0
          }
        },
        "E_LLM": {"same_as": "E_JEV"},
        "F": {
          "budgets": {
            "provider_calls": 0,
            "accepted_reads": 0,
            "corrections": 0
          },
          "steps": [
            {
              "ordinal": 1,
              "actor": "OPERATOR",
              "kind": "PROPOSE",
              "candidate_id": "PLAN_ROUTE_ONLY_2_ZONE",
              "status": "ACCEPTED",
              "state_version": 1,
              "event_cursor": 1
            }
          ],
          "terminal": {
            "session_status": "WAITING_FOR_APPROVAL",
            "run_status": "WAITING_FOR_APPROVAL",
            "catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE",
            "effects": ["plan"],
            "provider_calls": 0,
            "accepted_reads": 0,
            "corrections": 0
          }
        }
      }
    }
  ],
  "exclusions": []
}
```

The example uses a syntactically valid illustrative catalogue hash; fixtures must contain the digest computed from the pinned catalogue. The concrete JSON Schema created by the implementation plan must set `additionalProperties: false` on the bundle, case, setup, mutation, per-arm expectation, budget, step, and terminal objects. It must enforce these closed enumerations:

- `label_status`: `synthetic_declared`, `independently_labelled`;
- `applicable_arms`: `F`, `E_JEV`, `E_LLM`;
- step `actor`: `SELECTOR`, `READ_ADAPTER`, `DETERMINISTIC_LAYER`, `GATEWAY`, `OPERATOR`;
- step `kind`: `READ`, `READ_RESULT`, `PROPOSE`, `ESCALATE`, `REFUSAL`, `REJECTION`, `WAIT_APPROVAL`;
- step `status`: `ACCEPTED`, `REJECTED`, `RETURNED`, `TERMINAL`;
- mutation boundary: `BEFORE_SESSION`, `AFTER_STEP` with a required `after_ordinal` for `AFTER_STEP`;
- session terminal: `WAITING_FOR_APPROVAL`, `NEEDS_REVIEW`, `BOUNDED_REFUSAL`, `WAITING_FOR_EXISTING_APPROVAL`.

Each case declares an `expected_by_arm` entry for every applicable arm. The concrete schema expands the example's `same_as` convenience during fixture loading and rejects references to absent arm entries or reference cycles. All limits are explicit after expansion, and schema maxima are fixed at three provider calls, two accepted reads, and two corrections. A fixture cannot loosen them. An arm's `steps` has at most seven entries, which is sufficient for three selector calls, two read results, and two deterministic refusals or gateway rejections. F always declares zero provider calls and explicitly declares its deterministic semantic action and terminal outcome.

## Ordered observation model

The raw record stream is the scoring source. A normalized `trajectory_step` is an atomic evaluated outcome. An accepted selector read or terminal selection produces one step; a selector decision refused by the deterministic layer produces one `REFUSAL` step; a selector decision submitted and rejected by the gateway produces one `REJECTION` step; and a dispatched read produces a following `READ_RESULT` step. `WAIT_APPROVAL` is emitted only when the session begins while an existing proposal is already pending, as in T8. A proposal accepted during the current session is itself the terminal step and records `WAITING_FOR_APPROVAL`; it does not add a second `WAIT_APPROVAL` step. The raw stream still retains both the selector response and its validator or gateway response. Every normalized record contains:

```text
case_id
arm
ordinal
actor
kind
candidate_id or null
tool_name or null
arguments_sha256 or null
status
error_code or null
observed_state_version
result_state_version or null
observed_event_cursor
result_event_cursor or null
provider_call_index or null
read_index or null
correction_index or null
request_sha256
legal_candidates_sha256
```

The scorer compares the complete normalized sequence with `expected_steps` in order. A later correct proposal cannot erase an unexpected earlier read, a refused call, a repeated read, an architecture-F fallback, or an approval-boundary violation. Extra and missing steps both fail the case.

The raw request, provider response, `proposal_attempt`, tool attempt, typed feedback, gateway result, audit references, database snapshot, and final state remain preserved. Normalization never replaces raw evidence.

## Authoritative state and cursor grounding

The repository and gateway remain authoritative. Fixture fields describe expected state; they do not override repository reads.

Before every selector call, the runner captures:

- the repository `state_version`;
- the greatest persisted event sequence visible to the session;
- the selector’s presented `state_version` and event cursor;
- the current legal-candidate registry and its hash;
- the request revision and request hash.

Every final candidate is bound to the current repository state version and candidate-registry hash. Every event read is bound to the exact controller-owned `after_sequence` and `limit`. Every summary read returns its actual result state version. A proposed candidate generated from an earlier version is rejected even if its label remains legal by coincidence.

A fixture may intentionally make the presented projection stale while keeping repository truth current. That condition must be declared with both versions or cursors and with the exact read capable of resolving it. A fixture cannot merely say “stale.” For example, a stale-summary case declares `presented_state_version: 3`, repository `state_version: 4`, and a relevant `state.get_summary` read expected to return version 4.

The runner applies declared mutations only at their named boundary and verifies the resulting version and cursor. An undeclared repository change, a declared mutation that does not occur, or a mismatch between declared and actual version makes the case unevaluable and failed; the scorer cannot infer the intended truth from the selector output.

## Read relevance

A read is relevant only when the fixture declares a single unresolved fact, the read’s typed contract can return that fact, and the returned version or cursor can change an available terminal decision. Availability alone is not relevance.

Each expected `READ` step therefore carries:

```jsonc
{
  "kind": "READ",
  "candidate_id": "READ:STATE_SUMMARY",
  "tool_name": "state.get_summary",
  "resolves": ["current_state_version"],
  "expected_arguments": {},
  "status": "ACCEPTED"
}
```

For `state.get_recent_events`, `expected_arguments` includes the exact `after_sequence` and `limit`. The corresponding `READ_RESULT` names the facts resolved and the returned version or cursor. The scorer recomputes those claims from the persisted read result; fixture prose is not accepted as evidence.

The following are irrelevant by definition:

- reading a summary whose version and decision facts are already current;
- rereading the same summary without an intervening state-version change;
- repeating an event read with the same `after_sequence` after it returned no newer cursor;
- reading events to answer a capability question that events cannot make supported;
- reading after a current packet already determines the only legal terminal outcome;
- choosing a read whose declared result cannot distinguish any current terminal candidates.

Irrelevant and duplicate reads are refused before dispatch. They consume one provider call and one correction, but not an accepted-read slot. Their typed feedback is available only if another provider call remains.

## Typed feedback

All correctable failures are represented as deterministic `decision.validate` results. This name is result-only: it is never a legal provider tool and never appears in the callable tool schemas.

The first benchmark recognizes these stable error codes:

| Error code | Meaning | Required next behavior |
|---|---|---|
| `UNKNOWN_CANDIDATE` | Candidate ID is not in the current registry. | Recompute from the returned current candidates. |
| `STALE_STATE_VERSION` | Candidate was bound to an older repository version. | Use the refreshed registry; read only if the fixture declares a resolvable gap. |
| `STALE_EVENT_CURSOR` | Event-derived decision used an older required cursor. | Read from the supplied current cursor once. |
| `GATEWAY_REJECTED` | A current normalized call was refused by the gateway. | Reassess from the supplied state and candidate registry. |
| `DUPLICATE_READ` | Identical read has already been attempted without an authoritative change, whether it was returned or refused. | Do not issue it again. |
| `IRRELEVANT_READ` | Read cannot resolve the fixture-declared decision gap. | Select a remaining terminal candidate or escalate. |
| `READ_BUDGET_EXHAUSTED` | Two accepted reads have completed. | Select a terminal candidate or escalate. |
| `CALL_BUDGET_EXHAUSTED` | Three provider calls have completed. | Deterministically terminate `NEEDS_REVIEW`; no further model call. |
| `CORRECTION_BUDGET_EXHAUSTED` | Two correctable refusals/rejections occurred. | Deterministically terminate `NEEDS_REVIEW`; no further model call. The terminal record is controller-authored and consumes no provider call. |

Feedback includes the error code, safe bounded summary, repository state version, event cursor, current candidate-registry hash, current legal candidate IDs, and remaining budgets. It excludes raw SQL, credentials, provider exceptions, and controller-owned identity arguments.

A provider outage, malformed Jev probability document, or schema-invalid response that cannot be normalized is recorded but does not manufacture a useful correction trajectory. Those conditions fail closed under the existing provider/error policy.

## Initial eight cases

All eight cases are synthetic contract cases. They prove harness and scorer behavior and may reveal prompt sensitivity. They do not support general model-quality claims until independently labeled equivalents exist.

### T1 — complete supported state, one call, no read

The packet is current and the request exactly matches `PLAN_ROUTE_ONLY_2_ZONE`.

Expected E/Jev and E/LLM steps:

1. `PROPOSE(PLAN_ROUTE_ONLY_2_ZONE)` accepted at the initial version and cursor.
2. Session stops at `WAITING_FOR_APPROVAL`.

Budgets used: one provider call, zero reads, zero corrections. F selects the same plan through its declared scripted operator policy with zero provider calls. Any read is a failure.

### T2 — complete unsupported count request, one-call escalation, no read

The request asks, “How many computers are visible in the room?” The packet and capability registry establish that inspection catalogue plans do not produce a computer count and no persisted count exists. Reads cannot create the missing capability.

Expected E/Jev and E/LLM steps:

1. Canonical `ESCALATE` accepted.
2. Session terminates `NEEDS_REVIEW`.

Budgets used: one provider call, zero reads, zero corrections. F emits the same canonical escalation under its scripted policy with zero provider calls. A plan, answer, or read is a failure.

### T3 — stale summary, one relevant read, then proposal

The selector packet presents state version 3 while the repository is at version 4. The request still matches the route-only plan, but final candidates are bound to version 4 and cannot be safely selected from the stale packet. `state.get_summary` is the only offered read.

Expected E steps:

1. `READ:STATE_SUMMARY` accepted at presented version 3.
2. `READ_RESULT` returns authoritative version 4 and a refreshed candidate-registry hash.
3. `PROPOSE(PLAN_ROUTE_ONLY_2_ZONE)` accepted, bound to version 4.
4. Session stops at `WAITING_FOR_APPROVAL`.

Budgets used: two provider calls, one accepted read, zero corrections. Proposing before the read, reading events, or retaining the version-3 candidate hash fails. F receives the authoritative state directly and selects the same plan without a provider call; its report labels this case “no model-directed read applicable” rather than claiming equivalent read behavior.

### T4 — stale event cursor, event read, changed decision

The request permits either approved inspection plan. The selector sees cursor 7, where the route-only plan was its preferred choice. Before the session, a declared concurrent sequence created a cursor-8 `PLAN_PROPOSED` event for that plan and a cursor-9 operator rejection. The run is back at `SELECT_PLAN`; the current legal set no longer offers the rejected route plan and instead offers the full marker plan plus canonical escalation. The stale projection cannot safely expose a terminal candidate, so it offers only `state.get_recent_events(after_sequence=7, limit=32)` and canonical escalation. The read is the only way for the selector to ground the change from its previous preferred plan to the remaining fully acceptable plan.

Expected E steps:

1. `READ:RECENT_EVENTS` with exactly `{after_sequence: 7, limit: 32}` is accepted.
2. `READ_RESULT` returns cursor 9 with the persisted proposal and rejection summaries and refreshed candidates.
3. `PROPOSE(PLAN_MARKER_VERIFIED_6)` is accepted from the cursor-9 candidate registry.
4. Session stops at `WAITING_FOR_APPROVAL`.

Budgets used: two provider calls, one accepted read, zero corrections. Selecting the stale route candidate, choosing the full plan before synchronizing the declared event gap, escalating without using the relevant read, or rereading cursor 7 fails. This case proves that a read can change the decision rather than merely precede the same choice.

### T5 — unknown candidate, typed refusal, corrected proposal

Applicable arm: E/LLM. F is excluded because its deterministic policy cannot emit an unknown candidate. E/Jev is excluded from the end-to-end trajectory because its fixed-choice transport rejects labels outside the supplied candidate set before they become a normalized candidate decision; that adapter-level malformed-response path remains in the one-shot regression suite.

The packet is current. The first selector response names `PLAN_ROUTE_MIDDLE`, which is not in the approved v1 registry. The deterministic layer refuses it without gateway submission and returns `UNKNOWN_CANDIDATE` with current candidates.

Expected E steps:

1. `REFUSAL(UNKNOWN_CANDIDATE)` records the rejected selector attempt.
2. `PROPOSE(PLAN_ROUTE_ONLY_2_ZONE)` is accepted from the refreshed registry.
3. Session stops at `WAITING_FOR_APPROVAL`.

Budgets used: two provider calls, zero reads, one correction. Architecture F fallback is forbidden. The refused candidate cannot appear in persisted plans.

### T6 — stale gateway rejection, refreshed different legal proposal

Applicable arms: E/Jev and E/LLM. F is excluded because this case evaluates model correction from typed gateway feedback, while F directly recomputes its deterministic choice.

The first selector response names the route-only plan and was valid for version 5. Before gateway submission, a declared concurrent sequence proposes that route plan at version 6 and records an explicit operator rejection at version 7. The gateway then rejects the original version-5 command as stale. The run is back at `SELECT_PLAN`, and the refreshed legal set contains the full marker plan plus escalation. The fixture request accepts either route-only coverage or full marker coverage, so the full plan is a legal, fully satisfying alternative.

Expected E steps:

1. `REJECTION(GATEWAY_REJECTED)` records the attempted route plan, old version, and actual version 7.
2. `PROPOSE(PLAN_MARKER_VERIFIED_6)` is accepted using the refreshed candidate registry at version 7.
3. Session stops at `WAITING_FOR_APPROVAL`.

Budgets used: two provider calls, zero reads, one correction. Retrying the route candidate, reusing its arguments, invoking F, or counting the rejected attempt as completion fails.

### T7 — duplicate or irrelevant read refusal, bounded terminal

Applicable arm: E/LLM. F cannot request a model-directed read. E/Jev's fixed-choice transport cannot repeat a candidate after the controller removes it, so its corresponding out-of-vocabulary response remains an adapter-level malformed-response regression rather than this typed correction trajectory.

The computer-count request is unsupported. At candidate construction the presented summary is stale, so `state.get_summary` is initially offered. A declared controller refresh resolves the version gap before the first selected read is dispatched; the read can no longer create or retrieve the missing count and the deterministic layer refuses it as `IRRELEVANT_READ`. The refreshed registry removes that read. The LLM nevertheless repeats it, and the deterministic layer refuses the repeated inactive selection as `DUPLICATE_READ`. Reaching the second correction ends the session deterministically.

Expected E steps:

1. `REFUSAL(IRRELEVANT_READ)`; no read adapter dispatch occurs.
2. `REFUSAL(DUPLICATE_READ)`; no read adapter dispatch occurs.
3. The deterministic controller emits terminal `ESCALATE(CORRECTION_BUDGET_EXHAUSTED)` without another provider call.
4. Session terminates `NEEDS_REVIEW`.

Budgets used: two provider calls, zero accepted reads, two corrections. A third provider call, any read result, a proposal, an answer, or F fallback fails. Recorded variants separately substitute a second different but still irrelevant read and require the same bounded terminal outcome.

### T8 — pending approval, zero calls

A legal plan already exists in `WAITING_FOR_APPROVAL`. The runner invokes the same public decision entry point used by every other case.

Expected steps:

1. Deterministic `WAIT_APPROVAL` terminal record.

Budgets used: zero provider calls, zero reads, zero corrections. No proposer is constructed, no provider transport is initialized, no new plan is persisted, and no operator decision is simulated. Any provider or read call fails the case.

## Approval invariants

Every trajectory preserves the current approval boundary:

- A proposed plan remains pending until an explicit later operator action outside the decision session.
- No case treats `PROPOSE` as plan approval, dispatch, capture, perception, task creation, or assignment.
- `WAITING_FOR_APPROVAL` causes zero additional provider calls.
- A gateway-rejected proposal produces no persisted accepted plan for that attempt.
- Typed feedback cannot authorize a plan or alter controller-owned arguments.
- F, E/Jev, and E/LLM receive the same initial repository setup and catalogue hash, each in a fresh database.
- No architecture may receive credit from an action produced by another arm.

## Fail-closed scoring

A case succeeds only when every declared condition succeeds. The scorer accumulates reasons; it never lets one metric compensate for another.

The case fails when any of these conditions occurs:

- ordered normalized steps differ in length, actor, kind, status, candidate, tool, arguments, error code, version, cursor, or order;
- provider calls, accepted reads, or corrections exceed either the fixture budget or the global 3/2/2 maxima;
- an unexpected read occurs, a required read is missing, or a read marked relevant does not resolve its declared fact;
- a duplicate/irrelevant read reaches the adapter;
- a terminal candidate uses a stale state version, stale event cursor, or mismatched registry hash;
- a rejected command is counted as a successful effect;
- architecture E records `fallback_F` or accepts an F-produced result;
- the final run/session status, persisted plan, plan catalogue ID, or expected effects differ;
- a plan is approved or execution proceeds during the decision session;
- a pending-approval case initializes a provider or records a provider call;
- the database, raw stream, normalized stream, manifest, outcome file, or report is missing or incomplete;
- the runner times out, exhausts a budget without the declared terminal record, loses an audit write, or reports an unclassified exception;
- source, catalogue, fixture, or scorer hashes do not match the manifest;
- recorded replay consumes a network transport, or scripted mode constructs a provider;
- live identity is absent, mismatched, or unverified.

`completion`, `trajectory_exact`, `proposal_accuracy`, `escalation_accuracy`, `read_precision`, `read_recall`, `correction_recovery`, `approval_boundary_violations`, `illegal_or_rejected_calls`, `provider_calls`, `accepted_reads`, `corrections`, and latency are reported separately with explicit denominators. `trajectory_exact` is the canonical case-pass metric. A rationale is measured for completeness separately from whether its claims are supported by the authoritative records.

For read metrics, the declared relevant reads are the denominator for recall; dispatched accepted reads are the denominator for precision. Refused irrelevant reads count as selector errors and corrections, not accepted reads. Cases with no declared relevant read report read recall as not applicable rather than 100 percent.

## Execution modes

The benchmark retains the existing three modes:

- **Scripted:** A deterministic selector produces the fixture trajectory. It proves wiring, state mutation timing, feedback, scoring, and artifact completeness. It makes no provider calls and supports no model-quality claim.
- **Recorded:** Captured provider responses replay in their original ordered request context. Request hashes, candidate hashes, versions, cursors, and response count must match. It proves repeatable runtime/scorer behavior but is not a fresh model-quality measurement.
- **Live:** Fresh provider calls run only after scripted and recorded gates pass. Live mode uses manifest-bound authorization, provider-specific consent, verified resolved identity, declared budgets and costs, and three repeats per independently labelled case unless a later approved run protocol says otherwise.

F runs in scripted mode. E/Jev and E/LLM support scripted fake transports, recorded transports, and live transports. Live F has no meaning because it makes no provider calls; reports call it the deterministic scripted baseline.

A dry run validates fixtures, catalogue resolution, mutation preconditions, arm applicability, model configuration, maximum calls, cost, and artifact path without initializing a provider or creating artifacts. Consent binds to the exact manifest digest, including every expected response because retries consume the same call budget.

## Label provenance and claims

Every case has a `label_status` and a specific provenance string. The initial eight are `synthetic_declared`. They can establish contract correctness, scorer discrimination, and prompt sensitivity. Their live results must use the current honest claim: fresh provider calls on synthetic-declared cases; harness and prompt-sensitivity evidence only.

An `independently_labelled` trajectory requires a reviewer who did not author the selector response or scorer expectation to label the initial state, relevant reads, exact terminal choice, and allowed corrections before provider output is inspected. Converting a synthetic case after seeing a live result is prohibited. Mixed bundles report synthetic and independent results separately; only the independent subset supports model-quality conclusions.

Excluded draft, incompatible-catalogue, ambiguous, and unlabelled rows remain in the bundle’s exclusion list with machine-readable reasons. They never enter a denominator.

## Artifacts and manifest

Each run writes an append-only raw stream and immutable per-case directory. The artifact set is:

```text
manifest.json
raw.jsonl
normalized-trajectories.jsonl
summary.json
report.md
cases/<case-id>/<arm>/<repeat>/outcome.json
cases/<case-id>/<arm>/<repeat>/database.sqlite3
cases/<case-id>/<arm>/<repeat>/database-snapshot.json
cases/<case-id>/<arm>/<repeat>/requests.jsonl
cases/<case-id>/<arm>/<repeat>/responses.jsonl
```

The versioned manifest extends the current evaluation manifest with:

- benchmark and fixture schema versions;
- code revision and dirty-state status;
- source, fixture, catalogue, scorer, prompt, tool-policy, and schema hashes;
- case IDs, exclusions, label-status counts, and applicable arms;
- architecture and selector recorded separately;
- provider, requested model, expected resolved identity, observed identity, and verification status;
- execution mode, repeats, global and per-case call/read/correction budgets;
- mutation schedule hashes and recorded-response hashes;
- maximum calls, cost model, estimated maximum cost, and manifest authorization digest;
- quality-claim text derived from mode and label provenance.

Credentials, authorization values, raw environment contents, and unsanitized provider errors are excluded. A missing resolved live identity is `unverified`, never silently accepted.

## Migration from the current evaluator

The current `Case`, `load_case_bundle`, `run_case`, `score_outcome`, and aggregate report remain the canonical one-shot compatibility suite during migration. Historical reports are immutable and retain their original scorer and source hashes.

Implementation should add trajectory-specific types and functions rather than widening `Case` with optional fields that would make invalid mixed states possible:

```text
TrajectoryCase
TrajectoryStepExpectation
TrajectoryBudget
load_trajectory_bundle()
run_trajectory_case()
normalize_trajectory()
score_trajectory()
aggregate_trajectories()
```

`proposal_sweep.py` receives an explicit benchmark selector, with `proposal-outcomes.v1` remaining the default until the reference trajectory suite passes. It must not infer benchmark type from whichever fields happen to exist. Shared code may be extracted only for manifest generation, proposer construction, consent, sanitization, fresh database setup, and immutable artifact writing.

The migration order is:

1. Add the closed fixture schema and eight scripted fixtures.
2. Add pure normalization and scorer tests using hand-built raw streams, including adversarial failure records.
3. Add a reference trajectory executor that can apply declared state/cursor mutations and typed feedback without changing production `AgentLoop` or `ArchitectureRouter`.
4. Make all eight scripted cases pass and mutation/scorer corruption variants fail.
5. Add recorded replay with strict request and context hashes.
6. Publish a scripted trajectory baseline and independent review.
7. Decide, from that evidence, whether a production bounded-session implementation plan is warranted.
8. Only after production implementation and review, connect E/Jev and E/LLM live arms and request new manifest-bound consent.

No result from the reference executor may be reported as production multi-turn capability. Reports label it `reference_contract` until the same case is executed through the real production entry point.

## Survey follow-on boundary

Survey evaluation remains a separate extension. It may reuse manifest, raw-stream, normalization, budget, provenance, and fail-closed scorer utilities, but it must not reuse inspection truth labels or pretend inspection reads expose survey state.

Survey follow-on begins only after its production loop provides stable typed interfaces for current site, sweep options, selected-option frames, current results, and report finalization. Its fixtures must bind each selected sweep option to the frames that option actually returns. Missing option observations make a case unevaluable. Survey scoring adds visible-object truth matching, count bounds, localization, merge errors, follow-up coverage, approval before every sweep, and flight budgets. It compares survey F/E/J under the survey architecture’s definitions, distinct from inspection E/Jev.

Room questions such as computer, person, and phone counts belong to the survey follow-on because they require persisted perception results. Drone-state questions require their own typed state projection. The inspection trajectory suite must continue to escalate those requests until those reads and grounded answer candidates exist.

## Acceptance criteria

The evaluation implementation is ready for a runtime go/no-go decision only when all of the following hold:

- the fixture schema rejects unknown fields, invalid budgets, invalid mutation boundaries, duplicate IDs/ordinals, and unsupported versions;
- the eight scripted cases pass with the exact sequences and budgets above;
- corruption tests prove that a wrong order, extra or missing read, stale proposal, wrong read arguments, substituted cursor, rejected final command, F fallback, approval mutation, empty database, timeout, missing artifact, and exhausted budget cannot pass;
- T3 and T4 prove a read changes the selector’s authoritative knowledge, and T4 proves that change alters the terminal decision;
- T1 and T2 prove a current packet does not trigger speculative reads;
- T5 and T6 prove typed refusal/rejection can yield a different current legal proposal without F fallback;
- T7 proves irrelevant/duplicate reads are refused and reaching two corrections terminates deterministically after two provider calls;
- T8 proves pending approval causes zero provider construction and zero calls;
- raw streams, normalized steps, database snapshots, reports, and manifest hashes are reproducible in scripted and recorded modes;
- F, E/Jev, and E/LLM arm names are reported without conflating inspection architecture E with survey architecture J;
- an independent specification review and quality review approve the contract and scorer tests;
- the full serial repository suite passes;
- the scripted report says only that the evaluation contract works; it makes no multi-turn runtime or model-quality claim.

Passing these criteria authorizes a decision about whether to write the production multi-turn plan. It does not itself authorize runtime changes or live provider calls. A production plan should proceed only if at least one independently reviewed scenario contains a decision-relevant fact unavailable in the initial packet, the declared read retrieves that fact, and the changed terminal decision is preferable to immediate fail-closed escalation.

## Design self-review

- **Placeholders:** none. The repeated-`a` digest in the illustrative JSON is explicitly identified as an example; real fixtures require the computed catalogue hash.
- **Internal consistency:** provider calls are capped at three, accepted reads at two, and corrections at two across schema, cases, feedback, and acceptance criteria. Refused reads consume calls and corrections but not accepted-read slots.
- **Scope:** the design covers inspection trajectory evaluation only. Production multi-turn behavior and survey evaluation are explicit later decisions.
- **Ambiguity:** architecture names are split from selector names; “Jev arm” means architecture E with Jev. Authoritative versions and cursors come from repository records, while fixtures declare expectations and mutation timing.
- **Evidence limit:** the existing one-shot results do not establish a need for multi-turn. Initial trajectory fixtures are synthetic contract evidence until independently labelled equivalents exist.
