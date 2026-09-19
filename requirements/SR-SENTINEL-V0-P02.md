---
id: SR-SENTINEL-V0-P02
title: Artifact store, hash and provenance integrity
statement: "`ArtifactStorePort.put_selected_frame()` accepts only JPEG/PNG below 10 MiB, computes SHA-256, writes run-scoped `storage_key` paths and returns `ArtifactRef`; `get(artifact_id)` is coordinator-mediated; hash equality is semantic — request `artifact_sha256 != artifact.sha256` is rejected with `ARTIFACT_HASH_MISMATCH`; V0 retention is run-lifetime plus explicit manual cleanup; absence/corruption fails evidence use."
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

# SR-SENTINEL-V0-P02

**Status:** Accepted by human consent (2026-09-17). Normative statement, anchors,
acceptance criteria and proof shape are as recorded in the atomic SR matrix of
`.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md` (the
consent artifact). This file is the registration stub created by the bootstrap;
canonical text is the plan section — do not restate or paraphrase it here.

A02 (catalogue-bound proposals) is deferred pending the artifact-naming review
(see HANDOFF-artifact-naming-semantics.md) and has no stub by design.
