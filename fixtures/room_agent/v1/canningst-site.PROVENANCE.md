# `canningst-site.json` provenance

This is the CanningSt room-agent site used by Mission Control. The bound
`docs/wp5-flight1-site.json` and `docs/wp5-flight3-map.json` SHA-256 values
still identify the original cube survey and mapped tags. The larger free
space below comes from the operator's 2026-09-24 measurements in
`docs/handoffs/2026-09-24-room-agent-coverage-codex.md` (the handoff may live
in the `jev-agentic-room-loop` worktree). It supersedes the smaller WP5 cube
for this room-agent site; the hash binding is not a claim that its dimensions
still equal the old cube file.

## Clearance and home

The measured horizontal free space is x 90–410 cm, centred on x 250, and
y 109–400 cm measured from the tag wall at y 400. The commanded `flight`
volume is x 120–380 and y 140–360: at least 30 cm in from the measured
width and rear edge, and 40 cm from the tag wall. `cube` and `home-corridor`
use the measured horizontal extent so the checked landing area and home
route are contained in it. The landing polygons have the same footprint.
The home pad at (250,190,0) and route checkpoint at (250,190,80) are inside
the corridor; the checkpoint is also inside `flight` and `observation`.

The operator measured clear true height from about 70 to 300 cm. The Tello
height reading is 30–40 cm lower: it reads about 40 cm immediately after
takeoff when true height is roughly 70–80 cm. Site altitude uses that Tello
reading. `flight` spans reported z 0–250 cm: its minimum is 0 by the operator's 2026-09-24 decision (integration commit 56d6784), because the Tello height reading is faulty and must not refuse a low but real hover. The `cube` and
`home-corridor` extend to z −20 to include the 20 cm forecast error below
the floor for landing checks; their ceiling is reported z 250. Landing
contingencies span z 0–250, including the floor. The flight bounds no
longer refuse low reported heights; the coverage design still resolves
every viewpoint at reported z 70.

The `observation` semantic envelope is x 190–310, y 160–220, reported
z 0–140 (minimum 0 per 56d6784; the ceiling 140 keeps the middle height
at reported z 70, the height the coverage matrix was generated for). It keeps the eight resolved viewpoints inside `flight` while
placing the camera low enough to see the existing demo surface. Its middle
height resolves to reported z 70. All three left/center/right rear poses
resolve at y 170, front poses at y 210, and the two side looks at y 190.
The front side is +y, toward the wall tags.

Both `NAVIGATION_UNAVAILABLE` and `RETURN_ENERGY_UNAVAILABLE` retain the
`LAND_IN_PLACE` contingency with a current operator clear-floor assurance.
Landing never depends on a tag fix. The assurance lasts 30 minutes.

## Coverage and tags

The existing declared demo surface footprint x 190–310, y 190–270 and
support plane z 50 are retained. `scripts/room_site_coverage.py` partitions
that footprint into six 40 × 40 cm cells. Each cell requires `computer` and
`phone`. The generator uses `TELLO_MEASURED` (960 × 720, fx 892.58,
fy 893.74, 8.6° downward pitch), projects each cell centre from the eight
resolved poses at −30°, 0°, and +30° sweep headings, and limits detection
range to 250 cm. A cell with no such geometric view is omitted and logged.
The committed grid has six cells, each visible from at least one pose, and
zero omitted cells. This is geometric visibility; objects may still be
occluded in a real frame. Re-run with `PYTHONPATH=src;.` and
`python scripts/room_site_coverage.py --check` to verify the committed matrix.

The tag map places tag 2 at x 215.6 and tag 0 at x 284.4, both at y 400,
z about 53 and facing −y. At the six forward-facing poses, the tag wall is
190–230 cm ahead and both tags are within about 3 m and the camera's
horizontal field. Those poses declare both tag IDs, which is the minimum
for expected re-anchor credit. The `center-middle-middle-left` and
`center-middle-middle-right` poses look sideways and declare no tag view.

`FLOOR` at z 0 has no classes; `demo-surface` at z 50 supports `computer`,
`phone`, and `mug`. The surface footprint and supported class list remain
operator declarations from the earlier room site, not a new measurement.

## 2026-09-25: objects on the ground, not a table

There is no table at CanningSt: phones, computers and mugs lie on the brick
ground (operator, 2026-09-25). The `demo-surface` plane at z 50 is removed.
Site heights are Tello readings, which read 30–40 cm below true height, so the
ground is declared as the support plane `ground` at reported z −35 (`FLOOR`
must stay at z 0 and supports no class). `scripts/room_site_coverage.py` now
grids x 130–370, y 150–390 on that plane. From reported z 70 (about 105 cm
true) the 8.6° pitched camera first sees the ground about 1.75 m ahead, so
only the ten cells within about 90 cm of the tag doors (y 310–390) are
visible from any pose. The two side looks (`center-middle-middle-left/right`)
see no ground cell and are no longer declared zones. Objects placed further
from the doors are not seen at this height.
