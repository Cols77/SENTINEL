# `canningst-wide-site.json` provenance

The CanningSt **wide** scenario: the same room, tag map, measured free space
and measured drift rates as `canningst-site.json`, arranged so a count
mission tours several zones instead of one sweep beside the pad. Generated
by `scripts/room_site_wide.py` (run with `PYTHONPATH=src;.`; `--check`
verifies the committed files). Select it for a Live flight with
`python scripts/room_live_prepare.py … --scenario wide`.

## What changes from the compact site

| | compact `canningst-site.json` | wide `canningst-wide-site.json` |
|---|---|---|
| observation envelope | x 190–310, y 160–220 | x 140–360, y 170–330 |
| viewpoints | 8 in an 80 × 40 cm box (6 tag-facing, 2 sideways) | 13: 9 tag-facing (3 × 3 at x 177/250/323, y 197/250/303) plus 4 sideways (centre looks at each side wall; each side column looks across the room) |
| home pad / route | (250, 190) | (250, 220): where the operator places the drone, 180 cm from the tag wall |
| demo surface cells | x 190–310, y 190–270: 6 cells | x 90–410 (wall to wall), y 240–360: 24 cells |
| detection range for coverage | 250 cm | 120 cm (a phone is ~100 px wide at that range) |
| drift profile | `canningst-drift.json` | `canningst-wide-drift.json` |

Unchanged: the measured `cube`, the `flight` volume (x 120–380, y 140–360,
z 0–250), the home corridor, the land-in-place contingencies, the support
plane at z 50, the tag map binding and the cube-site hash.

## Why these choices

**Sideways looks.** A sideways zone turns 90° out, sweeps ±30° and turns
back: about 300° of yaw with the return. The compact profile allows 360°
between tag fixes, which refused them. The wide profile allows 720°: at 0.02°
of heading drift per degree turned, that adds at most about 7° of heading
uncertainty. The tags are out of view while the drone faces a side wall, so
it coasts until it turns back. There is no "look behind" in the pose
grammar. The side columns' forward sweeps already see the wall strips, so
Jev picks a sideways look for its view, not for new coverage.

**Tags.** Only the centre column sees both wall tags head-on (the minimum for
an expected re-anchor credit), so only those three poses declare tags. The
left column sees tag 2, the right column tag 0, and the front corners
neither. At the side poses a ±15° search yaw brings the second tag into view.

**Home at y 220.** Every return is also checked assuming no tag fix arrives.
From a side zone, that fixless return's uncertainty disk (about 80 cm) must
stay inside the measured room, whose rear edge is y 109. From a pad at y 190
it did not; from 220, where the drone is actually placed, it does.

**Pose rows at y 197–303.** They keep the rear poses' fixless disks off the
rear wall.

**Drift limits.** `canningst-wide-drift.json` keeps every measured drift rate
(0.157 cm per cm flown, heading drift, hover drift). It only raises how far
the aircraft may get from a tag fix: position uncertainty 30 to 90 cm,
heading uncertainty 15 to 30°, and total yaw between fixes 360 to 720°. This is the operator's 2026-09-24 decision to
use the drone more permissively. Walls and landings are still enforced by the
free-space and landing checks, which use the full uncertainty disk. The
longest hop in the grid (73 cm) sits in the calibrated flight envelope's
well-behaved range (90 cm moves drifted about 10 cm; 150 cm moves drifted
about 56 cm).

## Behaviour in the fake-flight test

`tests/room/test_canningst_wide.py` flies the real production stack against a
fake Tello from (250, 220):

- **Wide limits:** a greedy stand-in for Jev (always the top-ranked zone)
  tours the centre and both side columns, 146 cm side to side. Its order can
  leave the last wall-corner zone refused, because it was asked for from
  across the room. It answers (complete or explicitly incomplete) and lands
  on the pad.
- **Sideways-first stand-in:** the centre zone turns 90° to each side wall,
  and the side column looks across the room. It still lands on the pad.
- **Compact limits:** the far zones are refused one by one, each with a reason
  Jev reads. The drone keeps the zones it can reach, answers, and lands.

The fake Tello's tags are perfect, so real flights will see single-tag and
no-tag stretches at the side zones.
