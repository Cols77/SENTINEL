# Proposal: give `event_list` items a real schema

> **Status 2026-09-20: GRANTED (consent C-12) and implemented on `fix/model-visible-results`**, as its own
> small schema edit rather than inside the WP0 amendment revision, because a paid run showed it blocks the
> LLM loop. One deviation from the text below: `summary` is capped at **512**, the loop's existing
> `_SUMMARY_MAX`, not 256 -- 256 would have truncated summaries the loop already produces.

Status: proposal only. No schema, code, or test file has been touched to produce this
document. Gate: **Coherence consent from the operator** (see plan gate
`G-coherence-event-list`, P4 Task 7). Sequencing: if accepted, this should ride in the
**same schema revision** as the rest of the WP0 amendment (P3b1 Task 7, gate
`G-amend-rest`), not as a standalone edit to
`docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json`.

## The defect

In `docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json`, the `event_list`
branch of `AgentToolResultPayload` declares its item schema with no `properties` at
all:

```
3122          {
3123            "type": "object",
3124            "additionalProperties": false,
3125            "required": [
3126              "kind",
3127              "events"
3128            ],
3129            "properties": {
3130              "kind": {
3131                "const": "event_list"
3132              },
3133              "events": {
3134                "type": "array",
3135                "maxItems": 32,
3136                "items": {
3137                  "type": "object",
3138                  "additionalProperties": false
3139                }
3140              }
3141            }
3142          },
```

`events.items` is `{"type": "object", "additionalProperties": false}` — an object
schema with `additionalProperties: false` and no `properties` key, which admits only
the empty object `{}`. Any populated event object fails validation.

`src/sentinel/coordinator/agent_loop.py` works around this today. The comment at the
call site names it explicitly:

```
323          elif tool_name == "state.get_recent_events":
324              value = self.port.read_events(run_id, arguments["after_sequence"], arguments["limit"])
325              # WP0 schema defect (found live 2026-09-20): AgentToolResultPayload's
326              # event_list items are `{"type": "object", "additionalProperties":
327              # false}` with NO properties, so *any* populated event object fails
328              # validation and the call kills the run. Until the amendment lands,
329              # the events array stays empty and the bounded summaries travel in
330              # safe_summary, which the schema leaves free-form.
331              summaries = [_event_summary(item) for item in value]
332              result = {"kind": "event_list", "events": []}
333              joined = " | ".join(f"{item['sequence_no']}:{item['summary']}" for item in summaries)
334              safe_summary = (f"{len(summaries)} event(s): {joined}" if summaries else "no events after cursor")[:512]
```

The workaround always emits `"events": []` (line 332, valid against the empty-object-only
item schema) and instead packs the per-event summaries into `safe_summary` (lines
333-334) as a pipe-joined string of `"<sequence_no>:<summary>"` tokens — a field the
schema leaves free-form.

## The consequence

`state.get_recent_events` is one of only two read tools the model has (the other is
`state.get_summary`). Because `result.events` is always empty, the model cannot read
structured events at all through the typed `result` field; it must parse
`safe_summary`'s ad hoc `"N event(s): 3:TYPE: {...} | 4:TYPE: {...}"` prose instead of
consuming a typed array. That is strictly worse for a model caller than a schema-checked
list of objects, and it is a workaround the codebase itself flags as temporary.

## What is actually available to populate an item

`_event_summary` (`src/sentinel/coordinator/agent_loop.py:76-84`) is the function that
already computes, per event, exactly the fields a populated item would need:

```
76   def _event_summary(event: dict) -> dict:
77       event_type = str(event.get("event_type", "")) or "EVENT"
78       payload = event.get("payload") or {}
79       if payload:
80           body = json.dumps(_redacted(payload), separators=(",", ":"))
81           summary = f"{event_type}: {body}"[:_SUMMARY_MAX]
82       else:
83           summary = event_type[:_SUMMARY_MAX]
84       return {"sequence_no": event.get("sequence_no", 0), "event_type": event_type[:64], "summary": summary or event_type[:64] or "EVENT"}
```

It returns exactly three fields:
- `sequence_no` — `event.get("sequence_no", 0)`, an integer, never negative in practice (sequence cursors start at 0 and only advance).
- `event_type` — truncated to 64 chars (`event_type[:64]`, line 84).
- `summary` — truncated to `_SUMMARY_MAX` (512, `agent_loop.py:28`) at lines 81/83, though in the joined `safe_summary` string it is later re-truncated further by the overall 512-char cap on the whole joined string (line 334); a per-item cap of 256 leaves headroom for the `"N:"` prefix and `" | "` separators when several summaries share the 512-char `safe_summary` budget.

## Proposed item shape

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["sequence_no", "event_type", "summary"],
  "properties": {
    "sequence_no": { "type": "integer", "minimum": 0 },
    "event_type": { "type": "string", "maxLength": 64 },
    "summary": { "type": "string", "maxLength": 256 }
  }
}
```

This is precisely the shape `_event_summary` already produces (module the 256 vs. 512
cap noted above, which only tightens what the function already emits and would require
a one-line change to `_event_summary`'s truncation, not a new field). No other fields
of the raw event object (e.g. `payload` itself) are proposed for inclusion; `summary`
already carries a redacted, length-capped rendering of `payload` per `_redacted`
(`agent_loop.py:48`).

## Compatibility

- An empty `events` array remains valid under the proposed item schema (arrays are
  validated element-wise; zero elements trivially satisfy `items`), so today's
  workaround (`"events": []`, line 332) keeps validating unchanged during any rollout
  window.
- Adding `properties`/`required` to a previously property-less, additionalProperties-false
  item schema is a narrowing, not a widening: only conforming payloads that were already
  impossible to send (any non-empty object) become valid. No previously-valid message
  becomes invalid.
- This is a wire-schema change and should bump `tool_policy_revision` per the existing
  amendment convention, alongside the rest of the WP0 amendment rather than as its own
  bump.

## Sequencing

This edit touches the same file as the rest of the WP0 amendment (P3b1 Task 7, gate
`G-amend-rest`). To avoid two schema revisions landing back-to-back on the same file,
this proposal should ride in that same revision rather than being applied separately.

## Gate

This document proposes; it does not implement. Landing the schema change requires
**Coherence consent from the operator** (gate `G-coherence-event-list`). No schema file,
`agent_loop.py`, or test has been edited to produce this proposal.
