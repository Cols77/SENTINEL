# `lecture-room-site.json` provenance

The lecture-room venue, from the operator's tape measurements on
2026-09-26. It is demo5's five viewpoints, cells and support planes in a
different, larger room; nothing about CanningSt changes, and the CanningSt
scenarios keep the CanningSt tag map.

## What the operator measured

With the origin taken between the two AprilTags:

- two tags, **69.4 cm apart** along the tag line, centres **49.5 cm** above
  the floor;
- **350 cm** of usable depth on y, measured back from the tag plane;
- usable width **x -200 cm to +200 cm** about the tag midpoint.

## The frame

This codebase keeps positive coordinates with the tag line at the far y and
the tags facing -y (`cube-site-v1` emits every tag at `line_y_m` with
`yaw_deg -90`, and `parse_site` refuses a commanded envelope reaching
negative x). The operator's origin therefore sits at **x 250, y 400**:

| | |
|---|---|
| tags | x 215.3 and 284.7, y 400, z 49.5 cm |
| usable floor (`cube`, landing footprint) | x 50-450, y 50-400 |
| commanded `flight` volume | x 80-420, y 80-360 |
| `observation` semantic envelope | x 190-310, y 160-220, reported z 0-140 |
| home pad | (250, 190, 0), heading 90, facing the tags |

`flight` keeps CanningSt's validated insets: 30 cm in from the measured
width and rear edge, 40 cm clear of the tag stands. Home keeps CanningSt's
**210 cm standoff** from the tag line, which is the distance the tag-facing
poses were validated at (live 2026-09-26: 312 fixes), so the room's extra
space lands behind and beside the aircraft rather than between it and the
tags.

Heights are Tello readings, which read 30-40 cm below true, exactly as at
CanningSt. `observation`'s z ceiling of 140 puts `middle` at reported 70 and
`top` at reported 117 (about 150 cm true).

## Why the margins are safe here

Live `l3-20260926-142659` refused every travelling zone because the rear-row
poses left 30 cm to the flight volume's edge and the fixless route-home
forecast poked outside it. demo5's rebuild bought 50 cm at CanningSt. In
this room the same five poses sit at least **100 cm** from every flight
edge, which `test_lecture_room.py` asserts.

## Tags and the tag map

`docs/lecture-room-map.json` is generated from `docs/lecture-room-site.json`
by `Site.tag_map_doc`, so the two agree exactly rather than to the 5 cm
tolerance `check_tag_map_matches_site` allows (CanningSt's hand-measured map
differs from its site file by 2 cm). The camera calibration block is copied
from the CanningSt map unchanged: it describes the aircraft's camera, not
the room.

The tag map belongs to the scenario, not to the installation, so selecting
`lecture` in Mission Control's flight drop-down is what binds these tags;
the CanningSt scenarios still bind `docs/wp5-flight3-map.json`.

## What is reused, and what that assumes

The drift, energy, command-limit and flight profiles are the committed
CanningSt ones: the same aircraft, the same calibration history. The drift
profile's evidence refs still name CanningSt measurements. That is an
operator decision to fly this room on another room's measured drift, not a
new measurement -- the tag-fix rate depends on this room's lighting, and the
first flight here is the evidence for it.

The coverage cells are demo5's, unchanged, so the declared visibility stays
honest. They sit 120-200 cm in front of the pad (the forward looks), 120-160
cm to either side (the wall looks) and 40-80 cm behind it (the backward
look). Objects outside those cells are not counted however visible they
look: from reported z 117 the 8.6-degree pitched camera first meets the
floor about 175 cm ahead.

The larger room is not yet covered by new cells. Adding them is a coverage
generator run, not a hand edit, and it was deliberately left for after the
first flight here.

## `lecture4-site.json`

The same venue with four viewpoints (2026-09-26 evening operator request,
eve of the demo): one board look from the validated pad spot (250, 190),
then the aircraft travels to the left, right and rear walls and faces each.
Only the observation envelope and the declared poses differ from
`lecture-room-site.json`: the envelope widens to x 150-350, y 150-230, so
the semantic thirds place the wall looks at (183, 190), (317, 190) and
(250, 163) -- ~67 cm of sideways travel, ~27 cm back, every endpoint at
least 80 cm from the flight edges (l3-20260926-142659 refused zones at
30 cm). The board look declares the union of demo5's two tag-facing cell
sets; the wall looks keep their demo5 cells, now viewed from 40-27 cm
closer. Tags, tag map, home, routes, support planes, contingencies, drift
and profiles are `lecture`'s, unchanged.
