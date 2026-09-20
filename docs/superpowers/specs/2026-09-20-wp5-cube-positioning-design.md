# WP5 cube positioning — design

Date: 2026-09-20 · Branch: `feat/sentinel-wp5-drone-pose` · Status: draft for operator review
Supersedes: handoff steps 2–4 of `docs/handoffs/2026-09-20-wp5-pose-validation.md` (the six-zone
centre-line Test B/C). Related: FEAT-021, SR-060…SR-063, `docs/wp5-rehearsal-manifest.md`.

## 1. Goal

Show that the drone can be sent to a commanded `(x, y)` inside a bounded operating cube and that we
can **measure** how close it got, well enough to position it at the best available pose for
observing an alley. "Roughly there" is the bar: arrival within **20 cm horizontal distance** of the commanded point (first rung), not
centimetre placement. Alley observation itself is **best effort** and is not a pass/fail criterion.

Operating cube (operator decision, 2026-09-20): **3.0 m (x) × 2.5 m (y) × 2.0 m (z)**, floor-up.
The drone never operates outside it and views the alleys from inside it.

### Non-goals

- Continuous closed-loop flight control from pose (stream gives 1.1–3.4 Hz with gaps up to 6 s).
- Traversing the alleys. The original six-zone centre-line route is retired for this demo: at
  1.6–2.8 m from every tag with 4 of 6 tags under 15° obliquity it predicts 28–105 cm spread.
- Any change to the wire contract, the tag-map schema, or WP1 coordinator wiring.
- Guaranteed detection of anything in an alley.

## 2. Evidence this design rests on

| # | Finding | Source |
|---|---|---|
| E1 | Single-tag static accuracy: 40 cm → 1.0 cm spread; 80 cm → 2.1 cm; 111 cm → 11.1 cm. | measured, manifest |
| E2 | The error driver is **viewing obliquity**, not range: same frames, tag at 14.4° → 1.45 cm, tag at 6.8° → 8.66 cm, `corr(x, heading)` → +0.996 (planar PnP lateral/yaw degeneracy). | measured, truth-free |
| E3 | Reprojection error does not predict pose accuracy (~0.5 px in best and worst runs). | measured |
| E4 | Camera is pitched 8.6° down; FOV 70.2° × 55.6°; view spans 19.2° above to 36.4° below horizon. | measured / spec |
| E5 | **Joint PnP over a splayed tag pair removes the degeneracy** (Monte Carlo, σ = 0.35 px, p95 horizontal error): at 1.46 m a single tag gives 2.5–12.3 cm depending on off-axis angle; the joint solve over a ±25° V-panel gives **1.0–1.1 cm at every angle**; at 1.95 m joint ≤ 2.2 cm; ±45° corner ≤ 1.5 cm. Even a *flat* pair solved jointly is 3–4× better than one tag. | simulated 2026-09-20 |
| E6 | Joint solve on the **real** two-tag recording (25 frames, coplanar vertical pair): joint spread p95 1.06 cm vs 0.79 cm (good tag) / 3.77 cm (degenerate tag). The joint solve is not dragged by the degenerate tag. A vertical coplanar pair cannot show the yaw benefit, so this confirms robustness, not the E5 gain. Joint reprojection was 1.19 px vs ~0.5 px single — **the residual exposes map error** (tape-measured relative tag positions). | measured 2026-09-20 |
| E7 | The simulation under-predicts real single-tag scatter by ~2–3× (4.2 cm simulated vs 11.1 cm measured at 111 cm; real corner noise is quantised, not Gaussian). Apply the same factor to E5: expect **≈ 3–4 cm at 1.46 m** in reality. | derived |

E5 is a prediction; E6 is the only real-data check so far. **Stage 1 (section 7) exists to settle
it before anything flies.**

## 3. Architecture overview

Five units, each independently testable. Only unit 5 touches flight.

```
 panels.json ──► [1 panel-map generator] ──► tag-map-v1.json (unchanged schema)
                                                   │
 frames ──► [2 joint multi-tag PnP] ──► DronePoseReport (unchanged contract)
                                                   │
                        [3 stationary localisation burst] ──► LocalisationFix | UNRELIABLE
                                                   │
                        [4 cube containment check]  ──► advisory alert / existing abort  (SR-063 AC-2)
                                                   │
 waypoints.json ──► [5 cube navigator + mission runner] ──► moves (open-loop, envelope-checked)
                                                   └──► shadow correction (logged, never sent)
```

### 3.1 Site frame and fixture

- Room frame = cube frame. Origin at one floor corner of the cube; `x` along the 3.0 m side, `y`
  along the 2.5 m side; 1 unit = 10 cm, so the cube is `x ∈ [0, 30]`, `y ∈ [0, 25]` — inside the
  contract's `[0, 100]`, no frame-shift hack needed. Heading 0° = +x (existing convention).
- **Panel** = a rigid pair of tags splayed ±a about a vertical fold. Two kinds:
  `convex` (a wedge/tent, ridge toward the cube; used on flat walls or free-standing) and
  `concave` (two walls meeting; a room corner is a free ±45° concave panel).
- **Layout:** six panels — four cube corners + the midpoints of the two 3.0 m sides. Worst-case
  horizontal range from any floor point to the nearest panel is 1.46 m. Tag centres at
  **0.85 m** height (readable from 0.8–1.2 m altitude at ≥ 0.4 m standoff, per E4).
  Twelve tags, IDs 0–11, `size_m` measured per print (printer scales ~96%).
- **Why rigid panels:** E6 shows relative tag placement is the weak link. Inside a panel the
  geometry (splay, tag-centre offset from the fold) is measured once on a table with a ruler
  and protractor; only **one point and one angle per panel** are tape-measured in the room.
  Six room measurements instead of twelve, and the error-sensitive intra-pair geometry is never
  tape-measured at all.
- `panels.json` (new, small, not a contract) → generator → ordinary `tag-map-v1` JSON. The
  detector never learns about panels; `zone_id` carries the panel name (e.g. `PANEL_SW`).

### 3.2 Joint multi-tag PnP (`sentinel.pose.detector`)

Today `estimate_pose` keeps the single lowest-reprojection tag and discards the rest
(`detector.py:178`), and `_solve_mapped_tags` fails the **whole frame** if any mapped tag fails
its gate. Both are wrong for a multi-tag fixture.

New behaviour:

1. Per-tag solve as today, but a tag that fails PnP or the 2.0 px gate is **dropped**, not fatal.
   Zero surviving tags → fail closed as today.
2. One surviving tag → today's single-tag result (unchanged numbers, unchanged tests).
3. Two or more → express every surviving tag's four corners in room metres (same
   `R_room_tag`/`tag_pos` composition `_report_point` uses), solve one PnP over all of them
   (`SOLVEPNP_SQPNP` seed → `SOLVEPNP_ITERATIVE` refine), camera position `= −Rᵀt`, heading from
   the camera +z axis. Gate the **joint** reprojection error at 2.0 px; if it fails, fall back to
   the best single tag and log a warning (a map-error symptom, E6).
4. `DronePoseReport` is unchanged in shape: `tag_observations` lists every tag used,
   `reprojection_error_px` is the joint error, `method` stays `APRILTAG_36H11_PNP`.

`estimate_all` / `flag_tag_disagreement` stay as the per-tag diagnostic path.

### 3.3 Stationary localisation burst (`sentinel.pose.localise`, new)

Pose is trusted only at rest. `localise(reports) -> LocalisationFix | LocalisationUnreliable`
consumes the reports from one hover window and returns the component-wise median `(x, y)`, the
circular median heading (same helper as `scoring.py`), `n`, and spread p95.
Unreliable when `n < 8`, spread p95 > 5 cm, or the window exceeds 12 s. Pure function, no I/O;
stream latency and gaps cost time, never accuracy.

### 3.4 Cube containment check (`sentinel.pose.containment`, new — owed by SR-063 AC-2)

`check_containment(fix, bounds) -> INSIDE | MARGIN | OUTSIDE` against the cube with a configurable
margin (default 30 cm). Pure function. `MARGIN`/`OUTSIDE` surface an operator alert and may trip
the **existing** abort path; the function cannot issue motion. `tests/pose/test_containment.py`
is the marker SR-063 AC-2 already references and which does not exist yet.

### 3.5 Cube navigator and mission runner (`sentinel.cube`, new; CLI `cube-run`)

- `FlightEnvelope` is already a dataclass with overridable bounds; `FlightTestRunner` hardcodes
  `FlightEnvelope()` (1 m cube at z 150–250 cm). The cube mission constructs its own envelope:
  **commanded** bounds are the cube inset by 40 cm in x/y (drift margin + tag standoff) and
  `z ∈ [80, 150] cm`. Open-loop drift must not carry the drone out of the physical cube.
- Moves stay inside the safety checklist: `move` (20–500 cm, axis-aligned) and `rotate` only —
  no `go`, `curve`, or RC.
- **Cardinal-yaw rule.** `Position.moved` assumes body axes = room axes. The navigator tracks
  commanded yaw and permits translation only at yaw ∈ {0, 90, 180, 270}°, mapping body
  directions to room axes through a fixed table. Arbitrary yaw (facing a panel, facing an alley)
  is allowed only while stationary and must return to a cardinal yaw before the next move.
- **Waypoint cycle:** translate (cardinal yaw) → rotate to nearest panel → hover + localisation
  burst → containment check → record arrival error vs commanded → rotate to observation heading
  → capture → rotate back to cardinal.
- **Shadow correction.** After each fix the runner computes the move sequence that *would*
  correct the arrival error (axis error > 10 cm → one ≥ 20 cm move; residual < 10 cm) and
  **logs it without sending it**. SR-063 holds verbatim, and the log shows exactly what a
  correction stage would have done and whether it would have helped.
- Mission log is JSONL beside `run.jsonl` (not wire events → no WP0 amendment). Pose reports keep
  flowing to SQLite as `DRONE_POSE_REPORTED`.

### 3.6 Observation-pose candidates (`sentinel.cube.observe`, last phase)

Deterministic generator: given an alley mouth (centre, axis, width) it returns 2–3 feasible
in-envelope candidates — *close-and-deep* (on-axis at the nearest commanded-envelope face, lowest
altitude), *back-and-wide* (standoff `width / 1.41` if it fits), and *oblique* if on-axis is
infeasible — each with computed metrics (near blind distance `h / tan 36.4°`, fraction of mouth
width in frame, range to nearest panel). The agent **chooses**; geometry stays out of the LLM and
every candidate is already envelope-valid. Consuming this from the WP1 coordinator is out of scope.

## 4. Data flow and failure handling

| Failure | Behaviour |
|---|---|
| No tag / all tags gated out | frame skipped (as today) |
| Joint reprojection > 2.0 px | fall back to best single tag, warn "map residual" |
| Burst unreliable (n, spread, timeout) | waypoint marked `UNLOCALISED`; mission continues open-loop; no shadow correction computed |
| Containment `MARGIN` | operator alert in console + log |
| Containment `OUTSIDE` | operator alert; existing abort policy (controlled landing) |
| Envelope violation on a planned move | mission refuses **before takeoff** (whole plan validated up front) |
| Emergency latch | unchanged, terminal |

## 5. Critical issues and decisions

**C1 — SR-063 forbids the "hover-and-correct" loop. (Decision needed.)**
SR-063: pose "shall never directly command or modify drone motion in V0". A pose-derived
correction move is exactly that. Options:

| | Option | SR-063 | Risk | Demo value |
|---|---|---|---|---|
| a | Open-loop + measured arrival + **shadow correction** | compliant as written | none added | "reached within N cm, and here is the correction it would make" |
| b | **Operator-approved** correction (pose proposes, operator keys approval, navigator re-validates) | needs amendment | detector bug → bad *proposal*; human + envelope still gate it | closes the loop visibly |
| c | Autonomous bounded correction | needs amendment | detector bug → wrong motion (bounded by envelope) | most impressive, weakest safety story |

**Recommendation: build (a) now; it is fully specified above and needs no approval beyond G4.
Treat (b) as Stage 3 behind an explicit SR-063 amendment, decided after Stage 2 data shows whether
open-loop arrival is already inside ±20 cm.** The shadow log makes that decision evidence-based.
(c) is not recommended for V0.

**C2 — Altitude band conflicts with the existing envelope.** Existing: z 150–250 cm. Best for
alley viewing and for the 0.85 m panels: 80–120 cm, which is also roughly Tello's natural
post-takeoff hover (fewer climb commands). Proposed commanded band 80–150 cm. The
`HEIGHT_ENVELOPE` preflight check and `docs/safety.md` wording ("six-zone alley route") need the
cube variant. Trade-off: lower flight = better observation and pose, less margin over floor
clutter. **Recommendation: 80–150 cm, takeoff pad clear of obstacles.**

**C3 — Yaw drift corrupts open-loop translation.** Every `rotate` adds a few degrees of error;
5° over a 1.5 m leg is 13 cm — comparable to the whole tolerance. The waypoint cycle rotates 3×
per waypoint. Mitigations inside SR-063: order waypoints to minimise rotation; keep legs ≤ 1.5 m;
log measured heading at every fix so yaw drift is *quantified* in Stage 2. It can only be
*corrected* under C1(b). This is the most likely reason Stage 2 misses ±20 cm, and the strongest
argument for eventually adopting C1(b).

**C4 — Map error now shows up as joint residual (E6).** Rigid panels are the mitigation.
Additionally the Stage 1 tooling reports joint reprojection per panel — a truth-free map QA
number. A panel whose joint residual is persistently > 1 px has been mis-measured; fix the map,
not the detector.

**C5 — Simulation optimism (E7).** All E5 numbers carry an unverified 2–3× reality factor.
Stage 1 rung 1 is deliberately loose (p95 ≤ 15 cm) so the ladder, not the simulation, sets
expectations.

**C6 — Are the cube faces real walls?** Unknown. Real walls → corners are free ±45° concave
panels and mid-wall panels are 30 cm foam-board wedges. Virtual cube in a larger room → all six
are free-standing wedges on stands at 0.85 m, and the space *behind* panels must be clear of
the takeoff drift margin. The design handles both; the fixture build differs.

**C7 — Battery budget.** ~26 s per waypoint (move 5, rotations 6, burst ≤ 12, observe 3) →
6 waypoints ≈ 3 min of a ~10 min battery. Acceptable; cap missions at 8 waypoints.

## 6. What changes where

| File | Change |
|---|---|
| `src/sentinel/pose/detector.py` | drop-not-fail per-tag gating; joint solve; single-tag path unchanged |
| `src/sentinel/pose/panels.py` (new) | `panels.json` → tag-map tags; convex/concave geometry |
| `src/sentinel/pose/localise.py` (new) | stationary burst aggregation |
| `src/sentinel/pose/containment.py` (new) | SR-063 AC-2 advisory check |
| `src/sentinel/pose/scoring.py` | expose the circular-median helper for reuse |
| `src/sentinel/cube/navigator.py` (new) | cardinal-yaw dead reckoning, plan validation, shadow correction |
| `src/sentinel/cube/mission.py` (new) | waypoint cycle over `DroneClient`, JSONL log |
| `src/sentinel/cube/observe.py` (new) | observation candidates |
| `src/sentinel/cli.py` | `pose-panel-map`, `pose-map-check`, `cube-run` (`--dry-run` / `--real --confirm`) |
| `docs/safety.md`, `docs/wp5-rehearsal-manifest.md` | cube variant of the checklist and protocol |

## 7. Test strategy

- **Stage 0 — synthetic (CI).** Rendered two-tag V-panel frames: joint solve recovers truth;
  an off-axis view where the single-tag path is measurably worse; drop-not-fail; joint-gate
  fallback. Pure-function tests for panels, localise, containment, navigator, shadow correction,
  observe. Mission runner against the existing fake/dry-run client.
- **Stage 1 — static, motors off (no approvals; `pose-stream` never takes off).** Build one
  panel first. Drone on a stand at 6–8 taped points spanning 0.5–2.0 m and 0–30° off-axis;
  score with `pose-score`. Ladder: p95 ≤ 15 cm / 10° → 10 / 5 → 5 / 3. Compare joint vs
  single-tag on the *same recorded frames* via replay (this is the real-world test of E5).
  Then build all six and run `pose-map-check`.
- **Stage 2 — supervised flight, open-loop (needs G3 owner approval + G4 consent).**
  `cube-run --real --confirm` over 3, then 6 waypoints. Arrival truth: a tape to a hovering
  drone is impractical, so truth = the localisation fix itself, validated by Stage 1's accuracy
  at that range. Report per-waypoint arrival error, heading drift, shadow corrections.
  Pass: ≥ 5 of 6 arrivals with horizontal distance to the commanded point ≤ 20 cm, all containment `INSIDE`.
- **Stage 3 — operator-approved correction.** Only after an SR-063 amendment (C1b). Not planned
  in detail here.

## 8. Out of scope

WP1 coordinator consumption of pose or observation candidates; wire-contract or tag-map-schema
changes; PnP-derived altitude in the report (Tello height stays the z source); perception in the
alleys; pushing or merging this branch (operator instruction: feature branch only).
