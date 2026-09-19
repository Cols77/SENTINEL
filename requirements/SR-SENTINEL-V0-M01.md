---
id: SR-SENTINEL-V0-M01
title: Six-zone catalogue and layout modes
statement: "the indoor-alley catalogue is exactly the six fixed zones; “section” and “zone” name the same entities across `SectionState.section_id`, `layout_modes[].active_zone_ids`, `SurveyPlanReference.station_ids`, `StationCaptureProvenance.zone_id`; `ROUTE_ONLY_2_ZONE` is exactly the two middle zones with no marker claim; a marker-mode run fails when an expected marker is missing."
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

# SR-SENTINEL-V0-M01

**Status:** Accepted by human consent (2026-09-17). Normative statement, anchors,
acceptance criteria and proof shape are as recorded in the atomic SR matrix of
`.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md` (the
consent artifact). This file is the registration stub created by the bootstrap;
canonical text is the plan section — do not restate or paraphrase it here.

A02 (catalogue-bound proposals) is deferred pending the artifact-naming review
(see HANDOFF-artifact-naming-semantics.md) and has no stub by design.
