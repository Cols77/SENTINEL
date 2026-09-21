---
id: sentinel-wp5-cube-positioning-design
title: "WP5 cube navigation and positioning - design"
status: review-pending
---

# WP5 cube navigation and positioning — design (rev 3)

Date: 2026-09-20 · Branch: `feat/sentinel-wp5-drone-pose` · Status: draft for operator review
Supersedes rev 1 (V-panels around the cube) and rev 2 (single-height tag line), and handoff steps
2–4 of `docs/handoffs/2026-09-20-wp5-pose-validation.md`.
Related: FEAT-021, SR-060…SR-063, `docs/wp5-rehearsal-manifest.md`.

## 1. Goal

Show the drone **navigating to several places** inside a bounded operating cube — different
positions, heights and look directions — to observe the room (e.g. count the computers, look
down the two table alleys). One mission runner, **three pose modes**, chosen per run:

| Mode | Tags needed | What pose does | SR-063 |
|---|---|---|---|
| `off` | none | nothing; pure open-loop navigation | untouched |
| `passive` | yes | measured at each stop, logged, can trigger a controlled landing if the drone leaves the cube; computes the correction it *would* make | compliant as written |
| `reanchor` | yes | additionally replaces the open-loop position estimate at a hover and sends ≤ 2 bounded trim moves | compliant since the §5 C1 amendment (consented 2026-09-21) |

Which mode the final demo uses is decided later, from flight data. The mission never *depends*
on pose: a failed fix degrades a stop to open-loop, it does not end the run.

Arrival bar when pose is on: horizontal distance to the commanded point **≤ 20 cm**. Observation
is best effort.

### Operator constraints this design honours (2026-09-20)

Tags stand on **floor stands** on the open floor of the lecture room, outside the cube, and **not
in the alley mouths**. Both table alleys are seen from one side. The cube's place and size follow
from the tags. More of the cube's height should be usable. Stands/panels may be bought.

### Non-goals

Continuous in-motion control from pose; entering the alleys; wire-contract or tag-map-schema
changes; WP1 coordinator wiring; the counting/perception itself.

## 2. Evidence

| # | Finding | Source |
|---|---|---|
| E1 | Single-tag static accuracy: 40 cm → 1.0 cm spread; 80 cm → 2.1 cm; 111 cm → 11.1 cm. | measured |
| E2 | The single-tag error driver is viewing obliquity (planar PnP lateral/yaw degeneracy of one small head-on tag): 14.4° → 1.45 cm, 6.8° → 8.66 cm on the same frames. | measured |
| E3 | Reprojection error does not predict pose accuracy. | measured |
| E4 | Camera pitched 8.6° down; FOV 70.2° × 55.6°; view spans 19.2° above to 36.4° below horizon. **Corrected 2026-09-21:** only the pitch was measured. The FOV was derived from the datasheet's 82.6° and contradicts this repository's own chessboard calibration (fx 892.6), which gives 56.5° × 43.9° and a view from about 13° above to 31° below the horizon (coherence spec §10.5). `sentinel.cube.observe` still uses 55.6° and over-promises what is in frame at close range; owed by the WP5 fix session. | pitch measured; FOV was spec-derived and is wrong |
| E5 | Joint PnP over several tags removes the degeneracy. | simulated |
| E6 | Joint solve on the real two-tag recording is robust to the degenerate tag (1.06 vs 3.77 cm); joint reprojection 1.19 px vs ~0.5 px single — the residual exposes tape-measure map error. | measured |
| E7 | Simulation under-predicts real scatter ~2.5×. All predictions below include this factor. | derived |
| E8 | A flat line of tags, joint-solved, is self-compensating: near the line one head-on tag suffices; far from it several tags give a metre-scale baseline. Pitch between tags must stay ≤ ~0.6 m where coverage is wanted. | simulated |
| E9 | Detector change spiked against the repo: suite stays 220 green; truth recovered to 1.7 cm on a rendered two-tag frame; wrong map falls back to single-tag. | verified |
| E10 | **Stands only in front of the table blocks, alley mouths open (assumed 1.2 m), two tags stacked per stand at 0.90 + 1.45 m:** altitude 1.0–1.8 m all usable; in front of table blocks 1–8 cm from 0.8 m standoff; in front of an alley mouth **blind closer than ~1.6–2.0 m**, 1–6 cm beyond; outer line ends at 3.0 m standoff 9–13 cm. With a single tag height, altitudes above ~1.4 m lose the tags at close range — the narrow band in rev 2 was an artefact of one row. | simulated 2026-09-20 |
| E11 | Tags flat on the floor were simulated and rejected: above 1 m altitude most of the cube is blind (floor only visible ≥ 1.36 × altitude ahead, and grazing views are undecodable). | simulated |

E8/E10 are predictions with assumed room dimensions. **Stage 1 (§7) measures them; the layout
tool (§3.2) re-runs them with the real dimensions.**

## 3. Architecture

```
 site.json ──► [1 site model] ──► tag-map-v1.json · flight envelope · containment footprint
 site.json ──► [2 layout check] ──► predicted error grid, blind cells      (design-time)
 frames ──► [3 joint multi-tag PnP] ──► DronePoseReport (unchanged contract)
               └► [4 stationary localisation burst] ──► fix | UNRELIABLE
                        └► [5 containment check]   (SR-063 AC-2)
 mission.json ──► [6 cube mission runner] ── subclass of FlightTestRunner
                        pose mode: off | passive | reanchor
```

### 3.1 Site model (`cube-site-v1`, one JSON file describes the whole setup)

- The drone **translates only while facing +y** (toward the tables). `Position.moved` already
  defines forward = +y, right = +x, so the existing `FlightEnvelope`/`move_checked` are correct in
  the room frame with no new navigator. Yaw happens only at a stop, and is undone before the
  next move.
- **Stand** = one vertical support at tape position `x_m` on the line `y = line_y_m`, carrying
  1–2 tags at listed heights, facing −y. A stacked pair is a rigid fixture (the 30.1 cm pair
  already tested is one). Survey: **one tape along the line** gives every `x`; a ruler gives
  heights. E6's weak link (relative tag position) reduces to reading one tape.
- Default proposal, to be re-run with real dimensions: 7 stands in front of the three table
  blocks (2 + 3 + 2), tags at 0.90 and 1.45 m → 14 tags. Six are printed; eight more needed.
  Start smaller — Stage 1 needs two stands.
- The **cube is derived**: x spans the line ends inset 0.3 m; y spans standoff 3.0 → 0.8 m from
  the line; z 0–2.0 m. Commanded bounds are inset a further 0.3 m in x/y; altitude band
  **80–180 cm**.
- The site file yields the tag map (`zone_id = STAND_<n>`), the flight envelope and the
  containment footprint, so the three cannot drift apart.

### 3.2 Layout check (`cube-layout-check`)

The Monte Carlo behind E8/E10 as a tool: for a grid of positions and altitudes it prints
predicted p95 error and tags in frame, and marks blind cells. This is how the tests define tag
placement: edit the site file (move a stand, drop a tag row, widen an alley gap), re-run, see the
consequence before touching the room. It prints the reality factor it applies; Stage 1
recalibrates it.

### 3.3 Joint multi-tag PnP (`sentinel.pose.detector`) — spiked (E9)

A tag failing PnP or the 2.0 px gate is dropped, not fatal. One surviving tag → today's result.
Two or more → one PnP over all corners in room metres (`SQPNP` seed → `ITERATIVE` refine), joint
gate 2.0 px, fallback to best single tag with a warning. Report shape unchanged; `joint=False`
keeps the old path for A/B comparison.

### 3.4 Localisation burst (`sentinel.pose.localise`)

Median `(x, y)`, circular-median heading, `n`, spread p95 from one hover window. Unreliable when
`n < 8` or spread p95 > 5 cm. Pure function. "Burst localisation" *is* tag positioning averaged
over a hover; without tags in view there is no fix.

### 3.5 Containment (`sentinel.pose.containment`) — SR-063 AC-2, owed

`INSIDE | MARGIN | OUTSIDE` against the site footprint. May alert and trip the existing abort
path; cannot issue motion.

### 3.6 Mission runner (`sentinel.cube.mission`; CLI `cube-run`)

Subclass of `FlightTestRunner` (preflight, emergency latch, abort, landing inherited) via a small
pure refactor: injectable envelope, overridable `_fly_sequence`.

A waypoint is `{name, x_cm, y_cm, z_cm, looks_deg, localise}`. At each one:

1. **Go:** per axis (x, y, then z) one `move_checked`; an axis delta ≤ 10 cm is skipped, otherwise
   the move is `max(20, delta)` cm. Up/down are validated against the height sensor as today.
2. **Localise** (modes `passive`/`reanchor`, and only if the waypoint says `localise: true` — set
   it false for stops inside an alley-mouth blind zone): settle, burst (≤ 12 s, early stop at 12
   reports, the stream stays open all flight), containment, plausibility gate (fix within 60 cm
   of dead reckoning), log. The trim it implies is always computed and logged.
3. **Correct** (`reanchor` only): tracked position := measured position; if measured heading is
   off by > 10°, one trim rotation; then ≤ 2 rounds of trim moves, each envelope-validated and
   followed by a new burst.
4. **Look:** for each offset in `looks_deg` (0 = toward the tables, + = left) rotate, settle,
   capture a frame; finally rotate back to 0.

The whole mission is validated against the envelope before takeoff. Max 8 waypoints.

### 3.7 Observation candidates (`sentinel.cube.observe`)

For an alley mouth `(x, y, width)`: *close-and-deep* (nearest commanded standoff on the alley
axis) and, if that does not span the mouth, *back-and-wide* (standoff ≥ width / 1.41). If the
axis lies outside the commanded bounds, the nearest point with a look offset toward the mouth.
Each candidate carries its metrics; the agent chooses. Deterministic, envelope-valid.

## 4. Failure handling

| Failure | Behaviour |
|---|---|
| No tag / all tags gated out | frame skipped |
| Joint reprojection > 2.0 px | fall back to best single tag, warn "map residual" |
| Burst unreliable | stop marked `UNLOCALISED`; mission continues open-loop; nothing corrected |
| Fix > 60 cm from dead reckoning | implausible: ignored, operator alert; two in a row → controlled landing |
| Containment `MARGIN` / `OUTSIDE` | alert / controlled landing (existing abort path) |
| A waypoint outside the commanded bounds | refused before takeoff |
| A trim or min-move overshoot that would leave the bounds | skipped, logged |
| Emergency latch | unchanged, terminal |

## 5. Decisions

**C1 — SR-063 amendment for `reanchor` mode. CONSENTED 2026-09-21 (Colin) and applied to `requirements/SR-063.md`.** Stage 3 (§7) is still the first flight of this mode and keeps its G4 consent per flight. Replacement statement:

> Pose data shall never command drone motion while the drone is translating or rotating, and
> shall never originate a target. At a stationary hover, a pose fix that passes the quality gate
> (sample count, spread) and the plausibility gate (agreement with the open-loop estimate) may
> replace the worker's open-loop position estimate ("re-anchoring") and may trigger at most two
> bounded trim moves, and one trim rotation, toward the already-approved waypoint. Every
> resulting command is an ordinary `move`/`rotate` issued by the runner and validated by the
> flight envelope. Pose additionally remains an advisory containment input to the operator and
> the existing abort policy.

Targets still come only from the approved mission; a detector fault can at worst misplace the
start of one leg, and only after passing the quality gate, the plausibility gate, the envelope
and containment. `runner.py` still consumes no pose data (AC-1 holds literally). **Nothing waits
on this:** `off` and `passive` need no amendment; `cube-run --pose reanchor` refuses to start
without `--sr063-amended`, and the requirement file is edited only on the operator's recorded
consent.

**C2 — Altitude.** Commanded band 80–180 cm in the cube mission's own envelope; the default
envelope used by `sentinel run` is untouched. Stacked tags (E10) are what make the top of the band
localisable; in `off` mode the band is usable regardless.

**C3 — Alley-mouth blind zones.** Directly in front of an open mouth there is no fix closer than
~2 m. Pattern: localise at ≥ 2 m, then advance ≤ 1 m open-loop with `localise: false`. At 2 m the
whole mouth is already in frame, so the advance is optional.

**C4 — Double counting is a perception risk, not a navigation one.** Several stops see the same
computers. Fewer, higher stops with look offsets may count better than many stops. The mission
format supports both; the choice belongs with the perception work.

**C5 — Unknown room dimensions.** Alley width, table-block widths and open-floor depth are
assumed (1.2 m alleys). Everything dimensional lives in the site file, and the layout check
re-derives coverage from it.

**C6 — Simulation optimism.** Predictions carry a 2.5× factor from a single run. Stage 1's first
rung stays loose (p95 ≤ 15 cm).

## 6. What changes where

| File | Change |
|---|---|
| `src/sentinel/pose/detector.py` | drop-not-fail gating; joint solve; `joint=` switch |
| `src/sentinel/pose/localise.py`, `containment.py`, `mapcheck.py`, `layout.py` (new) | burst fix; AC-2 check; single-vs-joint on recordings; predicted-error grid |
| `src/sentinel/pose/scoring.py` | export `circular_median_deg` |
| `src/sentinel/cube/site.py` (new) | site file → tag map, envelope, footprint |
| `src/sentinel/cube/mission.py`, `observe.py` (new) | mission runner (3 pose modes); candidates |
| `src/sentinel/runner.py` | injectable envelope; overridable `_fly_sequence` (no behaviour change) |
| `src/sentinel/cli.py` | `cube-site-map`, `cube-layout-check`, `pose-map-check`, `cube-run` |
| `requirements/SR-063.md` | amended statement — only on the operator's recorded consent |
| `docs/safety.md`, `docs/wp5-rehearsal-manifest.md` | cube checklist; Stage 1–3 protocols |

## 7. Test strategy

- **Stage 0 — synthetic (CI).** Every unit; the mission runner against `DryRunClient` in all
  three modes.
- **Stage 1 — static "stand test", motors off, no approvals, fits a small room.** Two stands
  ~0.5 m apart. Drone on a support ~1.1 m back: on-axis and ~0.5 m to one side, at two lens
  heights. `pose-score` ladder p95 ≤ 15 cm / 10° → 10 / 5 → 5 / 3; `pose-map-check` on each
  recording compares single-tag vs joint on the *same frames* and reports the map residual. This
  is the first real test of E5/E8.
- **Stage 2 — supervised flight (G3 + G4), `--pose off` then `--pose passive`.** 3 then 6
  waypoints with height changes and looks. Pass (passive): ≥ 5 of 6 localised stops ≤ 20 cm, all
  containment `INSIDE`. The log shows what re-anchoring would have done.
- **Stage 3 — `--pose reanchor`**, after the amendment. Pass: every localised stop ≤ 20 cm after
  trims.

## 8. Out of scope

WP1 consumption of pose or candidates; schema changes; PnP altitude in the report; perception;
pushing or merging this branch.
