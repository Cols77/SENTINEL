---
id: SR-SENTINEL-V0-P03
title: Result status, class normalization, boxes
statement: "`PerceptionResult` carries every schema field including source/artifact-source kind, revisions and `judged_path`; `DETECTED` requires `rock|pebble_cluster` and a numeric score; `NOT_DETECTED` requires `none` and a numeric score; `UNSCORED`/`PROVIDER_ERROR` require `unknown` and a null score; canonical class is always `rock` under mapping revision `v0-rock-only`; a present `bbox_xyxy_normalized` must satisfy `0 <= x1 < x2 <= 1` and `0 <= y1 < y2 <= 1`."
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

# SR-SENTINEL-V0-P03

**Status:** Accepted by human consent (2026-09-17). Normative statement, anchors,
acceptance criteria and proof shape are as recorded in the atomic SR matrix of
`.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md` (the
consent artifact). This file is the registration stub created by the bootstrap;
canonical text is the plan section — do not restate or paraphrase it here.

A02 (catalogue-bound proposals) is deferred pending the artifact-naming review
(see HANDOFF-artifact-naming-semantics.md) and has no stub by design.
