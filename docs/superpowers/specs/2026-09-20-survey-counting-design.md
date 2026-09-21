---
id: sentinel-survey-counting-design
title: "SENTINEL survey and counting - design"
status: review-pending
---

# SENTINEL survey and counting — design (rev 6)

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

Reused unchanged: `coordinator.legal_tools.Candidate` and `LegalSet` (both fully generic), the model
adapter, `coordinator.shadow.AuditSink`, the raw-record persistence pattern from the live gate, and
the WP5 mission runner.

**Not** reusable, verified 2026-09-20: `coordinator.policy.PolicyContext` types its `state` field to
the alley's `DecisionState`, so P3b1's `LlmProposer` cannot take a survey context. The survey flow
defines its own `SurveyContext` and `SurveyPolicy` with the same method shape. What transfers from
P3b1 is the **validation pattern** — exactly one call, a legal tool, a legal label, a non-blank
reason, fail closed on anything else — not the class. An earlier revision of this spec claimed the
class plugged in unchanged; that was wrong.

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
| `Frame` | sweep id, stop index, look offset, drone position, heading **and altitude above the surface plane**, **`position_kind: COMMANDED \| MEASURED`**, artifact id + sha256, **`image_path`**, **`captured_at` and `command_completed_at`**, analysis status |
| `Sighting` | frame id + one `Detection`, its stored **`band`**, and its computed `(x_cm, y_cm, depth_cm)` |
| `Object` | class, position, supporting sighting ids, distinct stop count, `band: CONFIRMED \| UNCERTAIN`, `depth_spread_pct`, appearance summary |
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

> **Rev 5 correction (2026-09-21).** The model below the line was derived from the datasheet and
> is wrong for real frames. The repository already held the measurement that refutes it: the
> chessboard calibration in `calibration/tag-map-calibrated.json`, validated by WP5's pose accuracy
> (1.0 cm at 40 cm). The coherence spec §10.5 now makes that calibration the single camera model.
>
> | | Datasheet-derived (rev 1–4) | Measured |
> |---|---|---|
> | focal length | 683 px | 892.6 / 893.7 px |
> | principal point | (480, 360) | (467.8, 353.8) |
> | field of view | 70.2° × 55.6° | **56.5° × 43.9°** |
> | optical axis | level | pitched **8.6° down**; horizon near row 219 |
>
> Three consequences. **Objects are 31 % larger in pixels** than the table below says (laptop ≈ 104 px
> at 3 m, phone ≈ 45 × 21, pen ≈ 3 px wide), so a phone stays above the 20 px floor out to 6.7 m —
> pens remain not observable. **Each frame covers a narrower strip**: 3.2 m wide at 3 m, not 4.2 m;
> the option generator recomputes this per camera. **Depth cannot use the level shortcut** — see §5.2.
>
> The code carries both models (`survey.observability`): `SYNTHETIC_LEVEL`, the datasheet model,
> which the synthetic evaluation scenes are rendered with and must therefore be inverted with; and
> `TELLO_MEASURED`, which every path touching real Tello pixels passes explicitly
> (`scripts/survey_run.py --camera tello-measured`). Mixing them misplaces every object.
> `tests/survey/test_camera_model.py` fails if `TELLO_MEASURED` drifts from the calibration file or
> from the pose package's pitch. The calibration is one airframe's; a different drone is
> re-calibrated first. Lens distortion is ignored: it is about 1 % at the frame's side edges and
> grows to about 10 % only in the far corners.

*Original derivation, kept because the synthetic scenes use it:*

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

### 5.2 Depth comes from the ground plane, not from the baseline

> **Rev 3 correction.** Rev 1–2 made cross-stop triangulation the primary measurement. An
> adversarial review refuted that, and the arithmetic was reproduced and confirmed (§5.2.1). Depth
> now comes from the **vertical** bearing against a known surface height. The baseline survives only
> as a cross-check.

> **Rev 5 correction (2026-09-21).** The measurement is the intersection of the pixel's ray with the
> horizontal plane `Δz` below the lens. With the box centre at normalised image coordinates
> `x_n = (u − cx)/fx`, `y_n = (v − cy)/fy` and the optical axis pitched down by `p`:
>
> ```
> scale   = Δz / (sin p + y_n · cos p)          # defined only below the horizon
> forward = scale · (cos p − y_n · sin p)
> lateral = scale · x_n
> ```
>
> `D = Δz · f / v` is the special case `p = 0`. On the real camera (`p` = 8.6°), an object 3 m
> ahead on a plane 55 cm below the lens sits **28 px** below the principal row, not 125 px, and one
> pixel is worth **1.9 cm** of depth there. Fed real pixels, the level formula would have put that
> object beyond 17 m, and refused outright anything past about 3.5 m, whose box rises above the
> image centre while still being well below the horizon.
>
> Rev 5 fixes two further errors the first one had been hiding. `D` is a **forward** distance, and
> rev 3–4 used it as a slant range along the bearing, shortening every off-axis position by
> `cos(bearing)` — up to 18 % at the frame edge. And with positions now exact, two rules in §5.4
> misfired: the near-tie ambiguity rule fired on every well-measured object (all its sightings are
> near-equidistant *because* they agree), and `depth_spread_pct` compared viewing depths, which
> differ legitimately when one object is seen from two distances — so a closer follow-up marked
> everything it re-saw as degraded. A near-tie is now ambiguous only when the third sighting could
> *not* also join the pair, and the spread is the widest gap between the positions the sightings
> recover, as a fraction of the median depth.
>
> **New sensitivity: pitch.** One degree of unmodelled pitch moves a 3 m estimate by about 27 cm
> (9 %). The 8.6° was measured with the drone on the floor; in a hover the airframe tilts to hold
> position, more so in a garden breeze. §5.4's spread check is what detects this, and the first
> flight's tape-measured truth is what calibrates it.

**Primary measurement (rev 3 text, now the level special case).** Objects rest on a surface whose height is known (a garden table, a lecture
desk). With the camera `Δz` above that surface and the object's box centre `v` pixels below the
image centre:

```
D = Δz · f / v
```

At `Δz` = 55 cm and `D` = 3 m, `v` = 125 px and `dD/dv` = **2.40 cm per pixel**. A 5 px error is
12 cm of depth error. The inputs are the box, the camera's **altitude** — which the Tello measures —
and one tape-measured surface height. Lateral position, which the drone only *commands*, does not
enter at all.

**Horizontal position** still comes from the horizontal bearing, `θ = atan((u − 480)/f)`, where 480
is half the 960 px frame width. Combined with `D`, one sighting gives a full `(x, y)` from **one
frame**.

**Association, not triangulation.** Two sightings are the same object when they share a class and
their independently-computed positions agree within tolerance. Crossing rays are no longer used to
*derive* position, only to corroborate it. This removes the correspondence problem entirely: there
is nothing to cross, so there are no phantom crossings.

#### 5.2.1 Why the baseline method failed

Depth from two bearings is `D_est = D · B_believed / B_actual`. Stop positions are *commanded*, not
measured, with ~15 cm error at **each** end, so a believed 75 cm baseline is actually 45–105 cm and
the depth estimate for a single object at 3 m ranges 214–500 cm:

| stop pair | B believed | B actual | D estimated |
|---|---|---|---|
| 0–1 | 75 | 45 | **500** |
| 1–2 | 75 | 105 | **214** |
| 0–2 | 150 | 150 | 300 |
| 0–3 | 225 | 195 | 346 |

Clustered, that single laptop becomes **two `CONFIRMED` objects plus one `UNCERTAIN`** — confidently
wrong, in the sparse garden scene. Three further defects made it worse:

- **`1/B_actual` is convex**, so the estimate is biased long; spurious clusters land *behind* the
  truth rather than scattering around it.
- **Evenly spaced objects generate an organised phantom lattice.** Rays to object *n* from one stop
  cross rays to object *n+k* from another at a depth independent of *n*, so ghosts form their own
  tidy row, draw sightings from every stop, and pass `distinct_stops ≥ 3` as `CONFIRMED`.
- **No tolerance value works.** Separating a lecture-room laptop row (60 cm spacing) needs < 30 cm;
  holding one object together under 15 cm position error needs > 150 cm. Mutually exclusive by 5×.

Rev 1–2's error table also charged `ΔB/B` = 20 % for a 15 cm error, which assumes only one endpoint
is wrong. Both are, independently: up to 30 cm on 75 cm is 40 %.

### 5.3 Why the main rail never rotates

Still true, and now for a second reason.

A **constant** yaw offset cancels in any bearing *difference*; **differential** yaw does not, and
rotate-to-look-then-rotate-back introduces exactly that at every stop. At 3 m, 5° of differential
yaw is 26 cm of lateral error.

Under §5.2 the rail also must not rotate because **`Δz` must stay known**: pitch changes the
relationship between image row and ground plane. The mission holds one heading and one altitude for
its whole length, and `looks_deg` is reserved for follow-up close looks, whose sightings carry a
widened tolerance and are excluded from ground-plane depth unless the pitch is recorded.

### 5.4 Bands, exclusivity, and the honest failure direction

**Each sighting belongs to exactly one object.** Association runs as mutual-nearest-neighbour: a
sighting is matched to its closest compatible object and to no other. Rev 1–2 accumulated sightings
into every cluster they touched, which is what let phantoms reach `distinct_stops ≥ 3`.

`CONFIRMED` requires ≥ 3 distinct stops **and** no contributing sighting below the `medium` band —
enforced against a stored `band` column, not a value the ingest path overwrites. `UNCERTAIN`
otherwise, or when a sighting is nearly equidistant from two objects.

**Depth agreement is itself evidence.** If the per-sighting depths for one object spread by more
than 30 % of their median, the geometry has failed for that object: it stays `UNCERTAIN` and the
sweep records `geometry_degraded`. If more than half the objects are degraded, the whole sweep falls
back to `SINGLE_VIEW_LOWER_BOUND` for every class and says so.

Appearance (`description`) breaks ties between geometrically-ambiguous candidates of the same class:
useful in a sparse garden, weak in a row of identical laptops, and never load-bearing.

**The dominant error is undercount** — two true objects inside the tolerance collapse into one —
which runs the same way as the provider's own documented undercount bias. The report therefore
supports a range and never forces a bare number.

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

> **Rev 6 correction (2026-09-21).** §6.1 as written above describes an intent; the code now makes
> it true. `sentinel.survey.detector.normalise` gained a `DETECTOR_RAW` branch (plan workstream D):
> a bridged detector item carrying a numeric `score` and `"score_kind": "DETECTOR_RAW"` is banded by
> two module constants named and marked **PROVISIONAL** —
> `DETECTOR_RAW_HIGH_PROVISIONAL = 0.35`, `DETECTOR_RAW_MEDIUM_PROVISIONAL = 0.25` — a starting point
> for the bridge's output, not a measured cutoff; the provider spike (workstream E, not yet run) is
> what pins them for real. A `VLM_CONFIDENCE_BAND` sighting is never compared against these
> thresholds, and no `DETECTOR_RAW` sighting enters the rock path — both rules from §6.1 hold
> unchanged in code. See §6.4 for what else workstreams C and D actually built.

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

### 6.4 Three detector sources, and what the flight adapter stamps onto a frame (rev 6, 2026-09-21)

Rev 5 (§5.1) fixed the camera model workstream A actually built. This section records what
workstreams B, C and D built on top of it — nothing here changes an architectural decision made
earlier in this document; it records what now exists in code.

**Three sources implement `DetectorPort`, combined only as primary + fallback — no box fusion, as
already decided in the plan's context.** `FallbackDetector(primary, fallback)`
(`sentinel.survey.detector`) tries `primary` first and calls `fallback` only when `primary` raised a
`ProviderError` or returned a `"PROVIDER_ERROR"` batch (a `CLASS_MAP_MISS` batch is a substantive
answer, not a trigger). The returned batch's `raw` always records `provider`: which detector
actually answered, and, when the fallback ran, `primary_error`: the primary's exception class name.
That raw dict is what `loop.py` persists beside every sighting (§6.3), so a replayed run can show
which provider spoke without re-deciding it.

| Source | Module | `score_kind` | Notes |
|---|---|---|---|
| Moondream 3 (primary) | `survey.providers.moondream` | `VLM_CONFIDENCE_BAND` | one request per class (`object` is singular in the real API — the defect C1 found); base64 image body |
| Gemini Flash (fallback) | `survey.providers.gemini` | `VLM_CONFIDENCE_BAND` | `[ymin,xmin,ymax,xmax]` scaled 0–1000, converted to top-left pixels |
| Dzivha's Grounding DINO, via the offline bridge | `scripts/survey_detect_gdino.py` → `ReplayDetector` | `DETECTOR_RAW` | not a live `DetectorPort` implementation: it runs once, offline, and writes response files `ReplayDetector` then serves; §6.1's stated WP3-boundary exception |

Both VLM adapters send the image as base64 and are covered by a fake-session test asserting the
request payload decodes back to the frame's exact bytes — the defect C1's read of the real API docs
found (image bytes were never sent at all in the pre-C1 code).

**Per-frame `delta_z_cm` replaces the single `DELTA_Z_CM` constant for a real flight.**
`FlightFrame` (`sentinel.survey.flight`) gained an optional `delta_z_cm` field; when a frame does not
carry one (every replay fixture, and Codex's `scripts/survey_sweep.py` harness), `loop.py` falls back
to the old constant, so nothing existing changed shape. A `CubeFlight`-produced frame always carries
its own value: the runner's measured height reading at that stop minus the tape-measured surface
height passed in at the CLI (`--surface-z-cm`).

**The flight adapter stamps `MEASURED` or `COMMANDED` per frame, per amendment V13.**
`CubeFlight._frame_from` reads the mission log entry for the stop that produced each frame: when the
stop's pose fix was reliable and plausible (`status == "LOCALISED"`), the frame's `x_cm`/`y_cm`/
`heading_deg` are the measured fix and `position_kind="MEASURED"`; otherwise they are the
dead-reckoned commanded position and the rail's fixed heading (`FACING_DEG`, the drone only ever
translates facing +y), and `position_kind="COMMANDED"`.

**Every frame also carries the stop's tolerance verdict.** `cube/mission.py`'s two tolerance circles
(`TOLERANCE_CM = 15`, `OUTER_TOLERANCE_CM = 30`) already classify each stop as `INSIDE`, `OUTER` or
`OUTSIDE` (`None` when unlocalised) and log it on the mission-log entry; `CubeFlight` copies that
verdict onto every `FlightFrame` captured at that stop as `FlightFrame.tolerance`. This is carried
for the record only — an `OUTER`-circle photo is visibly one the drone did not quite hover where
commanded — and does not change how `merge.py` weighs a sighting; widening tolerance for a degraded
stop remains §5.4's `geometry_degraded` mechanism, unchanged by this addition.

A stop aborted en route (the in-move watchdog of §10.3 of the coherence spec, a containment or
tolerance abort) never reaches the mission runner's `_look` step, so it contributes no frames;
`CubeFlight.fly` returns whatever frames were captured before the abort instead of raising, since the
drone has already been landed by the existing controlled-landing path either way.

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
| **Provider returns a label the class map does not know** | counted as `dropped_labels` and surfaced in `get_results` with the offending strings. If more than half the returned items were dropped, the frame fails as `CLASS_MAP_MISS` rather than reading as an empty scene. A closed map that silently discards `macbook`, `open laptop` or `chromebook` would report a full room as empty. |
| **Box width changes by more than 2× between stops** | a laptop seen edge-on from one stop and open from another shifts its box centre by up to 15 cm of real extent. That sighting's tolerance widens and it is excluded from depth agreement. |
| **Operator approval must show what will happen** | `on_approval` receives the candidate's full rendered description — stops, extent, range, resolvable classes, duration — not just an option id and a sentence. Approving a bare token is not approval. |
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

The cube mission runner **has never flown**. 09-21 is the first time. A rail sweep is the simplest
possible mission: sideways moves, fixed height, no rotation (§5.3).

**The first action on 09-21, before anything else, is a tape measure.** Fly the rail once and record
by hand where the drone actually stopped. Fifteen minutes, and it is the highest-information action
available before the 25th: it converts the position-error assumption from a guess into a number. If
the residual exceeds ~20 cm, §5.2's ground-plane path is not merely preferable, it is the only
viable one, and we will know on day 2 rather than day 4.

**Corrected fallback.** Rev 2 said the cut was "modes `E`/`J` and the follow-up flight". That was
backwards on both counts: `E`/`J` are pure software that runs on free replay and are not on the
critical path, and the follow-up flight is the most compelling thing in the demo (§13.5). The real
fallback is a **replay-path demo**: capture frames by whatever means work — tripod, handheld, a
chair slid to tape marks — run the full loop through `ReplayFlight`, and film the drone separately.
It uses code that already exists, it survives any hardware failure, and it is honest as long as the
video says so.

### 13.2.1 Operational risks not previously recorded

| Risk | Why it matters | Mitigation |
|---|---|---|
| **2.4 GHz saturation** | The Tello is its own access point. A lecture room full of laptops and phones degrades the link; video stutters or the drone enters failsafe mid-mission. This is probably the single likeliest cause of 09-27 failing outright. | Scout the channel before the session; have the replay-path demo ready as the answer. |
| **Battery** | ~13 min rated, 7–8 min usable, less in wind, refuses takeoff below ~10 %. A sweep plus connect and land is ~3 min, so **2–3 sweeps per battery** then ~90 min recharge. | State the battery count on 09-20. Run the merge on sweep 1's frames *before* flying sweep 2. |
| **Mid-sweep landing** | A low-battery auto-land at stop 3 of 4 must not consume one of the two permitted flights. | `sweeps.status = ABORTED` does not count toward `flights_used`; a partial sweep is never merged. |
| **Stream latency** | Tello video is buffered UDP H.264, 150–400 ms end to end. At 50 cm/s that is 15 cm of *systematic* lag toward the previous stop — the worst error shape for any baseline method. | §5.2 removes the dependence on lateral position. `captured_at` and `command_completed_at` make the residual measurable rather than invisible. |
| **Flying near people** | The `SweepEnvelope` is geometric and people-blind. `consent.py` gates *photographing* people; nothing gated *flying at* them. | Prop guards fitted; a minimum horizontal separation recorded in the site file; the operator confirms the flight line is clear before takeoff. This is a safety gate, not a privacy one. |
| **Unmeasured room** | `standoff_cm` is hand-entered and silently drives `range_cm` → `resolvable_classes` → what the report may claim. | The site file records who measured it and when; a sweep whose standoff was never measured is flagged in the report. |

### 13.5 What the demo should actually show

Counting alone is a weak payoff: an informed viewer can count six laptops from one photograph, and
§4.3 already concedes single-frame counting is adequate for people. Three things here a photograph
cannot do, in order of strength:

1. **Uncertainty resolving.** Half-hide one laptop. Sweep 1 reports "at least 5, one uncertain
   behind the monitor". The agent proposes a close look *at that spot*; the operator approves;
   sweep 2 reports 6. This is §9.2's decision-quality metric made visible.
2. **A refusal with its arithmetic.** Ask it to count pens. *"A pen is 2.3 px wide at 3 m — not
   observable at this range."* A system that declines with a derivation is more convincing than one
   that answers, and it needs no flight.
3. **`check_report` rejecting an invented number**, live. Ten seconds, no flight risk, and it is the
   genuinely novel architectural property.

Ground truth belongs on screen — an establishing wide shot or a printed count — so the number is
verifiable rather than merely asserted.

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

> **Rev 3 correction.** Rev 2 promised that without consent "person regions are blurred before the
> frame is written, so no unblurred image reaches disk or the provider". **That guarantee was false
> and could not be made true in that shape**: the implementation nulled two text fields on the
> detection records *after* the frame had already been written and sent, and you cannot blur people
> before the provider tells you where they are. The guarantee is now one the code actually delivers.

Enforced in code, not left to discipline:

| Consent state | Behaviour |
|---|---|
| recorded for this location and date | frames are sent to the provider and retained |
| absent | **no frame leaves the process and none is retained.** The provider is not called; the run ends `CONSENT_REQUIRED` before any capture is written |

There is no partial path, because every partial path I examined leaked an unblurred frame to a
third-party API. The rule is simply: **without recorded consent, no frame is sent anywhere and none
is kept.**

Consequences, accepted deliberately:

- A provider-backed run needs consent. That is the correct constraint, not a limitation.
- Replay runs over already-consented frames are unaffected, so all hermetic and evaluation work
  proceeds without touching this gate.
- The garden shoot needs it for anyone in frame; the lecture room on 09-27 needs it for the room's
  occupants, which must be asked before the session, not during it.

A local person-blur pass before anything leaves the process would restore the middle option. It
needs a local detector and roughly half a day, and it is **not** in scope for the 25th. If the
operator wants it, it becomes its own task with its own evidence.

## 14. Out of scope

Per-stop airborne decisions; free-form waypoints; pen detection at demo range; exact person counts;
gateway changes; alley-path changes; WP3 judged-path changes; any P3b1 file; **any policy acting
without an operator gate** (§7.3); consolidation (§12).
