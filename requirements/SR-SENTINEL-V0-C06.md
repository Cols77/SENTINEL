---
id: SR-SENTINEL-V0-C06
title: Approval eligibility policy
statement: "eligibility is `raw_score >= provider_profile.decision_threshold` with matching provider/model/prompt/transform revisions and non-null score; no score, stale/mismatched artifact, non-`LIVE` evidence in judged mode, replay in judged mode, or unapproved/missing owner-approved profile blocks approval; both ranking arrays are ordered non-empty (`minItems = 1`, `maxItems = 64`)."
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

# SR-SENTINEL-V0-C06

**Status:** Accepted by human consent (2026-09-17). Normative statement, anchors,
acceptance criteria and proof shape are as recorded in the atomic SR matrix of
`.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md` (the
consent artifact). This file is the registration stub created by the bootstrap;
canonical text is the plan section — do not restate or paraphrase it here.

A02 (catalogue-bound proposals) is deferred pending the artifact-naming review
(see HANDOFF-artifact-naming-semantics.md) and has no stub by design.
