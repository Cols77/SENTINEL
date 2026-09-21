---
id: SR-D
title: "Pose never mutates flight state (fail-closed boundary)"
statement: "The system shall treat drone pose strictly as evidence/read-model data: no pose consumer shall exist in the flight path, no pose value shall be an input to any flight command decision in V0, and the pose slice shall be structurally incapable of mutating flight state — detector or ingestion failures degrade to missing/stale displayed pose, never to motion."
domain: safety
upstream: []
source: "docs/superpowers/specs/2026-09-16-sentinel-v0-coherence-spec.md#9"
---

## Scope

- Enforced by construction: pose events and the drone_pose read model have no producer-side edge into `src/sentinel/runner.py` flight sequencing, which keeps sole ownership of ordering, preflight, and abort policy (emergency latch unchanged).
- Any future closed-loop use of pose requires a new safety-reviewed requirement; it is out of scope here.

## Non-goals

- No closed-loop position hold, station-keeping, or pose-conditioned commands in V0.
- Flight remains the existing open-loop, operator-supervised command path behind the G4 live-rehearsal consent gate.
