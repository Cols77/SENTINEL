# Provider profile approval — 2026-09-23

## The record

| | |
|---|---|
| Profile | `config/provider-profile-gemini-openrouter.v1.json` |
| `profile_id` | `7f3c1e28-5a44-4b19-9c07-2d6e8a41b350` |
| `selection_decision_id` | `COLIN-2026-09-23-SOLE-OPERATOR-APPROVAL` |
| Provider / model | `gemini-openrouter` / `google/gemini-2.5-flash` |
| Evaluation pack | `ROOM_DOORS_V1`, `daf6b387c8fc40ce87ffee023a8ad8a92c30d5316e94f160db797cdc9669f59b` |
| Approved by | Colin, alone, on 2026-09-23, by explicit instruction |
| Dzivha's co-approval | **absent** |

## What this approval is, and what it is not

`docs/COHERENCE.md` (D-007 / G3) says an `APPROVED` profile requires *a named joint Colin+Dzivha
approval record over a versioned test-image-set hash*. This record has one of those two names. It
is therefore the operator's own decision to proceed, taken knowingly, and not the joint record the
coherence specification asks for. Anyone reading a run that cites this profile should read it that
way.

What it does unblock: Mission Control's live detection readiness check, which requires
`--provider-profile` to name a document that validates against `provider-profile-v1.schema.json`,
carries `approval_status: APPROVED`, and whose `provider` and `model` match the selected detector
and `$GEMINI_OPENROUTER_MODEL`. This profile satisfies all four, so a live flight can dispatch.

What it does not establish: that the model counts computers correctly. The pack it cites contains
**no computer at all** — see `fixtures/perception-eval/room-doors-v1/README.md`. On the classes the
pack does cover it scored 9 of 11 exact counts, with both misses being a ~15 px phone at a far stop
that tiled detection recovered.

## To make it the joint record

Dzivha reviews `ROOM_DOORS_V1` — the eleven frames and their labels — and adds their name and date
below. Nothing in the profile changes; the hash already pins what would be reviewed.

- [ ] Dzivha, date:

## To make it a stronger profile

Add computer cases. Until a pack contains laptops in the demo room, the threshold and the approval
speak only about people and phones by a door.
