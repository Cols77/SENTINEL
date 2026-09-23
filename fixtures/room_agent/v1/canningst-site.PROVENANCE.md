# `canningst-site.json` provenance

The room-site document for the operator's usual test space, Mission Control
profile **`CanningSt`**. Source of truth (read-only):
`C:/coding/sentinel/.worktrees/lightcode-mission-control-docs/config/mission-control-sites.v1.json`,
entry `CanningSt`:

| Registry field | Value |
|---|---|
| `site_path` | `../docs/wp5-flight1-site.json` |
| `tag_map_path` | `../docs/wp5-flight3-map.json` |
| `pose_mode` | `off` |
| `surface_z_cm` | `50` |
| `start_x_cm`, `start_y_cm` | `250`, `190` |

`docs/wp5-flight1-site.json` (cube-site-v1) supplies: cube x 1.6–3.4 m,
y 1.6–3.0 m, z ≤ 2.0 m; commanded inset 0.3 m, commanded z 60–180 cm; two
tag stands on the line y = 4.0 m (x 2.156 m and 2.844 m, tags at 0.55 m).
Both files in this checkout are content-identical to the Mission Control
worktree's copies (same LF-normalized SHA-256). `docs/wp5-flight3-corners.json`
is not used.

Every value below is either **derived** (from a named source), **contract**
(forced by a room-agent code contract, named), or **`operator_confirm`**
(not derivable from those sources; the operator must confirm it before the
L3 trial). Nothing is invented silently.

## Bindings

| Field | Value | Status | Source |
|---|---|---|---|
| `site_id` | `canningst` | derived | registry `id` |
| `cube_site_sha256` | `0770fca6…83b2` | derived | SHA-256 of `docs/wp5-flight1-site.json`, LF line endings (the committed blob; platform-stable) |
| `tag_map_sha256` | `b78f1262…f80a` | derived | SHA-256 of `docs/wp5-flight3-map.json`, LF line endings. `live_stack.load_bound_tag_map` accepts this hash for a CRLF working copy too |

## Volumes

| Field | Value | Status | Source |
|---|---|---|---|
| free space `cube` footprint | x 160–340, y 160–300 cm | derived | cube `min/max_x_m`, `min/max_y_m` |
| free space `cube` z | −20 to 200 cm | derived + contract | top: cube `max_z_m` 2.0. Bottom: the floor (0) minus the drift profile's `max_height_error_cm` (20, see the history profile). `admit_terminal_contingency` requires one free-space prism to contain the swept landing disk down to touchdown minus that height error |
| free space `flight` | x 160–340, y 160–300, z 60–180 cm | derived | cube footprint (the brief: semantic frame over the cube) and the commanded z band `commanded.min_z_cm`/`max_z_cm`. Until Task B2 fix 1 it was the semantic envelope |
| free space `observation` (semantic envelope) | x 220–280, y 160–300, z 60–140 cm | **operator_confirm** (Task B2 fix 1) | chosen, not derived: inside the cube, centred on the pad's x, so the three regions resolve to x 230/250/270 at y 183 and z 100 (the takeoff height; no vertical legs). With the history drift (0.157 cm/cm) the supervisor refuses the WP5 inset corners x 190/310 (their out-and-back ends 30 cm from the rear face) and admits these. The live runner's flight envelope is this volume (`live_stack.site_envelope`), so a measured home near the start pad (x ≈ 250, y ≈ 190, z 60–140) is inside it |
| forbidden volumes | none | derived | the WP5 cube is the surveyed clear volume; nothing is declared inside it |
| corridor `home-corridor` | x 160–340, y 160–300, z −20 to 200 cm | derived + contract | cube footprint; bottom = pad z minus `max_height_error_cm` (Task A carry-over: takeoff is refused unless the home corridor reaches it) |

## Home and route

| Field | Value | Status | Source |
|---|---|---|---|
| `home.nominal_pose` x, y | 250, 190 cm | derived | registry `start_x_cm`, `start_y_cm` |
| `home.nominal_pose` z | 0 cm | **operator_confirm** | the drone takes off from the floor at the start mark. `live_stack` anchors the pre-takeoff tracker at this height, and the landing contingency's `z_band_cm[0]` must equal it |
| `home.heading_deg` | 90 | derived | canonical heading (+y, facing the tag stands); every WP5 START fix read ≈ 90° (e.g. 89.9°, 2026-09-23 12:21) |
| `home.pad_id` | `canningst-start` | derived | name only |
| `home-route` checkpoint | (250, 190, 80) | derived + **operator_confirm** (z) | ends over the pad (`SafetySupervisor._termination` contract). z 80 cm is the airborne home altitude start admission assesses before takeoff; WP5 START hovers read 60–70 cm. After takeoff the measured home replaces it |

## Terminal contingencies

Both triggers (`NAVIGATION_UNAVAILABLE`, `RETURN_ENERGY_UNAVAILABLE`) are
`LAND_IN_PLACE` over the cube footprint, z band 0–200 cm, pose state
`COASTING` or better.

| Field | Value | Status | Source |
|---|---|---|---|
| landing polygon | cube footprint | derived | cube |
| `z_band_cm` | 0–200 | derived + contract | floor to cube top; `z_band_cm[0]` must equal the home z for a home landing |
| `clearance_source`, `assurance_policy_id` | `operator-checked-clear-cube`, `operator-clear-floor-check` | **operator_confirm** | the operator's pre-flight check that the floor inside the cube is clear (recorded per session as an assurance file) |
| `max_assurance_age_ms` | 1 800 000 (30 min) | **operator_confirm** | an assurance recorded before the run must outlive preview, connectivity and the whole flight |

## Semantic frame, coverage and supports

| Field | Value | Status | Source |
|---|---|---|---|
| axes | left→right +x, rear→front +y | derived | front = max y, the tag stands' side (y 4.0 m) |
| landmarks | none | derived | the tag stands are outside the cube; no other landmark is surveyed |
| coverage cells | left / center / right strips x 190–230 / 230–270 / 270–310, y 190–270, z 0–80 | derived + **operator_confirm** | the commanded (inset 0.3 m) area; z 0–80 = floor to the demo surface plus the 30 cm resting band. Required classes `computer`, `phone` (the L3 request) |
| `pose_visibility` | `left/center/right-rear-middle-toward_targets` → its own strip | **operator_confirm** | a declared visibility matrix, not a calibrated frustum. The left/right regions resolve to x 230/270 (the `observation` envelope), on the inner edges of their strips |
| `pose_tag_visibility` | each of the three poses → tags `0`, `2` | derived + **operator_confirm** (Task V2) | the tag map puts tags 2 and 0 on the stands at x 215.6 / 284.4 cm, y 400 cm, z 53 cm, facing −y. From x 230–270, y 183, z 100 facing +y (the poses' yaw 90°) both are about 217 cm ahead and at most 16° off the camera axis, inside the Tello's ~28° half field of view; every WP5 START fix from the pad saw both. The supervisor credits an expected re-anchor at a pose only when two declared tags are mapped at its yaw; if the fix does not come, the pose is latched RECOVERY_REQUIRED (yaw-scan recovery, then landing) |
| support plane `FLOOR` | z 0, no classes | derived | the floor |
| support plane `demo-surface` | z 50, classes `computer`, `phone`, `pen` | derived + **operator_confirm** (classes) | registry `surface_z_cm` 50. The survey path rays every detection onto this surface (`sentinel.survey.cube_bridge`); which classes rest on it (vs the floor) is the operator's to confirm |

## Clearance provenance

`clearance_provenance` names the registry profile, the cube file and the WP5
flights of 2026-09-21..23 (`artifacts/trials/*-reanchor` in the integration
worktree), all flown in this cube.
