# WP5 cube positioning — design (rev 2: tag line)

Date: 2026-09-20 · Branch: `feat/sentinel-wp5-drone-pose` · Status: draft for operator review
Supersedes: rev 1 of this file (six V-panels around the cube, yaw-multiplexed localisation) and
handoff steps 2–4 of `docs/handoffs/2026-09-20-wp5-pose-validation.md`.
Related: FEAT-021, SR-060…SR-063, `docs/wp5-rehearsal-manifest.md`.

## 1. Goal

Send the drone to a commanded `(x, y)` inside a bounded operating cube, **measure** where it
actually is, and use that to keep it on plan well enough to reach the best available pose for
observing an alley. The bar is "roughly there": horizontal distance to the commanded point
**≤ 20 cm**. Alley observation is **best effort** and is not a pass/fail criterion.

### What changed from rev 1, and why

Operator input (2026-09-20): the cube faces are not walls; the tags sit **outside** the cube; the
cube's position and size may be chosen to suit the tags; SR-063 may be amended; a simpler setup
beats a clever one. The room is a lecture room whose **two table alleys run parallel**, so both
are seen from the same side.

That makes one idea do all the work: **the drone always faces the alleys, and a single straight
line of tags stands at the alley end of the cube, in the same view.** Consequences:

| Rev 1 | Rev 2 |
|---|---|
| 6 two-tag V-panels around the cube (12 tags, 6 stands, folded fixtures) | 1 flat line of 7 tags, same height, same yaw |
| 6 positions + 6 angles to survey | tag positions read off **one tape measure** laid along the line |
| rotate to a panel, localise, rotate back (3 rotations per waypoint) | **no rotations at all**; tags and alleys are in the same frame |
| yaw drift corrupts every leg (rev 1 issue C3) | yaw never commanded; drift is measured continuously |
| cardinal-yaw navigator, body→room axis tables | body axes = room axes for the whole flight; the existing `FlightEnvelope` works as is |
| pose only at waypoints | pose available whenever the drone hovers, anywhere in the cube |
| cube fixed at 3.0 × 2.5 m, tags fitted to it | **cube derived from the tag line**: the region where predicted error is acceptable |

### Non-goals

- Continuous in-motion control from pose (the stream gives 1.1–3.4 Hz with gaps up to 6 s).
- Entering or traversing the alleys.
- Any change to the wire contract or the tag-map schema; WP1 coordinator wiring.
- Guaranteed detection of anything in an alley.

## 2. Evidence

| # | Finding | Source |
|---|---|---|
| E1 | Single-tag static accuracy: 40 cm → 1.0 cm spread; 80 cm → 2.1 cm; 111 cm → 11.1 cm. | measured |
| E2 | The error driver is viewing **obliquity**, not range: same frames, tag at 14.4° → 1.45 cm, tag at 6.8° → 8.66 cm (planar PnP lateral/yaw degeneracy of one small head-on tag). | measured, truth-free |
| E3 | Reprojection error does not predict pose accuracy. | measured |
| E4 | Camera pitched 8.6° down; FOV 70.2° × 55.6°; view spans 19.2° above to 36.4° below horizon. | measured / spec |
| E5 | Joint PnP over several tags removes the degeneracy (Monte Carlo). | simulated |
| E6 | Joint solve on the real two-tag recording: robust to the degenerate tag (1.06 cm vs 3.77 cm); joint reprojection 1.19 px vs ~0.5 px single — **the residual exposes tape-measure map error**. | measured |
| E7 | Simulation under-predicts real scatter by ~2.5× (4.2 cm simulated vs 11.1 cm measured at 111 cm). All predictions below already include this factor. | derived |
| E8 | **A flat line of tags, joint-solved, is self-compensating.** Close to the line one head-on tag is in frame, but at that range the degeneracy is harmless; far from the line 3–6 tags are in frame and their metre-scale baseline makes the solve strong. Predicted real p95 horizontal error, 7 tags at 0.5–0.6 m pitch, tag centres 0.90 m, altitude 1.0 m, over standoff 0.8–3.0 m and the full line width: **typically 1–4 cm, worst cell ≈ 10 cm** (a single tag in frame at ~1 m). 4 tags at 1.0 m pitch leave 14–35 cm holes — pitch must stay ≤ 0.6 m. Closer than 0.8 m the 0.6 m pitch has blind cells. | simulated 2026-09-20 |
| E9 | The detector change was spiked against the repo: full suite stays 220 green; truth recovered to 1.7 cm on a rendered two-tag frame; a deliberately wrong map falls back to single-tag. | verified 2026-09-20 |

E8 is a prediction. **Stage 1 (§7) measures it before anything flies.**

## 3. Architecture

```
 line spec ──► [1 tag-line map generator] ──► tag-map-v1.json (unchanged schema)
 tag map + cube ──► [2 layout check] ──► predicted error grid, coverage holes  (design-time tool)
 frames ──► [3 joint multi-tag PnP] ──► DronePoseReport (unchanged contract)
                 └► [4 stationary localisation burst] ──► fix | UNRELIABLE
                          └► [5 containment check]  (SR-063 AC-2)
 mission.json ──► [6 cube mission runner] ── subclass of FlightTestRunner, moves only
                          └► re-anchor on a good fix (needs the SR-063 amendment, §5 C1)
```

### 3.1 Site frame and the tag line

- The drone faces **+y** for the whole flight. This is deliberate: `Position.moved` already
  defines forward = +y, right = +x, so with no rotation the existing `FlightEnvelope` and
  `move_checked` are correct in the room frame without any new navigator.
- Tag line: straight, at `y = 4.0 m`, tags facing −y (`yaw_deg = -90`), all centres at
  **0.90 m**, first tag at `x = 0.5 m`. Default **7 tags at 0.6 m pitch** (x = 0.5 … 4.1 m). Six
  are already printed; one more is needed. Mounting: purchased display panels, stands, or the
  front edges of the table blocks — anything rigid. Tags that fall in an alley mouth go on a thin
  stand: a 19 cm square at 0.9 m hides almost none of the alley floor from a 1 m altitude.
- Survey method: lay one tape along the line; each tag's `x` is a tape reading. Height with a
  ruler. That is the whole map. E6's weak link (relative tag position) is reduced to reading one
  tape.
- **The cube is derived, not imposed:** `x ∈ [0.8, 3.8] m` (line ends inset 0.3 m → 3.0 m wide),
  `y ∈ [1.0, 3.2] m` (standoff 3.0 → 0.8 m → 2.2 m deep), `z ∈ [0, 2.0] m`. Commanded bounds are
  inset 0.3 m in x/y for open-loop drift; altitude band 80–130 cm, no vertical moves after entry.
- A generator turns `{first_x_m, pitch_m, count, y_m, z_m, size_m, first_tag_id}` (or an explicit
  list of tape readings) into a `tag-map-v1` file; `zone_id` = `TAG_LINE`.

### 3.2 Layout check (design-time tool)

The Monte Carlo behind E8 becomes `pose-layout-check --tag-map M`: for a grid over the cube it
prints predicted p95 error and tags-in-frame per cell, and flags blind or weak cells. This is
how "the tests define where the tags go": change the pitch, skip an alley mouth, shorten the
line — and see the consequence before touching the room. It also states the reality factor it
applies, and Stage 1 recalibrates that factor from measurement.

### 3.3 Joint multi-tag PnP (`sentinel.pose.detector`) — unchanged from rev 1, spiked (E9)

A tag failing PnP or the 2.0 px gate is dropped, not fatal. One surviving tag → today's
single-tag result. Two or more → one PnP over all corners in room metres (`SQPNP` seed →
`ITERATIVE` refine), joint gate 2.0 px, fall back to the best single tag with a warning when the
joint gate fails. `DronePoseReport` shape unchanged; `joint=False` keeps the old path for A/B
comparison.

### 3.4 Stationary localisation burst (`sentinel.pose.localise`) — unchanged from rev 1

Median `(x, y)`, circular-median heading, `n`, spread p95. Unreliable when `n < 8` or spread
p95 > 5 cm. Pure function.

### 3.5 Containment check (`sentinel.pose.containment`) — SR-063 AC-2, owed

`INSIDE | MARGIN | OUTSIDE` against the cube footprint. Pure function; may alert and trip the
existing abort path, cannot issue motion.

### 3.6 Cube mission runner (`sentinel.cube.mission`; CLI `cube-run`)

Subclass of `FlightTestRunner` (preflight, emergency latch, abort and landing inherited). Needs a
small pure refactor of the runner: injectable envelope, overridable `_fly_sequence`.

Waypoint cycle — no rotation anywhere:

1. `move_checked` legs (x then y), each 20–150 cm, validated by the cube envelope.
2. Settle 1.5 s, localisation burst (≤ 12 s, stops early at 12 reports; the stream stays open for
   the whole flight).
3. Containment: `OUTSIDE` → controlled landing via the existing failure path; `MARGIN` → alert.
4. Log commanded point, fix, arrival error, heading error, spread, tags used.
5. **Re-anchor (only with `--reanchor`, i.e. after the SR-063 amendment):** if the fix passes the
   plausibility gate, the envelope's tracked position becomes the measured position, so the next
   leg is planned from where the drone *is*. If arrival error > 15 cm, at most two trim moves
   (per axis: error > 10 cm → one move of `max(20, error)` cm), each envelope-validated, each
   followed by a new burst. If measured heading is off by > 10°, one trim rotation.
   Without `--reanchor` the same numbers are computed and logged as a shadow, nothing is sent.
6. Capture observation frames (the drone already faces the alleys).

Whole-mission validation happens before takeoff. Max 8 waypoints.

### 3.7 Observation candidates (`sentinel.cube.observe`)

Heading is fixed, so a candidate is just `(x, y)`: `x` = the alley axis clamped into the commanded
bounds; `y` trades depth against width — *close-and-deep* (nearest commanded standoff) and
*back-and-wide* (standoff ≥ mouth width / 1.41, if it fits). Each carries computed metrics
(fraction of mouth width in frame, near blind distance `h / tan 36.4°`, predicted pose error from
§3.2). The agent chooses; the geometry stays deterministic.

## 4. Failure handling

| Failure | Behaviour |
|---|---|
| No tag / all tags gated out | frame skipped |
| Joint reprojection > 2.0 px | fall back to best single tag, warn "map residual" |
| Burst unreliable | waypoint `UNLOCALISED`; mission continues open-loop from the dead-reckoned position; no re-anchor |
| Fix disagrees with dead reckoning by > 60 cm | **implausible**: no re-anchor, operator alert; two in a row → controlled landing |
| Containment `MARGIN` / `OUTSIDE` | alert / controlled landing (existing abort path) |
| Planned move leaves the commanded bounds | refused before takeoff; a trim that would leave them is skipped |
| Emergency latch | unchanged, terminal |

## 5. Decisions

**C1 — Amend SR-063 to permit re-anchoring. Recommended: yes.**
The operator's proposal — hover, take a good fix, continue the plan from it — is feasible and is
better than rev 1's open-loop-plus-shadow: errors stop accumulating across legs, and yaw drift
becomes a measured, trimmable quantity. With the tag line there is not even a "find the tag"
step: tags are in view whenever the drone hovers. Proposed replacement statement:

> Pose data shall never command drone motion while the drone is translating or rotating, and
> shall never originate a target. At a stationary hover, a pose fix that passes the quality gate
> (sample count, spread) and the plausibility gate (agreement with the open-loop estimate) may
> replace the worker's open-loop position estimate ("re-anchoring") and may trigger at most two
> bounded trim moves toward the already-approved waypoint. Every resulting command is an ordinary
> `move`/`rotate` issued by the runner and validated by the flight envelope from the measured
> position. Pose additionally remains an advisory containment input to the operator and the
> existing abort policy.

Safety argument: targets still come only from the approved mission; a detector fault can at worst
misplace the *start* of one ≤ 1.5 m leg, inside a room substantially larger than the cube, and
only if it also survives the quality gate, the 60 cm plausibility gate, the envelope, and the
containment check. SR-063 AC-1 ("runner.py consumes no pose data") stays literally true: the pose
consumer is the mission subclass, not `runner.py`. Until the amendment is recorded, `--reanchor`
stays off and the system runs in shadow mode — so nothing else waits on this decision.

**C2 — Altitude band.** Operator has approved changing the enforced band. The cube mission uses
its own envelope (80–130 cm); the default `FlightEnvelope()` used by `sentinel run` is left alone.

**C3 — Tags in the alley mouths?** A uniform 0.6 m pitch puts some tags in front of the alleys.
Preferred: keep the pitch and use thin stands. Alternative: leave each mouth open and tighten the
pitch on the table-block fronts; `pose-layout-check` shows whether the resulting gap is tolerable
(a 1.0 m gap was not, in E8). Needs the real table/alley dimensions.

**C4 — Minimum standoff 0.8 m.** Closer, the 0.6 m pitch has blind cells. Moving to 0.5 m pitch
(8 tags over the same line) allows 0.6 m. Not recommended: 0.8 m is also a sane safety distance
from the fixtures.

**C5 — Simulation optimism.** E8 already carries the 2.5× factor from E7, but that factor comes
from one run. Stage 1's first rung stays loose (p95 ≤ 15 cm) for that reason.

**C6 — One tag height only.** All tags coplanar and at one height keeps the survey trivial. If
Stage 1 shows weak cells, the cheapest fix is a second row of 2–3 tags at 1.3 m, not V-panels.

## 6. What changes where

| File | Change |
|---|---|
| `src/sentinel/pose/detector.py` | drop-not-fail gating; joint solve; `joint=` switch |
| `src/sentinel/pose/tagline.py` (new) | line spec → tag-map tags |
| `src/sentinel/pose/layout.py` (new) | predicted-error grid (Monte Carlo) |
| `src/sentinel/pose/localise.py`, `containment.py`, `mapcheck.py` (new) | burst fix; AC-2 check; single-vs-joint + residual on recorded frames |
| `src/sentinel/pose/scoring.py` | export `circular_median_deg` |
| `src/sentinel/runner.py` | injectable envelope; overridable `_fly_sequence` (no behaviour change) |
| `src/sentinel/cube/mission.py`, `observe.py` (new) | mission runner with shadow / re-anchor; candidates |
| `src/sentinel/cli.py` | `pose-tagline-map`, `pose-layout-check`, `pose-map-check`, `cube-run` |
| `requirements/SR-063.md` | amended statement — **only on the operator's recorded consent** |
| `docs/safety.md`, `docs/wp5-rehearsal-manifest.md` | cube variant; Stage 1/2 protocols |

Dropped from rev 1: `panels.py`, `cube/navigator.py`, all rotation logic.

## 7. Test strategy

- **Stage 0 — synthetic (CI).** Joint solve on rendered multi-tag frames; drop-not-fail; fallback;
  pure-function tests; mission runner against `DryRunClient` in shadow and re-anchor modes.
- **Stage 1 — static, motors off (no approvals).** Mount the line (start with the 6 printed tags).
  Drone on a stand at ~9 taped points: 3 standoffs (0.8 / 1.8 / 3.0 m) × 3 lateral positions, lens
  at 1.0 m, facing the line. `pose-score` ladder p95 ≤ 15 cm / 10° → 10 / 5 → 5 / 3;
  `pose-map-check` on each recording for joint-vs-single spread and the map residual; compare
  with `pose-layout-check`'s prediction and recalibrate its reality factor.
- **Stage 2 — supervised flight, shadow mode (G3 + G4).** 3 then 6 waypoints. Pass: ≥ 5 of 6
  arrivals ≤ 20 cm, all containment `INSIDE`. The shadow log shows what re-anchoring would do.
- **Stage 3 — supervised flight with `--reanchor`** (after the SR-063 amendment). Same mission;
  pass: all 6 arrivals ≤ 20 cm after trims.

## 8. Out of scope

WP1 consumption of pose or candidates; wire/tag-map schema changes; PnP altitude in the report;
alley perception; pushing or merging this branch.
