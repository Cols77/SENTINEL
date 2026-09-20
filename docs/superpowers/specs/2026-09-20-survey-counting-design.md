# SENTINEL survey and counting — design (rev 1)

> **Status:** approved in brainstorm 2026-09-20; spec pending operator review
> **Owner:** Colin (operator), implemented by the P4/orchestrating Claude session
> **Targets:** recorded video 2026-09-25 (garden); live demo 2026-09-27 (lecture room)
> **Depends on:** WP5 cube navigation (`feat/sentinel-wp5-drone-pose`) merged to `master`

## 1. Goal

One agentic loop that answers a spoken-style request — *"Do a rail sweep in your allowed safety
space and count the computers"* — by proposing a sweep the operator approves, flying it, counting
what it saw, and reporting a number it can defend.

The rail sweep **replaces** the six-zone alley demo as the thing we show. The alley code stays on
`master`, keeps its tests green, and is not deleted.

### 1.1 What the agent decides, and what it does not

| Step | Owner |
|---|---|
| Turn the request into a sweep choice | agent, from a fixed list of code-generated options |
| Approve the sweep | **operator** |
| Fly it | code (WP5 mission runner) |
| Detect objects in frames | provider (VLM), through `PerceptionPort` |
| Merge sightings into distinct objects, count them | **code** |
| Decide whether to follow up, and with which option | agent |
| Approve the follow-up | **operator** |
| Word the report | agent, checked by code against stored counts |

The agent never writes coordinates, never counts, and never states a number the store does not
hold. It chooses among options that code has already proven fit inside the safety envelope.

### 1.2 Non-goals

Free-form waypoint authoring by the model; per-stop decisions while airborne; pose-dependent
missions; changes to `CommandGateway`; changes to the alley judged path; changes to any file Codex
owns in P3b1; **promotion of any policy to act without an operator gate**. Mode `J` (§7.3) is not
such a promotion: every proposal it makes still stops at operator approval.

## 2. Evidence and what already exists

| # | Fact | Source |
|---|---|---|
| E1 | The cube mission runner already flies a waypoint mission (≤ 8 stops, each `{name, x_cm, y_cm, z_cm, looks_deg, localise}`), validates the whole mission against the envelope before takeoff, and captures a frame at every look. | WP5 design §3.6, implemented Tasks 1–11, 273 tests green, **never flown** |
| E2 | Nothing connects those frames to a store, a detector or the agent loop. `master` has no `cube/` package. | repo inspection 2026-09-20 |
| E3 | The normalized detection shape already exists and carries class, matched prompt, raw score, **score kind**, pixel bbox, description and provider item id. | `docs/SENTINEL_Perception_Pipeline_Spec_v0.1.md` §4.1 |
| E4 | `score_kind: VLM_CONFIDENCE_BAND` already exists and already carries the rule that it must never be compared to the detector's numeric threshold. | same, §4.1 |
| E5 | `PerceptionPort.observe(request) -> result` already exists and is provider-neutral. | `src/sentinel/coordinator/ports.py` |
| E6 | Real hash-verified Tello footage is committed: 960×720, four-turn yaw scan, 7.8 MB. | `fixtures/perception/live/yaw-scan-20260916.h264` |
| E7 | Provider selection is a **named joint Colin-and-Dzivha approval** of provider, model, profile and `evaluation_pack_sha256`; the checked-in profile template stays `UNAPPROVED` until then. | WP3 §3 |
| E8 | VLMs are documented-poor at counting: best model 60 % counting to 20 with multiple shapes; 13–40 % on another benchmark; consistent **undercount** bias; failure modes are hallucination, clutter/occlusion, poor object individuation and double counting. | *Counting to Four is still a Chore for VLMs* (2026); *CounterCount* (2026) |
| E9 | VLM **grounding** is a different and far better-served skill. Moondream 3 reports RefCOCO 91.1 and ~788 ms, purpose-built for detect/point/count. Zero-shot VLMs still trail specialist detectors on precise localisation. | Moondream docs; Roboflow100-VL |

**E8 and E9 together are the load-bearing evidence for this design**: the provider is asked *where
is each object*, never *how many are there*. Counting is arithmetic over stored rows.

## 3. Architecture

### 3.1 Placement — a sibling package, not a generalised coordinator

`sentinel.survey` is a new package with its own contract file and its own SQLite tables. It does not
touch `CommandGateway`, the alley schema, or any P3b1 file.

Rejected alternative: generalising the existing coordinator (stations → stops, classes opened up,
gateway taught about sweeps). That rewrites the gateway, the main schema and `agent_loop.py` five
days before a recording, and `agent_loop.py` is actively owned by Codex. Also rejected: routing
sweeps through the existing gateway as catalogue plans, which would require inventing fake alley
stations to pass plan validation.

**Cost accepted:** two flows with two approval paths until after 2026-09-27. The survey flow can
change exactly two things — propose a sweep (operator-approved, envelope-checked before takeoff) and
file a report (code-checked). §12 records the convergence intent.

### 3.2 Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `survey.options` | Generate legal sweep options from the site file + envelope, each with its metrics. Deterministic. | `cube.site`, `cube.observe` |
| `survey.store` | Own the SQLite tables: runs, sweeps, frames, sightings, objects, reports, events. | — |
| `survey.detector` | Implement `PerceptionPort`. Frame in → normalized detections out. Real provider, replay, and scripted implementations. | `coordinator.ports` |
| `survey.merge` | Sightings → distinct objects with confidence bands. Pure geometry + appearance. | — |
| `survey.observability` | Compute, per class and range, whether the class is resolvable at all. | — |
| `survey.candidates` | Build the complete legal tool calls at each decision point, arguments already filled from the store. The input every proposer sees. | `survey.options`, `survey.store` |
| `survey.proposer` | `RuleProposer`, `LlmProposer`, `JevProposer` — three implementations of the one `ContextualPolicy` seam (§7.3). | `survey.candidates` |
| `survey.flight` | One interface, two implementations: real mission runner, and replay from a frame folder. | `cube.mission` |
| `survey.loop` | The eight-step run; enforces the two-flight cap and the report check. | all of the above |
| `survey.eval` | Episode harness: replay a scene, score the run against ground truth. | `survey.loop` |

Reused unchanged: the model adapter, the `ContextualPolicy` seam (so architecture F/E selection and
Codex's `LlmProposer` both work here), the raw-record persistence pattern from the live gate, and
the WP5 mission runner.

### 3.3 The run, step by step

1. **Request.** Free text from the operator.
2. **`survey.get_site`** returns the perimeter and the legal sweep options with their metrics
   (stop count, viewed extent, estimated flight time, resolvable classes at that range).
3. **`survey.propose_sweep`** — one option id plus a reason. The run parks in
   `WAITING_FOR_APPROVAL`.
4. **Flight.** `survey.flight` executes; the loop is idle while airborne.
5. **Ingest.** Detector over every frame → sightings. Merge → objects. Counts per class.
6. **`survey.get_results`** returns counts with bands and methods, the uncertain objects with the
   option that would view each one better, unanalysed frames, and per-class observability.
7. **Report, or one follow-up.** A follow-up re-enters step 3. **Hard cap: two flights per run.**
8. **`survey.submit_report`** — the agent's wording and caveats. Code rejects any number that
   disagrees with the store.

Four tools, two read and two write. Both writes are inert without operator approval or the code
check.

## 4. Contract (`sentinel-survey-v1.schema.json`)

### 4.1 Reused verbatim from E3

`Detection` is the pipeline spec's §4.1 shape, unchanged: `class`, `matched_prompt`, `raw_score`,
`score_kind`, `bbox` (nullable, `PIXELS_TOP_LEFT`), `bbox_coordinate_space`, `description`,
`provider_item_id`. **A box is never manufactured from prose or a guessed location** (§4.1's own
rule, and the property the merge depends on).

### 4.2 New shapes

| Shape | Carries |
|---|---|
| `Sweep` | option id, stops actually flown, pose mode, start/end time, status |
| `Frame` | sweep id, stop index, look offset, drone position and heading, **`position_kind: COMMANDED \| MEASURED`**, artifact id + sha256, analysis status |
| `Sighting` | frame id + one `Detection` |
| `Object` | class, estimated position, supporting sighting ids, distinct stop count, `band: CONFIRMED \| UNCERTAIN`, appearance summary |
| `ClassCount` | class, confirmed count, uncertain count, `method: MERGED_MULTI_VIEW \| SINGLE_VIEW_LOWER_BOUND \| NOT_OBSERVABLE` |
| `Report` | class counts, agent caveats, flights used, unanalysed frame count |

`position_kind` exists because WP5 pose is optional: a stop flown with `--pose off` is `COMMANDED`,
and the merge widens its tolerance accordingly.

### 4.3 Classes

`computer`, `person`, `phone`, `pen`. Set now so Dzivha's WP3 detector fills them later without a
second contract change.

| Class | Method | Why |
|---|---|---|
| `computer` | `MERGED_MULTI_VIEW` | ~80 px at 3 m; the target of the demo |
| `person` | `SINGLE_VIEW_LOWER_BOUND` | people move between stops, so position-merging is invalid; and they are not on a fixed plane |
| `phone` | `MERGED_MULTI_VIEW`, usually `UNCERTAIN` | ~34 × 16 px at 3 m; marginal |
| `pen` | `NOT_OBSERVABLE` at demo range | ~2 px wide at 3 m; below any detector |

People are counted as **the largest number seen in any single frame**, reported as *"at least N, as
of HH:MM:SS"*. It is a lower bound because occluded people are missed, and the report says so.

## 5. The merge

### 5.1 Camera model

Tello: 960 × 720, 82.6° **diagonal** FOV. Half-diagonal 41.3°, image half-diagonal 600 px, so

```
f = 600 / tan(41.3°) ≈ 683 px
horizontal FOV = 2·atan(480/683) ≈ 70.2°
vertical   FOV = 2·atan(360/683) ≈ 55.6°
```

An object of real size `S` at distance `D` spans `S·f/D` pixels. At `D` = 3 m:

| Object | Size | Pixels at 3 m |
|---|---|---|
| person (standing) | 150 cm | ~341 |
| laptop | 35 cm | ~80 |
| phone | 15 × 7 cm | ~34 × 16 |
| pen | 14 × 1 cm | ~32 × **2** |

These numbers justify §4.3 and are recomputed per sweep by `survey.observability` at that sweep's
actual range, so a closer follow-up can legitimately change a class from not-observable to
observable. **This is what gives the follow-up flight a real reason to exist**; without it the
second flight would be theatre.

### 5.2 Triangulation

Each sighting yields a **bearing** from the box's horizontal centre: `θ = atan((u − 320)/f)` in the
drone frame, plus the frame's position and heading. Two sightings may be the same object when they
share a class, come from different stops, and their rays cross in front of both at 0.5–12 m.
Crossing points are clustered; tolerance grows with distance.

**Only a bearing is needed — not a tight box.** This is the least demanding thing a grounding model
can produce and is why a *pointing*-capable provider is acceptable even if its boxes are loose
(E9).

### 5.3 Why the main rail never rotates

Depth from a baseline `B` and a bearing difference `Δθ` behaves as `D ≈ B/Δθ` for small angles, so

```
relative depth error ≈ (ΔB/B)  ⊕  (Δ(Δθ)/Δθ)
```

At `D` = 3 m with `B` = 75 cm, `Δθ` ≈ 0.25 rad (an object shift of ~170 px between neighbouring
stops — a strong signal):

| Error source | Magnitude | Depth impact |
|---|---|---|
| position error 15 cm on a 75 cm baseline | ΔB/B = 20 % | ~60 cm |
| **differential** yaw of 5° between stops | 0.087 rad on 0.25 rad = 35 % | ~105 cm |
| 1 px bearing noise | 0.084° | negligible |

A **constant** yaw offset cancels exactly, because `Δθ = θ₂ − θ₁` and both shift equally; it
rotates the whole scene without distorting it. **Differential** yaw does not cancel, and it is
precisely what rotate-to-look-then-rotate-back introduces at every stop. Therefore the main rail
holds a single heading for its whole length. Follow-up close looks may use `looks_deg`; their
sightings carry a widened tolerance.

### 5.4 Bands, and the honest failure direction

`CONFIRMED` requires ≥ 3 distinct stops with a non-low band. `UNCERTAIN` otherwise, or when a
sighting could belong to two clusters. Appearance (`description`) breaks ties between
geometrically-ambiguous clusters of the same class — valuable in a sparse garden scene, weaker in a
row of identical laptops.

**The dominant error is undercount**: two true objects closer than the merge tolerance collapse into
one. This runs in the same direction as the VLM's own documented undercount bias (E8). The system
must therefore never present a bare number where a range is honest, and the report format supports
*"at least 6, possibly 7"*.

## 6. Detector and provider

### 6.1 Selection, and the WP3 boundary

WP3 lists **"No VLM-only approval"** as a non-goal, and the pipeline spec §6.1 ranks VLMs third, as
"semantic verification candidates, not presumed detector-equivalent sources". Those rules govern the
**judged rock path**, where a detection becomes a hazard and then a patrol task a human acts on.

This spec does not weaken them. Instead:

- the survey flow records `score_kind: VLM_CONFIDENCE_BAND` honestly and **never** claims
  `DETECTOR_RAW`;
- no survey detection may enter the rock path, the alley schema, or any hazard/task decision;
- the survey flow's consequence is a **stated count with caveats**, not a dispatched human.

This is a **stated exception, not an oversight**, and Dzivha must see it before the provider profile
is approved (E7).

### 6.2 Provider

**Primary: Moondream 3** (`detect`). Purpose-built for detect/point/count, normalized `(x, y, w, h)`,
~788 ms, $0.30/M in and $2.50/M out with three tokens per box, $5/month free credits. **Fallback:
Gemini Flash** — `[ymin,xmin,ymax,xmax]` scaled 0–1000 plus masks, a different architecture that
fails differently.

**Settled by a day-one spike, not by this document.** The pipeline spec §6 is explicit: *"A provider
must not be selected merely because a model page or repository exists."* The spike runs both on E6's
committed Tello footage plus garden stills (~50 calls, a few cents) and measures: does every named
object carry a usable location; miss rate; invention rate; latency; behaviour on an empty scene.

### 6.3 Adapter rules

Inherited from pipeline spec §6, unchanged: pin the model version; bounded timeouts; cap image and
response size; validate status, content type and shape; preserve provider request id and the
**actual resolved model identity**; typed errors for timeout/rate-limit/auth/transport/schema; never
log image bytes or credentials.

Every raw provider response is stored beside its frame, so replay and re-scoring never call the API
again. (Same lesson as the live-gate work: persist raw rows before scoring.)

## 7. Jev

### 7.1 What Jev can actually do here

Jev converts a fuzzy input into a typed value drawn from a set **the caller enumerates**. The input
may be free text, a state projection, or a table of detections; the constraint is not "text only",
it is that the answer must be one of the listed options. Jev cannot produce a UUID, a sentence, or
any value outside the schema — but it *can* choose among complete, pre-assembled tool calls.

That split is already this project's design
(`docs/superpowers/specs/2026-09-19_jev-integration-architecture.md` §2): Jev picks from the legal
set, and **arguments are filled deterministically from state**.

The survey flow fits that far better than the alley flow did:

| Decision point | Legal set | Arguments |
|---|---|---|
| propose a sweep | ~5 options + escalate | entirely from the site file |
| review results | report, or follow-up with option *i* | entirely from the store |

Both sets are small, fully enumerable, and contain **no free text anywhere**. Code can therefore
pre-build every candidate call complete with arguments, and Jev's job reduces to picking an index.

### 7.2 Three roles

1. **Request → typed fields.** Batched atomic questions — which classes, exact or approximate, was a
   perimeter named, was a sweep type named — which code combines into an option set. TypeSafe's own
   guidance: *"decompose a judgment into atomic questions, then combine them with weights and
   thresholds you own."* Batching is near-free.
2. **Whole-decision choice.** `Choice` over the pre-built candidate calls of §7.1.
3. **Guardrail.** `Noul` over the LLM's output: does the proposed sweep match the request; does the
   report claim anything the counts do not support. Disagreement is recorded and surfaced at
   approval time.

### 7.3 Jev may propose — a third architecture mode

`JevProposer` is a third implementation of the same `ContextualPolicy` seam P4 already provides:

| Mode | Who proposes at the decision points |
|---|---|
| `F` | deterministic rules |
| `E` | the LLM |
| `J` | **Jev**, choosing among pre-built candidate calls |

**A proposal is not an act.** The run parks in `WAITING_FOR_APPROVAL` and the operator decides, so a
wrong Jev pick costs one rejection, not a wrong flight. This is the identical argument P3b1 already
makes for `LlmProposer` needing no promotion evidence, and it applies unchanged to Jev. Earlier
drafts of this spec put Jev in shadow at both points; that was a failure to apply the project's own
reasoning, not a finding about Jev.

Invariants that do not move:

- Jev never decides **whether a flight happens**; the operator does, and the envelope check runs
  regardless of mode.
- No Jev output bypasses the two-flight cap or the report consistency check.
- Promotion to act **without** an operator gate still requires the full criteria (n ≥ 60 HIGH picks,
  `false_act == 0`, Wilson lower 95 % ≥ 0.93, ≤ 1 flip per 20 repeats, median latency ≤ 1500 ms,
  operator sign-off). Mode `J` claims none of that and is not a promotion.

### 7.4 Compensating for Jev's missing rationale

Jev returns **no natural-language rationale at all** — a documented weakness, and a real one when
the operator is approving a flight. Mode `J` therefore has code generate the justification from
Jev's typed fields and the option's own metrics:

> You asked for: left side, all depths, no marker check.
> `RAIL_MID_HEIGHT` — 3 stops, views 4.2 m of table, 90 s, resolves `computer` and `person`.

This is *derived*, not written, so unlike model prose it cannot be wrong about itself.

### 7.5 What this buys

The same replayed scenes run through `F`, `E` and `J` and are scored against the same ground truth
(§9), at zero marginal cost for the replayed arms. That is the survey-shaped Jev evidence which does
not exist today — and the sharpest available test of whether the decomposed question shape is what
Jev was missing. See `docs/handoffs/2026-09-20-HANDOFF-to-codex-jev.md`.

## 8. Failure handling

| Failure | Behaviour |
|---|---|
| Provider names an object but returns no location | sighting stored with `bbox: null`; unmergeable; counted `UNCERTAIN`; surfaced to the agent |
| Provider error or timeout | frame marked unanalysed, never silently dropped; report states *"6 of 8 frames analysed"* |
| Outdoor drift | stops are `COMMANDED`; if passive pose reports drift beyond threshold the sweep is marked degraded and merge tolerance widens |
| Ambiguous cluster | stays as separate `UNCERTAIN` objects; count reported as a range |
| Two flights used | third proposal refused by code |
| Report contradicts the store | rejected; the agent rewrites or the run ends reportless |
| Mission fails preflight/envelope | never took off; nothing to reconcile |

Every failure degrades the **claim**, not the run. An agent that says *"at least 6, possibly 7; two
frames unanalysed; pens not resolvable at this range"* is demonstrating competence, not weakness.

## 9. Evaluation

Neither existing evaluation can score this: the decision fixture scores one request against one
label, and the live gate checks three scripted scenarios. Multi-turn behaviour needs an episode
harness.

### 9.1 Two ground truths

- **Frame level** — hand-labelled boxes on a few dozen real frames, built to the pipeline spec §9
  checklist (varied distance, illumination, motion blur, hard negatives, at least one frame
  representative of the choreography). Measures the provider alone. Identified by
  `evaluation_pack_sha256` (E7).
- **Episode level** — a scene manifest recording what was really present and where. Measures the
  whole loop.

### 9.2 Scored separately, so failure is attributable

| Metric | Answers |
|---|---|
| count error per class | is the headline number right |
| localisation (matched within a radius) | is it right for the right reason |
| decision quality | did the proposed follow-up address the objects that were actually uncertain |
| turns and model calls | what the reasoning cost |
| illegal calls | must be 0 |
| report consistency | must be 0 by construction; non-zero is a bug |

**Every scene is scored in all three modes.** `F`, `E` and `J` (§7.3) see identical replayed frames
and identical ground truth, so the comparison is free for the replayed arms and controls for
everything except who proposed. This is the measurement that does not exist today for Jev.

An aggregate score would hide whether a wrong count came from the provider, the merge or the
planning. **Decision quality is the metric that answers "can it think across turns"**: build a scene
with something deliberately hard to see, then check whether the agent noticed and whether its
follow-up was the option that addressed it. An agent that reports a confident number without
noticing the ambiguity scores well on count error and zero here — exactly the distinction worth
measuring.

### 9.3 Free and repeatable

Every run replays from stored frames and stored provider responses. Changing a merge tolerance
re-scores every past run in seconds, with no API calls and no flying.

## 10. Test strategy

- **Hermetic** — scripted detector, replay flight, fixed site file. Covers options generation,
  merge geometry (including synthetic scenes with known truth), observability arithmetic, banding,
  the two-flight cap, the report check, and every failure row in §8.
- **Replay** — the real garden frames with recorded provider responses. Covers the whole loop end to
  end at zero cost.
- **Live** — one garden capture, then one full-loop garden run. Paid calls; operator-gated.

No existing test is modified. A failing existing test is a finding, surfaced, not edited.

## 11. What changes where

```text
src/sentinel/survey/{options,candidates,proposer,store,detector,merge,observability,flight,loop,eval}.py
src/sentinel/survey/sql/schema.sql
docs/superpowers/specs/contracts/sentinel-survey-v1.schema.json
docs/superpowers/specs/contracts/provider-profile-<chosen>.json        # after E7 joint approval
fixtures/survey/{scenes/,frames/,responses/}
tests/survey/…
scripts/survey_run.py                                                   # CLI
```

Touched outside the package: nothing on `master` except new files. **No** change to
`CommandGateway`, the alley schema, `agent_loop.py`, or any P3b1 file.

## 12. Convergence after 2026-09-27

Two approval paths is a temporary cost, not the end state. After the demo, the intended
consolidation is: the survey flow's two mutations become command types on the existing gateway, and
`sentinel.survey` keeps only its detector, merge and evaluation. That work is out of scope here and
must not be started before 2026-09-27.

## 13. Schedule and gates

| Day | Work | Gate |
|---|---|---|
| 09-20 | Provider spike on E6's committed footage. Merge WP5 to `master`. Package skeleton: contract, store, options, candidates, detector port, replay flight. All hermetic. | **WP5 merge**. **~50 paid calls**. |
| 09-21 | Merge geometry, counting, observability, report check, episode harness; mode `F` end to end on synthetic scenes. **Garden capture** — short rail, frames only, no loop. | **First real flight**. **G-filming-consent** if anyone is in frame. |
| 09-22 | Real frames in; tune merge tolerances; modes `E` and `J`; full-loop garden run. | **Paid LLM/Jev calls**. |
| 09-23/24 | Buffer: re-capture, polish, and whatever the first two days broke. | — |
| 09-25 | Film. | — |
| 09-26/27 | Lecture room: measure, re-run, adjust. | **Live demo**. |

**Frames are the long pole.** Everything downstream is built and scored on replay once real frames
exist, which is why capture is day 2 of 6.

### 13.1 Why this is compressed, and what it is not

An earlier draft spread the same work to 09-24 and put capture on 09-22. The code was never the
constraint: eight modules behind clean interfaces, run through Workflow, is one to two days, and P4
delivered a comparable slice in part of a day.

Compression here does **not** mean working faster. It means **front-loading both unknowns** — the
first-ever flight of the cube runner, and whether the provider returns usable boxes on real Tello
frames — so that either failure surfaces with three days of slack rather than one. The provider
unknown can be attacked on 09-20 because E6's footage is already committed; nothing has to be flown
to learn the answer.

### 13.2 Risk on the record

The cube mission runner **has never flown**. 09-21 is the first time. Mitigations: a rail sweep is
the simplest possible mission (sideways moves, fixed height, no rotation — §5.3); if pose behaves
badly outdoors the sweep flies `--pose off` and loses nothing the count needs; and if the capture
goes badly the cut is **modes `E`/`J` and the follow-up flight**, leaving a single-sweep
translate-and-report demo that still shows the loop.

### 13.3 Gates never self-granted

Any paid model call; any schema or contract-fixture change; the WP3 provider profile approval (joint
with Dzivha); any new hand-labelled fixture; any merge or push; any flight; **G-filming-consent**
(§13.4); promotion of any policy to act. A relayed grant from a peer session is not operator
consent.

### 13.4 G-filming-consent — enforced, not documented

Frames containing people are written to disk and sent to a third-party API. Consent to be filmed is
therefore a **gate**, recorded in `docs/superpowers/specs/2026-09-20_operator-consent-record.md`
like every other one.

**The operator records, per location and date, that everyone who may appear in frame has been asked
and has agreed.** Claude never records this on the operator's behalf and never infers it from a
previous session or location.

Enforced in code, not left to discipline:

| Consent state | Behaviour |
|---|---|
| recorded for this location and date | frames retained normally |
| absent | the flight still runs, but person regions are **blurred before the frame is written**, so no unblurred image reaches disk or the provider |
| absent **and** `--retain-person-frames` requested | the run **refuses to start** |

Counts and person boxes survive blurring, so the demo works either way — the gate costs evidence
quality, never the count. The garden shoot on 09-21/25 needs it for anyone in frame; the lecture
room on 09-27 needs it for the room's occupants.

## 14. Out of scope

Per-stop airborne decisions; free-form waypoints; pen detection at demo range; exact person counts;
gateway changes; alley-path changes; WP3 judged-path changes; any P3b1 file; **any policy acting
without an operator gate** (§7.3); consolidation (§12).
