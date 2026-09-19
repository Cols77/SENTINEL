---
id: SR-SENTINEL-V0-W04
title: Live video and station capture
statement: "`VideoStatus` reports lifecycle, freshness, dimensions, degradation and the selected-frame artifact ID/SHA-256; `SafeVideoStreamHandle` is an expiring read-only served endpoint with no storage path, bytes or raw-frame field; the coordinator must not persist video bytes or proxy raw frames; WP4 captures one to three live JPEG/PNG frames per approved station with full `StationCaptureProvenance`; marker-mode captures are accepted only with the expected marker; route-only captures are labelled `route-only, not visually verified`."
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

# SR-SENTINEL-V0-W04

**Status:** Accepted by human consent (2026-09-17). Normative statement, anchors,
acceptance criteria and proof shape are as recorded in the atomic SR matrix of
`.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md` (the
consent artifact). This file is the registration stub created by the bootstrap;
canonical text is the plan section — do not restate or paraphrase it here.

A02 (catalogue-bound proposals) is deferred pending the artifact-naming review
(see HANDOFF-artifact-naming-semantics.md) and has no stub by design.
