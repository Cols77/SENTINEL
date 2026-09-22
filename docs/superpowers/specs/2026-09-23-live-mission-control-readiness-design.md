# Live Mission Control Readiness Design

## Goal

Allow `sentinel mission-control --mode live` to open its localhost operator UI without drone, provider, consent, or flight-confirmation prerequisites, while preserving every operational safety gate at the point where the corresponding capability is used.

## Scope

The Live UI starts in an explicit `NOT_READY`/unarmed state. It shows an allowlisted readiness summary and a concise operator/coding-agent message containing the exact missing CLI flags or environment-variable names. The message may include a copyable example command, but must never echo secret values.

This change does not add configuration editing, secret entry, passive hardware discovery, automatic fallback to Rehearsal, or automatic arming. Existing `/api/v1` route paths remain unchanged.

## Architecture

Split Live startup into two phases:

1. **Host composition:** construct the HTTP application, persistent session, public projection, and inert Live boundaries. This phase performs no drone connection, video startup, provider call, paid action, emergency transmission, or flight preflight.
2. **Capability admission:** validate the inputs required by planning, detection, emergency transport, and flight immediately before that capability becomes available. Admission failures are projected as public readiness facts and leave the session unarmed.

Live service composition uses deferred adapters. Their methods fail closed with stable public reason codes until admitted. Once all planning inputs are valid, bounded chat may create a proposal. Approval still authorizes only that immutable proposal; flight confirmation and the no-takeoff preflight are checked separately at dispatch. Provider authorization and recorded consent are checked before the detection supervisor starts.

## Public projection and UI

Schema v3 gains a backwards-compatible `readiness` object rather than a route change:

- overall state: `NOT_READY`, `READY_TO_PLAN`, `READY_TO_DISPATCH`, or `ACTIVE`;
- allowlisted checks for planning, flight, detection, and emergency transport;
- stable public reason codes;
- safe remediation text naming required flags, paths, and environment-variable names;
- no filesystem contents beyond explicitly supplied paths and no secret values.

The header identifies Live mode and its unarmed/readiness state. A readiness panel explains what is unavailable and gives the coding agent a copyable launch-command template. Controls remain visible but disabled when their capability is unavailable, with the relevant readiness reason exposed accessibly.

## Safety invariants

- Opening, refreshing, polling, chatting for status, or reading evidence never connects to the drone or provider.
- Missing or invalid Live inputs never select Rehearsal adapters.
- Planning cannot begin without valid site, tag-map, coverage, start-position, and pose inputs.
- Dispatch cannot begin without explicit flight confirmation, successful drone preflight, and the existing immutable proposal approval.
- Detection cannot begin without provider configuration, paid-call authorization, and recorded consent.
- Land and Emergency remain unavailable until their independent transports are initialized.
- Admission failure produces a visible reason and no partial hardware action beyond the bounded preflight explicitly being attempted.

## Testing

Tests first establish that a bare Live host serves `/console/` and schema-valid snapshots without touching injected drone/provider factories. Additional tests cover safe remediation messages, progressive readiness with supplied configuration, fail-closed planning and dispatch, provider/consent admission, no Rehearsal fallback, disabled browser controls, and successful configured Live composition. Existing Rehearsal, schema-v3, voice, safety, survey, and full-suite tests remain green.

