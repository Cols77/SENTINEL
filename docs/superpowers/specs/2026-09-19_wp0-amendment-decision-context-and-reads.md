---
id: sentinel-wp0-amendment-decision-context-and-reads
title: "WP0 amendment - decision_context, typed reads, result ids, operator.ask"
status: proposed (Coherence consent required — gate G-amend, P1 Task 6)
proposed_by: >-
  P1 — Agent Loop Foundations (`docs/superpowers/plans/2026-09-19-p1-agent-loop-foundations.md`)
proposed_at: 2026-09-19
scope: >-
  `sentinel.v0` wire schema (`docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json`)
implements_in: >-
  P3 Task 1 (`docs/superpowers/plans/2026-09-19-p3-hybrid-E-followup.md`), only after this document is
  accepted. **This document does not modify the schema file.** It is a diff proposal only.
consent_required: >-
  yes — never self-granted (SR-A03 requires any tool-surface change to be a schema amendment plus a
  `tool_policy_revision` bump, approved as a WP0 contract change)
---

# WP0 amendment — `decision_context`, typed reads, result ids, `operator.ask`

## 1. Why this amendment exists

P1 fixes what the current `sentinel.v0` schema already allows (D1–D9, D11): real event payloads, a computed legal
set, gateway rejections as feedback, a policy seam, bounded history. All of that ships in P1 without touching the
wire schema (Global Constraint: "No wire-schema edits in P1").

Two things P1 explicitly does **not** fix because they need new wire surface:

- **D4 (partial):** `state.get_summary` stays a 4-field payload (`kind`, `run_status`, `phase`, `state_version`) in
  P1. The richer facts computed by `DecisionState.facts()` (Task 1) reach the loop's *internal* legal-set logic and
  the *policy's* prompt/criteria (Task 2, Task 4), but a policy that wants to re-read a specific record after a
  gateway rejection — "what did I already rank?", "what plan is approved?" — has no tool for that. It can only see
  what's already in `event_context`/`evidence_context`.
- **D5 (typed ids):** P1 Task 3 puts ids into event-summary strings (`"TASK_CREATED: {\"task_id\": \"...\"}"`) so
  they're visible, but the `AgentToolResult.result` payload itself (`AgentToolResultPayload`) still validates as
  bare `{"kind": "task"}` — the id is not a first-class, typed field a policy can read without string-parsing.

This is also where P3's architecture E needs new surface that P1 has no reason to add: `LlmProposer` and
`EscalationInvestigator` need read tools and a way to ask the operator a bounded question instead of only
`finish(NEEDS_REVIEW)` or a bare `escalate` candidate.

Everything below is additive (new optional fields, new enum members, new tool names). No existing required field,
enum member, or payload shape is removed or narrowed. `contract_version` stays `sentinel.v0`; this is a minor
revision of that contract plus a `tool_policy_revision` bump (SR-A03), not a new major contract.

## 2. Reconciliation with SR-SENTINEL-V0-A03

SR-A03's normative statement: *"The V0 default allowlist contains no side-effecting tools: no drone control, no
dispatch, no SQLite access. World and run facts reach the agent only as typed read-only `AgentRequest` fields."*

Read literally against the schema as it stands today, `AgentToolResult.tool_name` already includes side-effecting
tools (`task.create`, `patroller.rank_for_task`, `assignment.propose`, `inspection.propose_plan`) that predate this
amendment and predate P1. This amendment does not resolve that pre-existing tension — it is out of scope for a P1
Task 6 doc to re-litigate P0/WP1's original tool surface — but it must not make the tension worse, and where it can,
it should move in A03's stated direction:

- The five new **typed read tools** (§4) are read-only by construction: each takes a fixed shape or a single UUID
  argument and returns a bounded, schema-typed payload with no mutation path. They are the concrete form of "world
  and run facts reach the agent only as typed read-only fields" that A03 already asks for — today the agent gets
  facts only via `event_context`/`evidence_context`/`state.get_summary`; these tools let a policy fetch one record
  on demand instead of relying on the loop having already surfaced it.
- `operator.ask` (§4) is explicitly **not** an action on the world: it requests an operator decision and blocks on
  it. It replaces the pattern of encoding a question inside `finish(NEEDS_REVIEW)`'s `summary` string.
- No new side-effecting tool is added. `legal_tools.maxItems` rises (§4) to fit the five read tools alongside the
  existing eight, not to add new mutating tools.
- The `tool_policy_revision` bump (§7) is the mechanism A03 itself names for any tool-surface change; this document
  is that change's contract artifact.

**Open question for the operator at G-amend:** whether to fold "resolve the SR-A03 side-effecting-tool tension for
the *existing* eight tools" into this amendment or treat it as a separate future amendment. This document takes no
position and proposes no change to the existing eight `AgentToolResult.tool_name` values.

## 3. `AgentRequest.decision_context` (optional object)

Today `AgentRequest.legal_tools` (a flat array of tool-name strings, unchanged in shape by this amendment — see §4)
tells a policy *which* tool names are legal but not *why*, nor what arguments would make sense, nor which of several
same-named options (e.g. two catalogue plans) are on the table. P1's `legal_set()` (Task 2) already computes this
richness (`LegalSet.decision_point`, `Candidate.label`/`.description`/`.arguments`) and feeds it to policies
in-process via `PolicyContext` (Task 4) — but a request built and sent to an *out-of-process* adapter (the LLM over
HTTP, or a future out-of-process Jev policy) has no wire field to carry it. `decision_context` is that field.

**New optional property on `AgentRequest`:**

```jsonc
"decision_context": {
  "type": "object",
  "additionalProperties": false,
  "required": ["decision_point", "packet", "candidates"],
  "properties": {
    "decision_point": {
      "type": "string",
      "enum": ["TERMINAL", "WAIT_APPROVAL", "SELECT_PLAN", "WAIT_OBSERVATION",
                "EVIDENCE_GATE", "EVIDENCE_VETO", "RANK", "ASSIGN", "DONE"]
      // the LegalSet.decision_point values from src/sentinel/coordinator/legal_tools.py (Task 2), unchanged by P3
    },
    "packet": {
      "type": "object",
      "additionalProperties": false,
      "required": ["context_version", "run", "operator_request", "facts"],
      "properties": {
        "context_version": { "const": "decision_packet.v1" },
        "run": {
          "type": "object", "additionalProperties": false,
          "required": ["status", "phase"],
          "properties": { "status": {"type": "string", "maxLength": 64}, "phase": {"type": "string", "maxLength": 64} }
        },
        "operator_request": { "anyOf": [{"type": "string", "maxLength": 2048}, {"type": "null"}] },
        "facts": {
          "type": "object", "maxProperties": 32,
          "additionalProperties": { "anyOf": [{"type": "string"}, {"type": "boolean"}, {"type": "integer"}, {"type": "null"}] }
          // DecisionState.facts() (P1 Task 1) — categorical only, no raw floats, no UUIDs
        }
      }
    },
    "candidates": {
      "type": "array", "maxItems": 8,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["label", "tool_name", "description"],
        "properties": {
          "label": {"type": "string", "minLength": 1, "maxLength": 64},
          "tool_name": {"type": "string", "minLength": 1, "maxLength": 64},
          "description": {"type": "string", "minLength": 1, "maxLength": 256}
        }
      }
    }
  }
}
```

`decision_context.packet` is `DecisionState.to_packet()` (P1 Task 1) verbatim — it is already UUID-free by
construction (Task 1's own test asserts `[0-9a-f]{8}-[0-9a-f]{4}-` is absent from its JSON). `candidates` is
`LegalSet.candidates` projected to `(label, tool_name, description)`, dropping `arguments` (which does carry
UUIDs, e.g. `task.create`'s `perception_result_id`) — a policy that picks `label` still gets the ids to fill
those arguments from the typed read tools in §4, not from `decision_context`.

`legal_tools` (the existing flat array) is unchanged and stays required; `decision_context` is a strictly additive,
optional companion. A consumer that only understands the current schema keeps working unmodified.

## 4. Typed read tools

**Five new entries in the `AgentToolResult.tool_name` enum** (and the matching `AgentToolCall`/legal-tools
enumerations wherever `tool_name` is constrained): `state.read_plan`, `state.read_detection`, `state.read_task`,
`state.read_rankings`, `state.read_evidence`.

Each is read-only: no `CommandGateway.submit` call, no state mutation, no event emitted. Arguments are either empty
(the tool reads "the current X for this run") or a single UUID the caller already holds (never a free-text query or
a raw SQL-shaped filter — this keeps them inside A03's "no SQLite access" line, i.e. the tool signature can never
carry a query, only an id already handed to the agent by an event or a prior read).

| Tool | Arguments | Returns (`AgentToolResultPayload.kind`) |
|---|---|---|
| `state.read_plan` | `{}` (the run's current plan) | `{"kind": "plan_detail", "plan_id", "mode", "station_ids": [...], "approved": bool}` |
| `state.read_detection` | `{"perception_result_id": uuid}` | `{"kind": "detection_detail", "result_id", "artifact_id", "station_id", "status", "score_band", "canonical_class"}` (categorical `score_band`, not the raw float — same rule as `DecisionState.facts()`) |
| `state.read_task` | `{"task_id": uuid}` | `{"kind": "task_detail", "task_id", "status", "section_id", "perception_result_id"}` |
| `state.read_rankings` | `{"task_id": uuid}` | `{"kind": "ranking_detail", "task_id", "patroller_ids": [configured_id, ...]}` (≤ 16 entries) |
| `state.read_evidence` | `{"perception_result_id": uuid}` | `{"kind": "evidence_detail", "result_id", "artifact_id", "source_kind", "summary"}` (`summary` ≤ 512 chars, the same bounded string `evidence_context` already uses — P1 Task 3 D2) |

Each payload is capped (P3 Task 1 states 2 KB of serialized JSON per read; this document fixes the *shape*, P3 fixes
the *enforcement*). Unknown/missing id → `AgentToolResult.status = "REJECTED"`, `error_code = "NOT_FOUND"`, same
convention P1 Task 3 (D7) already establishes for unmapped tool names (`error_code = "UNKNOWN_TOOL"`).

**`legal_tools.maxItems` rises from 8 to 16.** The current enum has exactly 8 members
(`state.get_summary`, `state.get_recent_events`, `inspection.propose_plan`, `task.create`,
`patroller.rank_for_task`, `assignment.propose`, `wait_for_event`, `finish`); this amendment adds the 5 read tools
above plus headroom of 3 for `operator.ask` (§5, one slot) and 2 reserved/unused slots so a future amendment that
adds one or two more read-only tools doesn't need another `maxItems` bump. `minItems` stays 0 (a `TERMINAL` request
still legally has zero legal tools — P1 Task 3, D3).

## 5. Result payload ids

> **STATUS 2026-09-20: GRANTED by the operator and IMPLEMENTED for this section only.**
> Sections 2-4 (`decision_context`, typed read tools) and 6 (`operator.ask`) remain proposals.
> **Deviation from the text below, made during implementation:** each id key is *required* but its
> value is *nullable* (`anyOf: [uuid, null]`). A required non-null value is underivable on the legacy
> `AgentLoop(port, policy)` path, which has no read model to resolve ids from; making the key always
> present keeps the guarantee a policy actually needs (look for the id, never parse a string), while
> `tests/coordinator/test_typed_result_ids.py::test_production_path_never_yields_a_null_id` pins that
> the real path never returns null. Implemented in `_typed_result` (`agent_loop.py`).


Today (`AgentToolResultPayload`, `oneOf` branch 3) `plan`/`task`/`ranking`/`assignment` results carry only
`{"kind": "..."}` — no id, which is D5's remaining half after P1 Task 3 (P1 puts the id into the *next request's*
`event_context` summary string; the *result itself*, seen by the policy in the same turn, still has none).

**Extend the four existing `kind` branches with a required id field each, additive (no field removed):**

```jsonc
{"kind": "task", "task_id": {"$ref": "#/$defs/uuid"}}
{"kind": "plan", "plan_id": {"$ref": "#/$defs/uuid"}}
{"kind": "ranking", "patroller_ids": {"type": "array", "maxItems": 16, "items": {"$ref": "#/$defs/configured_id"}}}
{"kind": "assignment", "task_id": {"$ref": "#/$defs/uuid"}, "patroller_id": {"$ref": "#/$defs/configured_id"}}
```

`wait`, `finish`, `empty`, `state_summary`, `event_list` branches are unchanged.

## 6. `operator.ask`

A new tool, templated by the coordinator (never freely composed by a policy's arbitrary string — the coordinator
fills `options` from the current `LegalSet.labels`, so an `operator.ask` can never offer a choice that isn't
already gateway-legal):

```jsonc
"AgentOperatorAskArguments": {
  "type": "object", "additionalProperties": false,
  "required": ["question", "decision_point", "options", "deadline"],
  "properties": {
    "question": {"type": "string", "minLength": 1, "maxLength": 512},
    "decision_point": {"type": "string", "maxLength": 64},
    "options": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string", "maxLength": 64}},
    "suggested": {
      "anyOf": [
        {"type": "object", "additionalProperties": false, "required": ["label", "confidence"],
         "properties": {"label": {"type": "string", "maxLength": 64}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}}},
        {"type": "null"}
      ]
    },
    "deadline": {"$ref": "#/$defs/timestamp"}
  }
}
```

- The operator answers by choosing one of `options` in the UI — never free text. This keeps `operator.ask` inside
  the same closed-vocabulary discipline as every other tool call (SR-A03's spirit: bounded, enumerable surface).
- Expiry (deadline passed with no answer) yields `finish(NEEDS_REVIEW)` automatically — `operator.ask` never leaves
  a run silently stuck; it degrades to the existing, already-accepted escalation path.
- `operator.ask` is the typed replacement for "escalate with the question packed into `finish.summary`"; see §8
  reconciliation.

## 7. Events

**Three new `event_type` enum members** (additive to the existing 29-member enum in `EventEnvelope`):

- `OPERATOR_QUERY_REQUESTED` — `actor_type: "COORDINATOR"`, payload `{question, decision_point, options, suggested, deadline}` (mirrors `AgentOperatorAskArguments`).
- `OPERATOR_QUERY_ANSWERED` — `actor_type: "OPERATOR"`, payload `{selected_label}`.
- `DECISION_RECORDED` — `actor_type: "AGENT"`, payload `{chooser, confidence, band, legal_labels, model, error}`, i.e. `Decision` (P1 Task 5) serialized. This is what moves the RULE/Jev/LLM choice — already computed as `Policy.last: Decision` in-process — into the durable, queryable event log, so a promotion report (P3 Task 3) can be built from event history instead of only from the shadow-JSONL sink.

**`tool_policy_revision` bump:** the `llm-adapter-config-v1` config's `tool_policy_revision` field (already required,
already free-text, `SR-A03`-governed) moves to a new value at the point this amendment is implemented (P3 Task 1),
marking that the allowlist changed. This document does not fix the new value; P3 Task 1 does, alongside a draft
`SR-SENTINEL-V0-A04` (below) that supersedes the enumeration `SR-A03` currently only describes prose-side.

### Draft `SR-SENTINEL-V0-A04` (not registered; drafted here for the operator's review at G-amend)

```yaml
id: SR-SENTINEL-V0-A04
title: Agent tool allowlist — typed reads and operator.ask amendment
statement: >
  Per SR-A03's amendment clause, the V0 agent tool allowlist is extended with five read-only typed
  tools (state.read_plan, state.read_detection, state.read_task, state.read_rankings,
  state.read_evidence) and one operator-mediated tool (operator.ask). None execute a mutation or
  accept a free-text query; each read tool's arguments are fixed or a single UUID already held by
  the caller. legal_tools.maxItems rises 8 -> 16. tool_policy_revision bumps accordingly.
domain: behavioral
status: draft (pending Coherence consent — gate G-amend)
feature: FEAT-SENTINEL-V0-BOOTSTRAP
depends_on: SR-SENTINEL-V0-A03
```

This draft is not added to `requirements/index.json` by this document — only the operator's consent at G-amend
promotes it from draft to a registered requirement, at which point P3 Task 1 registers and implements it together.

## 8. Reconciliation

- **`finish(NEEDS_REVIEW)` stays the contradiction channel.** It is unchanged by this amendment: a policy that hits
  a genuinely contradictory or out-of-scope state still ends the run there. Nothing here narrows or replaces it.
- **`operator.ask` replaces "escalate with the question in the summary."** Concretely: where a policy today would
  call `finish` with `summary` holding a human-readable question (or the `_escalate()` candidate's description in
  `legal_tools.py`, Task 2) *and expects the operator to read free text and act outside the schema*, it should
  instead call `operator.ask` with the question and the bounded `options`. `finish(NEEDS_REVIEW)`'s `summary` field
  keeps its existing purpose — a terminal, human-readable record of *why* the run stopped — it just stops being
  overloaded as an ad hoc question-asking channel once `operator.ask` exists. This is a policy-authoring convention
  this document recommends, not a schema constraint the amendment itself enforces (both tools remain legal and
  independently valid).

## 9. What this document does not do

- It does not modify `docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json`. P3 Task 1 does, exactly to
  this diff, after G-amend.
- It does not resolve the pre-existing SR-A03 tension over the eight already-side-effecting tools (§2).
- It does not add, remove, or reorder any P1 task, decision point, or policy. `DecisionState`, `legal_set()`,
  `AgentLoop`, `RulePolicy`, `JevPolicy` (P1 Tasks 1–5) are unaffected; they gain a wire-level way to *expose* what
  they already compute, nothing more.

## 10. Acceptance checklist (for the fresh reviewer, Task 6 Step 2)

- [ ] Every new/changed field above is additive: no existing required field removed, no existing enum member
      removed, no existing payload shape narrowed.
- [ ] Every new tool is read-only or operator-mediated; none constructs a `CommandGateway.submit` call.
- [ ] Field length/size bounds mirror existing schema conventions (512-char summaries, 2048-char request text,
      ≤ 32/16/8-item arrays) rather than introducing new unbounded shapes.
- [ ] `decision_context.packet` and the new read-tool payloads contain no UUIDs outside `$ref: uuid`-typed fields
      and no raw floats (categorical bands only), matching `DecisionState.facts()`'s existing discipline.
- [ ] The SR-A03 reconciliation section (§2) does not overstate what this amendment fixes.
- [ ] Checked against `requirements/SR-SENTINEL-V0-A03.md` and `docs/COHERENCE.md` §"Change order — schema and
      code": the schema diff is the authoritative artifact; P3 Task 1's dataclass wrappers must round-trip it
      without information loss.
