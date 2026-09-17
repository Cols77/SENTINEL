---
id: SR-SENTINEL-V0-P04
title: LIVE vs SIMULATED/REPLAYED judged policy
statement: "with `judged_path: true`, `ArtifactRef.source_kind` and `PerceptionResult` source_kind/artifact_source_kind must all be `LIVE`; `SIMULATED` and `REPLAYED` are schema-invalid — `REPLAY_NOT_ALLOWED` for replay, `MISSING_EVIDENCE` for non-live evidence; deterministic fakes always emit `SIMULATED` (no “fake real-mode”); replay is rehearsal-only and rejected in judged runs; the UI cannot select replay in a judged run."
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

# SR-SENTINEL-V0-P04

**Status:** Accepted by human consent (2026-09-17). Normative statement, anchors,
acceptance criteria and proof shape are as recorded in the atomic SR matrix of
`.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md` (the
consent artifact). This file is the registration stub created by the bootstrap;
canonical text is the plan section — do not restate or paraphrase it here.

A02 (catalogue-bound proposals) is deferred pending the artifact-naming review
(see HANDOFF-artifact-naming-semantics.md) and has no stub by design.
