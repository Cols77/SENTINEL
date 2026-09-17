---
id: SR-SENTINEL-V0-M02
title: Snapshot/state freshness derivation
statement: "on every committed event the coordinator atomically increments `state_version` and assigns the next `sequence_no`, writing a mountain snapshot with `snapshot_version == sequence_no` (projection-changing) or retaining the prior snapshot version; `STALE` is never persisted — it is derived as `now - as_of > mountain.fixture.snapshot_stale_after_ms OR snapshot_version < StateSnapshot.latest_event_sequence`; `latest_event_sequence` always equals the highest committed sequence."
domain: behavioral
status: accepted-pending-registration
feature: FEAT-SENTINEL-V0-BOOTSTRAP
consent:
  decided_by: Colin Aube
  decided_at: 2026-09-17
  decision: accepted
  session: "native-coherence-bootstrap parent chat (round 1: A01, C01-C05, W04-W06; round 2: all remaining)"
  artifact: .hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md
---

# SR-SENTINEL-V0-M02

**Status:** Accepted by human consent (2026-09-17). Normative statement, anchors,
acceptance criteria and proof shape are as recorded in the atomic SR matrix of
`.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md` (the
consent artifact). This file is the registration stub created by the bootstrap;
canonical text is the plan section — do not restate or paraphrase it here.

A02 (catalogue-bound proposals) is deferred pending the artifact-naming review
(see HANDOFF-artifact-naming-semantics.md) and has no stub by design.
