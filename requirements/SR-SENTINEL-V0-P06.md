---
id: SR-SENTINEL-V0-P06
title: Retry, error, no invented score, isolation
statement: "provider retries are adapter-local for the same request ID only; exhaustion returns a typed provider error, never an invented score; no provider switching or physical retry; a `PROVIDER_ERROR` result marks the run `FAILED` before any score interpretation; WP3 never writes SQLite, coordinator events or drone commands."
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

# SR-SENTINEL-V0-P06

**Status:** Accepted by human consent (2026-09-17). Normative statement, anchors,
acceptance criteria and proof shape are as recorded in the atomic SR matrix of
`.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md` (the
consent artifact). This file is the registration stub created by the bootstrap;
canonical text is the plan section — do not restate or paraphrase it here.

A02 (catalogue-bound proposals) is deferred pending the artifact-naming review
(see HANDOFF-artifact-naming-semantics.md) and has no stub by design.
