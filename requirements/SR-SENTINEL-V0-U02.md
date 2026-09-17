---
id: SR-SENTINEL-V0-U02
title: Approval/decision interactions
statement: "each Confirm/Reject attempt creates one full `CommandEnvelope` with a fresh `command_id`, correlation ID and idempotency key; until a terminal result or a state-version refresh, retries and duplicate clicks resend that same envelope/key and return the original result; actions render only from coordinator-declared `legal_operator_actions`; `FORCE_RUN_CREATE` is operator-only, from a failed preflight, unavailable under an active emergency latch."
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

# SR-SENTINEL-V0-U02

**Status:** Accepted by human consent (2026-09-17). Normative statement, anchors,
acceptance criteria and proof shape are as recorded in the atomic SR matrix of
`.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md` (the
consent artifact). This file is the registration stub created by the bootstrap;
canonical text is the plan section — do not restate or paraphrase it here.

A02 (catalogue-bound proposals) is deferred pending the artifact-naming review
(see HANDOFF-artifact-naming-semantics.md) and has no stub by design.
