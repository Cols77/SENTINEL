---
id: SR-SENTINEL-V0-C03
title: Run state machine and outcome vocabulary
statement: "the run advances only per the WP1 §4 transition matrix; terminal outcomes are `COMPLETED`, `FAILED`, `EMERGENCY`; `APPROVED`/`REJECTED` are not run states; `NEEDS_REVIEW` is a review-hold escaped only by an explicitly approved new/revisit plan; `PROVIDER_ERROR` and scoreless are total-and-disjoint keyed on status; an active emergency latch cannot be overridden."
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

# SR-SENTINEL-V0-C03

**Status:** Accepted by human consent (2026-09-17). Normative statement, anchors,
acceptance criteria and proof shape are as recorded in the atomic SR matrix of
`.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md` (the
consent artifact). This file is the registration stub created by the bootstrap;
canonical text is the plan section — do not restate or paraphrase it here.

A02 (catalogue-bound proposals) is deferred pending the artifact-naming review
(see HANDOFF-artifact-naming-semantics.md) and has no stub by design.
