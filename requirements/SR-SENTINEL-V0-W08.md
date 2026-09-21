---
id: SR-SENTINEL-V0-W08
title: Geofence hard stop
statement: "**Normative statement (user-directed, 2026-09-17):** the worker continuously verifies its AprilTag-derived position against the configured room safety envelope (the 1 m demo cube of W03). On envelope exit the worker autonomously issues the terminal emergency hard stop without waiting for coordinator or operator action; the condition is ingested as `EMERGENCY_LATCHED` when the coordinator is reachable, and the W05 latch semantics (terminal, non-overridable, no post-latch commands) apply unchanged. This is a fail-safe reflex only: it can stop the drone and nothing else. The UI emergency stop (U03) and the pre-transmission envelope check (W03) remain as before."
domain: behavioral
status: accepted-pending-registration
feature: FEAT-SENTINEL-V0-BOOTSTRAP
consent:
  decided_by: Colin Aubé
  decided_at: 2026-09-17
  decision: accepted (user-directed new requirement, approved in parent chat)
  session: native-coherence-bootstrap parent chat
---

# SR-SENTINEL-V0-W08 — Geofence hard stop

**Normative statement (user-directed, 2026-09-17):** the worker continuously
verifies its AprilTag-derived position against the configured room safety
envelope (the 1 m demo cube of W03). On envelope exit the worker autonomously
issues the terminal emergency hard stop without waiting for coordinator or
operator action; the condition is ingested as `EMERGENCY_LATCHED` when the
coordinator is reachable, and the W05 latch semantics (terminal, non-overridable,
no post-latch commands) apply unchanged. This is a fail-safe reflex only: it can
stop the drone and nothing else. The UI emergency stop (U03) and the
pre-transmission envelope check (W03) remain as before.

**Anchors:** V0 spec §9 (amendment 2026-09-17: geofence carve-out in non-goals);
WP4 §2–§3; W03 flight envelope; W05 emergency latch; indoor-alley design §1
(amendment: pose feeds the safety-cube check).

**Acceptance:** simulated envelope-exit produces the hard stop without any
coordinator/operator input; the run lands in `EMERGENCY`; no post-latch commands;
no non-stop autonomous action exists; position source is the W07 AprilTag pose.

**Evidence class:** implementation-pending (worker behavior; the reflex itself is
testable in simulation, real-drone proof is human/live).

**Coherence note (2026-09-21; the statement above is unchanged).** The V0 spec §10.3 records
three differences between this statement and what is implemented: W08-a, the reflex is written
as the terminal emergency hard stop but implemented as a controlled landing through the existing
abort path; W08-b, "continuously" is written but a fix exists only with tags in view and is
checked at localised stops; W08-c, "the 1 m demo cube" is written but the envelope is now derived
from the site file. Each awaits the operator's decision. Until then this requirement is **not
satisfied** and must not be reported as satisfied. The position source in practice is the WP5
`DronePoseReport` (SR-060), which generalises W07's per-station marker mode.
