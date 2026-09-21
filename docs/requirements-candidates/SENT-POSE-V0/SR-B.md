---
id: SR-B
title: "DronePoseReport wire object and WP0 contract amendment"
statement: "The system shall add a schema-validated, closed-object DronePoseReport to the WP0 wire oneOf registry (report_id, run_id, observed_at, source_kind LIVE|SIMULATED|REPLAYED, pose point in [0,100] room units with optional heading_deg/altitude_m, method APRILTAG_36H11_PNP, tag_observations >=1, reprojection_error_px with a fail-closed ceiling), add DRONE_POSE_REPORTED to the WP0 section 3.5 event enum, add drone_pose snapshot fields governed by the WP0 section 3.4 staleness rule, and amend the V0 spec section 9 non-goal from GPS/localisation to GPS/global localisation."
domain: interface
upstream: []
source: "docs/superpowers/specs/2026-09-16-sentinel-wp0-shared-coherence-contracts.md#3.5"
---

## Scope

- Amend: `docs/superpowers/specs/2026-09-16-sentinel-wp0-shared-coherence-contracts.md` (3.3 records, 3.5 event enum), `docs/superpowers/specs/2026-09-16-sentinel-v0-coherence-spec.md` (section 9 non-goals), `docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json` (oneOf registry entry).
- Add `tag-map-v1` contract schema + valid fixture under `docs/superpowers/specs/contracts/`.
- Pose provenance: every report carries `source_kind`; pose is a room-local estimate, never a GPS claim.
- Reprojection error above the fail-closed ceiling invalidates the report rather than degrading silently.

## Non-goals

- No changes to existing wire objects or the closed-object registry semantics.
