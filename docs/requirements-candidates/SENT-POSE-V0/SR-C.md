---
id: SR-C
title: "Run-scoped pose persistence and drone_pose projection"
statement: "The system shall persist accepted DRONE_POSE_REPORTED events run-scoped in the coordinator SQLite store and project them into LocalPose/drone_pose in the mountain state snapshot, applying the WP0 section 3.4 staleness rule, via a minimal coordinator-side ingestion slice whose full replacement remains the WP1 boundary."
domain: behavioral
upstream: []
source: "docs/superpowers/specs/2026-09-16-sentinel-wp0-shared-coherence-contracts.md#3.4"
---

## Scope

- Minimal ingestion slice: append pose events to the run-scoped store; a test proves `drone_pose` artifacts exist end-to-end from a valid report.
- Staleness: the snapshot exposes pose with its observation time; consumers see stale-marked pose per the section 3.4 rule rather than an ageless position.

## Non-goals

- No full coordinator implementation (WP1 boundary unchanged).
- No historical trajectory queries in V0.
