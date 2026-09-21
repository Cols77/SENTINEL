---
id: SR-SENTINEL-V0-W08
title: Geofence controlled landing
statement: "**Normative statement (user-directed 2026-09-17; amended 2026-09-21):** the worker verifies its AprilTag-derived position, at every localised stop and during moves whenever tags are in view, against the configured safety envelope, which is the operating cube derived from the site file (W03). When a measured position is outside that envelope the worker, without waiting for coordinator or operator action, ends the flight through the existing abort policy with a **controlled landing**. This is a fail-safe reflex only: it can end the flight and nothing else. The terminal emergency motor cut remains the operator's control (U03) with the W05 latch semantics unchanged; the pose-triggered reflex does not arm that latch. The pre-transmission envelope check (W03) remains as before. Where no tag is in view there is no fix and no check; the pre-transmission envelope check is what covers those intervals."
domain: behavioral
status: accepted-pending-registration
feature: FEAT-SENTINEL-V0-BOOTSTRAP
consent:
  decided_by: Colin Aubé
  decided_at: 2026-09-17
  decision: accepted (user-directed new requirement, approved in parent chat)
  session: native-coherence-bootstrap parent chat
amendments:
  - decided_by: Colin Aubé
    decided_at: 2026-09-21
    decision: W08-a controlled landing replaces the motor cut; W08-c envelope is the site-derived cube; W08-b checked at every localised stop and during moves whenever tags are in view
    session: coherence-alignment session
---

# SR-SENTINEL-V0-W08 — Geofence controlled landing

**Normative statement (user-directed 2026-09-17; amended 2026-09-21):** the worker verifies its AprilTag-derived position, at every localised stop and during moves whenever tags are in view, against the configured safety envelope, which is the operating cube derived from the site file (W03). When a measured position is outside that envelope the worker, without waiting for coordinator or operator action, ends the flight through the existing abort policy with a **controlled landing**. This is a fail-safe reflex only: it can end the flight and nothing else. The terminal emergency motor cut remains the operator's control (U03) with the W05 latch semantics unchanged; the pose-triggered reflex does not arm that latch. The pre-transmission envelope check (W03) remains as before. Where no tag is in view there is no fix and no check; the pre-transmission envelope check is what covers those intervals.

**Anchors:** V0 spec §9 (amendments 2026-09-17 and 2026-09-21) and §10.2–§10.3; WP4 §2–§3; W03
flight envelope; W05 emergency latch; WP5 cube design §3.5 and §4; `docs/safety.md` (cube mission).

**Acceptance:** a simulated envelope exit produces a controlled landing without any
coordinator/operator input, both at a localised stop and while a move is executing; no motion command other than the landing follows it; the run is
reported as failed with the containment reason, not as `EMERGENCY`; no non-stop autonomous action
exists; the position source is the WP5 `DronePoseReport` (SR-060), which generalises W07's
per-station marker mode.

**Evidence class:** implementation-pending (worker behavior; the reflex itself is testable in
simulation, real-drone proof is human/live).

**Amendment record (2026-09-21, Colin Aubé, in the coherence-alignment session).**

- **W08-a, decided:** the reflex is a controlled landing, not the terminal emergency hard stop the
  2026-09-17 text named. A single-tag fix is realistically noisy wherever only one tag is in view
  (measured 10.4-12.5 cm spread off-axis, docs/wp5-rehearsal-manifest.md stand test), so a false
  "outside" is a realistic event there; with two tags in view the joint solve measured 0.45 cm at
  the same range, so a false "outside" is implausible at a stop where both are seen. A motor cut
  at up to 1.8 m near people is the more harmful failure regardless. This is also what the cube
  mission already implements.
- **W08-c, decided:** "the 1 m demo cube of W03" becomes "the configured safety envelope", the
  operating cube derived from the site file.
- **W08-b, decided:** "continuously" becomes "at every localised stop and during moves whenever
  tags are in view", which is what a camera-based fix can honestly deliver.

**Implementation status (2026-09-21, updated later the same day): the in-move watchdog now exists
in software; the requirement is still not satisfied.** The stop-time check exists (`passive` and
`reanchor` modes). The in-move watchdog is now built: `sentinel.pose.watchdog.PoseWatchdog` runs on
a background thread beside the blocking `move` (`CubeMissionRunner.move_checked`), evaluating the
already-open pose stream and debouncing on 2 consecutive `OUTSIDE` **joint (two-tag)** fixes before
it sets an abort event, ending in the existing controlled landing.

**Bench test B1 (`docs/wp5-rehearsal-manifest.md`, flown by the WP5 session) has so far shown**
that the Tello honours `land` sent while a `move` executes: the move answers `error Auto land`
within 0.3 s and the drone travels a further 12 cm at the missions' `speed 20` (about 35 cm at
`speed 50`). `stop` is not a command on this firmware. From that the software now has: a
lock-free `land` (`DjitellopyClient.land_now`; the ordinary `land()` waits for the lock the
in-flight move holds and could never interrupt it), `error Auto land` reported as the
containment abort it is, and a trip line `trip_margin_cm(speed)` inside the cube edge, about
26 cm at mission speed: (debounce + 0.2 s video lag) x speed + travel after `land`.

Sending `land` mid-move stays behind a constructor flag that defaults **off**: B1's pass bar is
9 runs and 2 are flown. With the flag off an abort takes effect when the move returns. Until B1
passes and the flag is on for flights, this requirement is **not satisfied** and must not be
reported as satisfied.
