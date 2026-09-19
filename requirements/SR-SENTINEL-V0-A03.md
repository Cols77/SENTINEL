---
id: SR-SENTINEL-V0-A03
title: Agent tool allowlist
statement: "**Normative statement (user-directed, 2026-09-17):** the agent's invocable tool surface is a closed, versioned allowlist owned by WP0 and referenced by `tool_policy_revision` in `llm-adapter-config-v1`. The V0 default allowlist contains no side-effecting tools: no drone control, no dispatch, no SQLite access. World and run facts reach the agent only as typed read-only `AgentRequest` fields. The allowlist schema and its enumeration are a WP0 contract artifact; any future tool addition is a schema amendment plus a `tool_policy_revision` bump."
domain: behavioral
status: accepted-pending-registration
feature: FEAT-SENTINEL-V0-BOOTSTRAP
consent:
  decided_by: Colin Aubé
  decided_at: 2026-09-17
  decision: accepted (user-directed new requirement, approved in parent chat)
  session: native-coherence-bootstrap parent chat
---

# SR-SENTINEL-V0-A03 — Agent tool allowlist

**Normative statement (user-directed, 2026-09-17):** the agent's invocable tool
surface is a closed, versioned allowlist owned by WP0 and referenced by
`tool_policy_revision` in `llm-adapter-config-v1`. The V0 default allowlist
contains no side-effecting tools: no drone control, no dispatch, no SQLite
access. World and run facts reach the agent only as typed read-only `AgentRequest`
fields. The allowlist schema and its enumeration are a WP0 contract artifact; any
future tool addition is a schema amendment plus a `tool_policy_revision` bump.

**Anchors:** WP1 §2, §3.1 (amendment 2026-09-17); WP1 §2 ownership boundary;
A01 adapter boundary; `contracts/llm-adapter-config-v1.schema.json`
(`tool_policy_revision`).

**Acceptance:** the allowlist artifact exists and validates; the V0 enumeration
contains zero side-effecting tools; the adapter refuses/never constructs any tool
invocation outside the allowlist; a test proves the agent path has no DB/drone
import.

**Evidence class:** implementation-pending (enforcement) + contract/bootstrap-
verifiable (allowlist schema).
