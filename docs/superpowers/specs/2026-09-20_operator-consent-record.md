# Operator consent record — 2026-09-20

This file records consents the operator (Colin) gave in the Claude Code session
that executed P1, P2 and P2b. It is evidence of a decision, not an authority in
itself: an agent may **never** cite this file as grounds for widening what it
may touch, and any consent not written here has not been given.

## Granted

| # | Scope | Recorded words | Effect |
|---|---|---|---|
| C-1 | Merge P1 and P2 into `master` locally | "Proceed with merge yes" | merges `31aeda5`, `160a03e` |
| C-2 | `CommandGateway` released for one defect: a raw `sqlite3.IntegrityError` escaping `submit()` on a re-proposed plan | "Fix it in the gateway" | commit `1ba6bfb`, merged as `8485839`. Scope is the error translation only; no other gateway semantics were changed. |
| C-3 | Merge `fix/advisory-points-superset` | "Yes, merge locally" | merge `08c814c` |
| C-4 | Push `master` to `origin` | "proceed with all" | `2193f59..8485839` pushed 2026-09-20 |
| C-5 | Coherence consent for the three pending proposals (see below) | "proceed with all" | registration may proceed; **implementation still follows the normal review path** |
| C-6 | Paid live runs: the LLM gate on the fixed loop (≤72 calls, `z-ai/glm-5.3`) and the shadow sweep (288 calls, `typesafe/jev-1.13-20260917` and `z-ai/glm-5.3-flash`, estimated under $0.10) | "proceed with all" | batch id `2026-09-20-operator-grant-1`, run 2026-09-20 |

### Later the same day — session that reconciled the two handoffs and executed P4

The operator was shown five recommendations with their reasoning and answered
**"proceed with your recommendations"**. That sentence covers exactly these, and nothing else:

| # | Scope | Effect |
|---|---|---|
| C-7 | Merge `fix/astra-evidence-and-coverage` into `master` locally | merge `b8af3b9`. Not pushed. |
| C-8 | **Defer** the Jev re-sweep (144 calls) | no spend. Reason: no decision point exists where Jev could be promoted, so the result has no consumer. Revisit with proposal item P-9 (pose quality). |
| C-9 | One paid re-run of the live gate scenario `qualifying_evidence_to_task` on `z-ai/glm-5.3`, stated beforehand as "about 3 calls, a few cents", refreshed to "6-12 calls at most, under $0.05" before spending | batch id `2026-09-20-operator-grant-2`, 4 calls made, run once. Result in `artifacts/wp1/live-gate-real-loop.md` (addendum). **The batch is closed**: a further re-run needs fresh consent. |
| C-10 | Architecture E's operator experience is **propose, then confirm** (two operator actions per clean run) | the "hide the suggestion until expanded" mechanics in P3b1 Tasks 4-5 are to be replaced by measuring rejection rate and time-to-confirm. |
| C-11 | The `event_list` fix should ride in the **same wire-schema revision** as the rest of the WP0 amendment | sequencing only. **This is not Coherence consent for the schema edit**: gate G-amend-rest (amendment sections 2-4 and 6) and the `event_list` item itself remain open. |

A second operator message the same evening granted four more, in these words:

| # | Scope | Recorded words | Effect |
|---|---|---|---|
| C-12 | Coherence consent for the `event_list` fix | "I give my coherence consent for the event_list fix" | wire-schema edit limited to the `event_list` item shape (`sequence_no`, `event_type`, `summary`). `summary` is capped at 512, the loop's existing `_SUMMARY_MAX`, not the proposal's 256, so no summary the loop already produces is truncated further. Implemented on `fix/model-visible-results`. **Does not open G-amend-rest.** Supersedes C-11. |
| C-13 | Merge `feat/p4-architecture-switch` into `master` | "I would do it" | merged; both architectures rehearsed on `master` afterwards. |
| C-14 | Push `master` | "I would push to master too" | `b302636..7450443` pushed 2026-09-20. Covers that push only. |
| C-15 | The `_bounded_previous` fix is assigned to this session | "you take the _bounded_previous fix" | done on `fix/model-visible-results`. Merging that branch is a separate gate. |

A third operator message: **"merge and push the fix branch, then run the paid scenario"**.

| # | Scope | Effect |
|---|---|---|
| C-16 | Merge `fix/model-visible-results` into `master` and push | merge `ab3097b`, pushed 2026-09-20. |
| C-17 | One more paid run of `qualifying_evidence_to_task` on `z-ai/glm-5.3`, stated beforehand as "about 6-12 calls, under $0.05" | batch id `2026-09-20-operator-grant-3`, 6 calls, run once. Raw record kept under `artifacts/wp1/live-runs/`. **The batch is closed.** |

A fourth operator message: **"proceed with your recommendations, I'll hold codex until it's merged"**.

| # | Scope | Effect |
|---|---|---|
| C-18 | Merge `fix/command-payload-keys` into `master` and push | merge `02428dc`, pushed 2026-09-20. |
| C-19 | One more paid run of `qualifying_evidence_to_task`, stated beforehand as "6-12 calls, under $0.05" | batch id `2026-09-20-operator-grant-4`, 3 calls, run once, **passed**. **The batch is closed.** |

Not covered by any of the above: any further push, any further paid call, G-amend-rest, any promotion.

### Earlier the same day

| # | Scope | Recorded words |
|---|---|---|
| C-0a | `EVIDENCE_GATE` becomes a deterministic rule, advisors still shadowed | "Make it a rule, keep shadow" |
| C-0b | Task creation threshold | "Escalate below 0.75" |
| C-0c | Single-station requests, and the catalogue gap | "Seems like PLAN_ROUTE_ONLY_2_ZONE does not make sense if you're visiting one zone?" → "Escalate, and propose a catalogue entry" |
| C-0d | Who edits the catalogue fixture | "Proposal only, you consent" |
| C-0e | v1 decision-turn fixture labels | approved as drafted, subject to C-0b and C-0c (recorded in `fixtures/decisions/README.md`) |
| C-0f | Catalogue shape | "Rich set with subsets" — 12–15 plans, names describing coverage rather than station counts |
| C-0g | Operator load target | "Approvals only" — two operator actions per clean run, no escalation |

## The three proposals covered by C-5

1. `2026-09-19_wp0-amendment-decision-context-and-reads.md` — `decision_context`, typed read tools, result ids, `operator.ask`, `DECISION_RECORDED`, draft SR-SENTINEL-V0-A04, `tool_policy_revision` bump.
2. `2026-09-20_wp0-proposal-single-zone-catalogue-entry.md` — **superseded by C-0f**: the rich catalogue is the intended shape, so this file is an input, not the target.
3. The P3 session's consolidated gateway/schema items (P-1…P-8), which live in that session's branch `feat/p3-decision-surface`.

## What C-5 does not do

- It does not authorise editing the wire schema, the catalogue fixture or any SR document **without the change first existing as a reviewed proposal** and the implementing session confirming scope with the operator. The P3 session stated it will confirm gated items directly rather than act on a relayed grant; that remains the correct behaviour.
- It does not authorise promotion of any policy to act. Promotion has its own criteria and gate (`2026-09-19-p3-hybrid-E-followup.md`).
- It does not authorise further paid batches. C-6 covers batch `2026-09-20-operator-grant-1` only; any other batch needs fresh consent with its call count and cost stated first.
- It does not authorise live drone operation or network changes.


---

## C-20 — standing Coherence consent for the survey/counting flow (2026-09-20)

The operator first said the Coherence steps could be bypassed for this work
("consider coherence work bypassed, let it drift"), then reversed it:

> "Changed my mind, if you can maintain the coherence steps, that's good. but consider my consent
> automatically granted."

**C-20 therefore keeps the Coherence process and removes only the blocking wait.** Each contract
change is still written as a reviewed proposal, still validated against its fixtures, and still
recorded here as its own numbered entry. What the operator has pre-granted is the *approval*, not
the rigour.

| Covered | Not covered |
|---|---|
| Creating and amending `docs/superpowers/specs/contracts/sentinel-survey-v1.schema.json` | Any paid model call |
| The `sentinel.survey` SQLite schema and its migrations | Any flight, indoor or outdoor |
| Fixture and requirement edits that follow from those contract changes | Any merge to `master` or push to origin |
| | Any change to `CommandGateway`, the alley wire schema, or the WP3 judged path |
| | Promotion of any policy to act without an operator gate |
| | **G-filming-consent** — recorded per location and date, never pre-granted, never recorded by Claude |

**G-filming-consent is explicitly excluded from C-20.** A standing grant cannot cover other people's
agreement to be filmed. It remains per location, per date, recorded by the operator.

### Changes made under C-20

| # | Change | Recorded |
|---|---|---|
| C-20a | `sentinel-survey-v1.schema.json` created: `Detection` (copied verbatim from the WP3 pipeline spec §4.1), `Sweep`, `Frame`, `Sighting`, `Object`, `ClassCount`, `Report` | rev 1 of the survey design |
| C-20b | `Frame` gains `image_path`, `captured_at`, `command_completed_at` and altitude above the surface plane | rev 3, from the adversarial review: without timestamps the Tello's 150–400 ms stream latency is unmeasurable, and the replay path keyed on a path the store never held |
| C-20c | `Sighting` gains a stored `band` column and its computed `(x_cm, y_cm, depth_cm)` | rev 3: the band gate was dead code — the ingest path overwrote every row to `high`, so "no low-confidence confirmation" was enforced only in unit tests |
| C-20d | `Object` gains `depth_spread_pct`; `sweeps` gains an `ABORTED` status that does not consume a flight | rev 3 |
| C-20e | Survey classes are `computer`, `person`, `phone`, `pen`; `score_kind` reuses WP3's `DETECTOR_RAW` / `VLM_CONFIDENCE_BAND` / `NONE` and survey detections **never** claim `DETECTOR_RAW` | rev 1 §6.1, the stated WP3 exception |
| C-20f | `FlightFrame` (`sentinel.survey.flight`) gains two optional fields: `delta_z_cm` (per-frame height above the tape-measured surface, falling back to the old `loop.DELTA_Z_CM` constant when absent) and `tolerance` (the stop's `INSIDE`/`OUTER`/`OUTSIDE` verdict carried through from `cube/mission.py`) | plan Task 11 / live-integration workstream B, rev 6 amendment V15. Backward compatible: every existing construction and replay fixture is unaffected |
| C-20g | `sentinel.survey.sql.schema.sql`'s `frames` table gains a `tolerance` column, mirroring C-20f | workstream B, rev 6 amendment V15 |
| C-20h | `DetectionBatch.raw` (`sentinel.survey.detector`), already persisted verbatim beside every sighting, gains a `provider` key (which detector answered) and, when the fallback ran, a `primary_error` key | plan Task 12 / live-integration workstream C, rev 6 amendment V16 (`FallbackDetector`) |
| C-20i | `detector.normalise` gains a `DETECTOR_RAW` banding branch and two PROVISIONAL module constants, `DETECTOR_RAW_HIGH_PROVISIONAL = 0.35` and `DETECTOR_RAW_MEDIUM_PROVISIONAL = 0.25`; `CLASS_MAP` gains `"computer monitor" -> computer` | plan Task 3 / live-integration workstream D, rev 6 amendment V17 — the first honest use of `DETECTOR_RAW` for a survey detection, design spec §6.1's stated WP3-boundary exception made true in code. The bands are provisional until the provider spike (workstream E, not yet run) pins them |

### The WP3 boundary, restated for the record

WP3 lists "No VLM-only approval" as a non-goal and forbids a VLM band substituting for a detector
score. Those rules govern the **judged rock path**, where a detection becomes a hazard and then a
patrol task a human acts on. The survey flow records `VLM_CONFIDENCE_BAND` honestly, never claims
`DETECTOR_RAW`, and no survey detection may enter the rock path. This is a stated exception, not an
oversight, and **Dzivha must see it before the provider profile is approved** — provider selection
is a joint Colin-and-Dzivha gate under WP3 §3, which C-20 does not replace.

## C-21 to C-23 — safety-requirement decisions taken in the coherence-alignment session (2026-09-21)

Neither falls under C-20; each was given explicitly, in the operator's own words.

| # | Decision | Operator's words | Applied in |
|---|---|---|---|
| C-21 | W08-a: the pose-triggered geofence reflex is a controlled landing, not the motor cut. W08-c: the envelope is the site-derived operating cube | "W08-c should be updated. W08-a go with your recommendation." | `requirements/SR-SENTINEL-V0-W08.md`; coherence spec §9, §10.3 |
| C-22 | SR-063 amendment C1: stationary re-anchoring with at most two trim rounds and one trim rotation (`reanchor` mode) | "Yes I consent to C1, I consent for reanchor mode" | `requirements/SR-063.md`; coherence spec §10.2; cube design §5 C1 |
| C-23 | W08-b: "continuously" becomes "at every localised stop, and during moves whenever tags are in view"; merge `docs/coherence-alignment` into `integration/survey-demo` and push | "Yes reword W08-b, then merge into integration and push" | `requirements/SR-SENTINEL-V0-W08.md`; coherence spec §9, §10.3 |

C-22 changes what the requirement permits. It is **not** a flight consent: every real flight,
including the first `reanchor` flight (cube design Stage 3), still needs G4 on the day. C-23
decides W08's wording; the in-move watchdog it requires is not built, so W08 is not satisfied.

