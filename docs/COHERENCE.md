---
id: sentinel-coherence-authority-map
title: "SENTINEL Coherence Authority Map"
status: review-pending
---

# SENTINEL Coherence Authority Map

> **Status:** Preparation-only map, pending review. It records, from repository evidence, who owns SENTINEL contract semantics, how contract artifacts are labelled, and who may change them.
> **Limitation:** native Coherence contract artifacts — the upstream contract-artifact capability that emits semantic requirements (SRs) and projections from these documents — are **not landed**. Until that capability is landed and independently verified, this map is documentation-only preparation and nothing in it is machine-enforced by a Coherence compiler.

## 1. Authority hierarchy

Ordered from most to least authoritative, per the [V0 coherence spec](superpowers/specs/2026-09-16-sentinel-v0-coherence-spec.md) §3:

1. **V0 coherence spec** — selects and defines the V0 product/integration profile (`sentinel.v0` contract revision). It is the canonical profile document and the top of the hierarchy.
2. **Indoor-alley survey design** ([2026-09-16-sentinel-v0-indoor-alley-survey-design.md](superpowers/specs/2026-09-16-sentinel-v0-indoor-alley-survey-design.md)) — the V0 decision record for the room-demo (six-zone route, station capture, layout modes, preflight/force-run/emergency semantics). It ranks just below the coherence spec and everything below must conform to it.
3. **WP0 shared-coherence contracts** ([2026-09-16-sentinel-wp0-shared-coherence-contracts.md](superpowers/specs/2026-09-16-sentinel-wp0-shared-coherence-contracts.md)) **plus** its machine-readable contract artifacts (`docs/superpowers/specs/contracts/`) — define executable shared semantics: the directly-validatable public root [`sentinel-v0-contract.schema.json`](superpowers/specs/contracts/sentinel-v0-contract.schema.json), the `oneOf` wire registry [`sentinel-v0-wire.schema.json`](superpowers/specs/contracts/sentinel-v0-wire.schema.json), the mountain fixture, the survey-catalogue, the provider-profile and evaluation-manifest schemas, and the DDL/constraint manifest.
4. **WP1–WP3 component specs** — define component behavior only within WP0; they may not contradict WP0 or the survey design.
5. **Parent database specification** ([SENTINEL_Demo_State_Database_Spec_v0.2.md](SENTINEL_Demo_State_Database_Spec_v0.2.md)) — remains the persistence/safety source except where the V0 mapping table explicitly defines a V0 representation.
6. **Historical broad component drafts** — supporting design history, not current V0 authority (this includes the retained [DJITelloPy backend plan](superpowers/plans/2026-09-15-sentinel-djitellopy-backend.md), classified retained/deferred/non-authoritative in place).

The parent→V0 compatibility mapping table in the coherence spec §3 is binding: *no component may use a parent value outside that table without a WP0 compatibility amendment*.

## 2. Artifact lifecycle labels

| Label | Meaning (repository evidence) | Where defined |
|---|---|---|
| `sentinel.v0` | contract revision of the product baseline; `contract_version` on wire documents and the DDL manifest | coherence spec §1/§6; `sentinel-v0-wire.schema.json`; `ddl_manifest.json` |
| `sentinel.survey-catalogue.v1` | `contract_version` of catalogue instances | `survey-catalogue-v1.fixture.json` / `survey-catalogue-v1.schema.json` |
| `source_kind: LIVE / SIMULATED / REPLAYED` | provenance of any artifact, snapshot, perception record or video | wire schema `#/$defs/source_kind`; WP0 §3.4–§6; WP4 spec |
| `approval_status: UNAPPROVED` | provider-profile template; present, valid, **not selectable** in the judged path | `provider-profile-unapproved.template.json`; WP0 §5 |
| `approval_status: APPROVED` | a concrete provider profile approved by a *named joint Colin+Dzivha record* over a versioned test-image-set hash; without that record a profile is not authoritative | WP0 §5; coherence spec D-007/G3 |
| run outcome: `COMPLETED / FAILED / EMERGENCY` | the only terminal run statuses; `EMERGENCY` is terminal and non-overridable | WP0 §3.3.2; wire schema `StateSnapshot` |
| run outcome: `NEEDS_REVIEW` | review-hold, never terminal; escaped only by an approved new/revisit plan | WP0 §3.3.2; coherence spec §4 |
| run outcome: `REJECTED` | event-only (`PLAN_REJECTED`, `OPERATOR_ASSIGNMENT_REJECTED`); refined and re-proposed, never a terminal run status | WP0 §3.3.2 |
| operation lifecycle | `PENDING → DISPATCHED → SUCCEEDED\|FAILED`, `PENDING\|DISPATCHED → IN_DOUBT`, `IN_DOUBT → SUCCEEDED\|FAILED` via `WorkerPort.reconcile` only | WP0 §4 |
| plan lifecycle | `PLAN_PROPOSED → PLAN_CREATED → PLAN_APPROVED\|PLAN_REJECTED` | wire schema event enum; probe test |
| evidence artifact lifecycle | `put_selected_frame`: JPEG/PNG < 10 MiB, SHA-256, run-scoped `storage_key` → `ArtifactRef`; retention is run-lifetime plus explicit manual cleanup | WP0 §5 |
| document status | `review-pending` for the documents in this preparation set (coherence spec, WP0 spec, this map) | frontmatter of each document |

## 3. Ownership and change order

- **Semantic ownership (PM decisions, coherence spec §2 D-002):** Colin owns coordinator semantics; Alan owns the LLM adapter and WP2; Dzivha owns WP3; WP0 is owned by Colin / PM-architect (WP0 spec header). Every WP has one dedicated spec and a common contract revision (D-001); no overlapping write ownership.
- **Gate ownership (coherence spec §8):** G0 contract (Colin), G1 coordinator simulation (Colin + Alan), G2 human surface (Alan), G3 perception (execution Dzivha; approved/reviewed Colin + Dzivha), G4 physical (Colin). V0 release requires G0–G4.
- **Provider authority (D-007, G3):** the checked-in provider-profile instance is the `UNAPPROVED` template; **G3 is BLOCKED**. An approved profile requires a named joint Colin+Dzivha approval over a versioned test-image-set hash with reproducible evaluation-manifest evidence. No provider name, threshold, evaluation data, or benchmark result is declared by the specifications.
- **Change order — normative text:** the V0 coherence spec selects the profile; the survey design fixes the room-demo decisions; the WP0 spec and its schemas define executable semantics. Component specs (WP1–WP3) and implementations may only refine within WP0. The parent spec supplies persistence/safety defaults only where the mapping table is silent.
- **Change order — schema and code:** the JSON Schema is authoritative for wire representation (WP0 §3); frozen dataclasses are convenience wrappers that must round-trip without information loss; cross-WP ports MUST reference canonical schema IDs (`https://sentinel.local/contracts/sentinel-v0-wire.schema.json#...`), not unconstrained objects (WP0 §3.2). WP0 declares the DDL/constraint manifest; WP1 owns the production migration/repository implementation. A parent value outside the mapping table requires a WP0 compatibility amendment before any component may use it (coherence spec §3).

## 4. Catalog ownership boundary

"Catalog" is spelled **catalogue** in SENTINEL documents.

- The **worker owns a finite survey catalogue** (survey design): each catalogue entry carries an ID/revision/hash, a layout mode, station steps, capture headings/count, segment heights, route bounds, timeout and command budget. The agent may propose **only catalogue-bound plan references**; it cannot emit raw station text (`AgentModelResult.catalogue_plan_id` is a typed configured ID; probe test `test_agent_proposal_is_catalogue_bound_and_force_run_is_typed`).
- The **catalogue contract is a WP0 artifact**: [`survey-catalogue-v1.schema.json`](superpowers/specs/contracts/survey-catalogue-v1.schema.json) is listed in WP0's normative spec-artifacts and is what "the approved catalogue for a plan validates against" (WP0 §3.2). [`survey-catalogue-v1.fixture.json`](superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json) is an example catalogue instance (`SURVEY_CATALOGUE_V1` / `REV_1`, `contract_version: sentinel.survey-catalogue.v1`), not an approved on-drone catalogue.
- **Consumption boundary:** WP4 consumes the catalogue through the shared worker/operation contracts; WP2 renders plan read models; WP3 never invents catalogue entries or zone semantics. Indoor-alley runs use only the six catalogue-bound zones (`mountain-v0.fixture.json`), and `SectionState.section_id`, `layout_modes[].active_zone_ids`, `SurveyPlanReference.station_ids` and `StationCaptureProvenance.zone_id` use the identical six IDs (WP0 §3.3.1).

## 5. Tests and evidence are consumers, not normative contract sources

- **Schemas and fixtures are normative; validators and tests are consumers.** The public root `sentinel-v0-contract.schema.json` / wire registry `sentinel-v0-wire.schema.json` are the directly-validatable compatibility roots (coherence spec §6; WP0 §3). `tests/contracts/` and `tests/test_indoor_alley_contract_probe.py` load those artifacts and assert behavior; they never define wire semantics, event vocabularies, or mappings.
- **Deterministic test fixtures record no authority.** `fixtures/sentinel/v0/` and the fixture package report `SIMULATED` provenance; deterministic fakes always emit `SIMULATED`; there is no "fake real-mode" and no simulated/replayed judged evidence (WP0 §5; `fixtures.py`).
- **Evidence demonstrates conformance; it does not grant authority.** Evaluation manifests and gate evidence make a claim reproducible and reviewable, but the only artifact that authorizes a provider in the judged path is the named joint Colin+Dzivha approval over a versioned test-image-set hash (coherence spec D-007/G3; WP0 §5). An `approval_status: APPROVED` value inside a simulated fixture is fixture data, not a joint approval record.
- **Fixture validity ≠ provider authorization or benchmark evidence.** A schema-valid fixture or profile instance is not authorization, and no checked-in data set constitutes benchmark evidence (fixtures README).

## 6. Native catalog / projections / evidence: blocked until upstream capability lands

The Coherence **contract-artifact capability** — native generation of semantic requirements (SRs) and projections from contract documents — is an upstream capability that is **not landed** in this baseline. Consequences, stated here so no later step invents otherwise:

- **No SRs or native projections are authored** as part of this preparation; the map above and the document frontmatter are the preparation surface.
- **Native catalog, projections, and evidence remain blocked** (i.e., are not produced, consumed, or claimed) until the upstream capability is landed **and independently verified** — verified meaning demonstrated against this repository's contracts by an independent check, not by the capability's own self-report.
- Until then, the authority map in this document, the spec frontmatter, and the fixture classifications are documentation-only and subordinate to the documents in §1.