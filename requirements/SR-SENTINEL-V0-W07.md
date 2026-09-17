---
id: SR-SENTINEL-V0-W07
title: AprilTag pose estimation
statement: "**Normative statement (user-directed, 2026-09-17):** in marker mode, the worker estimates drone pose from the station's numbered AprilTag. The estimate (a) verifies the capture's zone, (b) provides the displayed drone pose, and (c) feeds the worker's continuous safety-cube position check (W08). It does not steer the drone or correct its route. Pose estimation quality is bounded and tested against fixtures; a failed or unreadable expected tag in marker mode is a capture failure."
domain: behavioral
status: accepted-pending-registration
feature: FEAT-SENTINEL-V0-BOOTSTRAP
consent:
  decided_by: Colin Aubé
  decided_at: 2026-09-17
  decision: accepted (user-directed new requirement, approved in parent chat)
  session: native-coherence-bootstrap parent chat
---

# SR-SENTINEL-V0-W07 — AprilTag pose estimation

**Normative statement (user-directed, 2026-09-17):** in marker mode, the worker
estimates drone pose from the station's numbered AprilTag. The estimate (a)
verifies the capture's zone, (b) provides the displayed drone pose, and (c) feeds
the worker's continuous safety-cube position check (W08). It does not steer the
drone or correct its route. Pose estimation quality is bounded and tested against
fixtures; a failed or unreadable expected tag in marker mode is a capture
failure.

**Anchors:** indoor-alley design §1 Marker mode (amendment 2026-09-17);
`2026-09-15-sentinel-djitellopy-backend.md` (deferred-localization note superseded
for marker mode by the amendment); WP4 §1, §3.

**Acceptance:** pose estimate produced per station in marker mode; zone
verification uses it; displayed pose uses it; W08 geofence check consumes it;
fixture-proven bounded accuracy; no steering/correction path exists.

**Evidence class:** implementation-pending (worker code) + contract/bootstrap-
verifiable (fixture shape).
