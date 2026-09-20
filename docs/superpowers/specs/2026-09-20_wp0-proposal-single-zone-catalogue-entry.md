---
status: proposed (Coherence consent required — no gate opened yet; this document grants none)
proposed_by: P2 Task C — Shadow Advisors follow-up
  (`docs/superpowers/plans/2026-09-19-p2-shadow-advisors-F.md`, gate G-labels decision 3)
proposed_at: 2026-09-20
scope: `docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json` (catalogue content) and
  `docs/superpowers/specs/contracts/survey-catalogue-v1.schema.json` (catalogue shape)
implements_in: not scheduled. **This document does not modify the catalogue fixture or the catalogue schema.**
  It is a diff proposal only, matching the "Proposal only, you consent" pattern already used for this catalogue
  (see `fixtures/decisions/README.md` §4).
consent_required: yes — operator Coherence consent, never self-granted. This document is the proposal artifact;
  it does not open or satisfy that consent gate.
---

# WP0 proposal — a single-zone (`1_ZONE`) catalogue entry

## 1. The gap

`docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json` (revision `REV_1`) has exactly two
entries:

| id | mode | stations |
|---|---|---|
| `PLAN_MARKER_VERIFIED_6` | `MARKER_VERIFIED` | 6 (all zones, marker-verified) |
| `PLAN_ROUTE_ONLY_2_ZONE` | `ROUTE_ONLY_2_ZONE` | 2 (`LEFT_ALLEY_MIDDLE`, `RIGHT_ALLEY_MIDDLE`) |

There is no entry for **exactly one** station. `legal_tools.load_catalogue_plans()` turns each catalogue entry
into one `SELECT_PLAN` candidate 1:1 (`legal_tools.py:98-101`), so a request that names exactly one station has no
candidate that matches it — the narrowest available plan, `PLAN_ROUTE_ONLY_2_ZONE`, still visits two.

This is the same lexical trap already documented against the WP1 live gate. From
`docs/superpowers/specs/2026-09-19_wp1-architecture-review.md`, §2.4, on the `initial_plan_one_station` scenario:

> **`initial_plan_one_station` is self-contradictory**: "Survey only the LEFT_ALLEY_MIDDLE station... exclude any
> plan that... covers more stations than requested" — with the only non-excluded plan being
> `PLAN_ROUTE_ONLY_2_ZONE` (2 stations). Literal reading excludes everything; the model's `finish(NEEDS_REVIEW)`
> was correct escalation.

That review's own name for the underlying defect is "the station-vs-plan-zone confusion" (§2.4, fix option (b)):
`PLAN_ROUTE_ONLY_2_ZONE`'s id and its `ROUTE_ONLY_2_ZONE` mode both encode a station *count*, and any request that
also states a station count (one, in both cases) is read against that count literally. GLM's `finish(NEEDS_REVIEW)`
on the live gate and the operator's own hand labels on this fixture are the same call for the same reason: there is
no catalogue plan whose station count is 1, so a 1-station request is correctly unsatisfiable by the current
catalogue, not by either the model or the operator label logic.

The operator's P2 gate-`G-labels` decision 3 recorded this directly (`fixtures/decisions/README.md` §3, quoted
verbatim): *"Seems like `PLAN_ROUTE_ONLY_2_ZONE` does not make sense if you're visiting one zone?"* — followed by
*"Escalate, and propose a catalogue entry"*. `sp-001`..`sp-006` in `fixtures/decisions/turns.v1.jsonl` are
currently labelled `escalate` for exactly this reason (`turn_generator.py:68-78`, rationale text: *"a WP0 proposal
for a 1-zone catalogue entry is pending (Task C)"*). This document is that proposal.

## 2. Proposed entries

One `ROUTE_ONLY_1_ZONE` plan per catalogue station, so every station namable in a request has an exact-match
candidate, mirroring how `PLAN_MARKER_VERIFIED_6` already covers "all six" and `PLAN_ROUTE_ONLY_2_ZONE` covers "the
middle pair":

| id | mode | station |
|---|---|---|
| `PLAN_ROUTE_ONLY_1_ZONE_LEFT_ALLEY_FRONT` | `ROUTE_ONLY_1_ZONE` | `LEFT_ALLEY_FRONT` |
| `PLAN_ROUTE_ONLY_1_ZONE_LEFT_ALLEY_MIDDLE` | `ROUTE_ONLY_1_ZONE` | `LEFT_ALLEY_MIDDLE` |
| `PLAN_ROUTE_ONLY_1_ZONE_LEFT_ALLEY_BACK` | `ROUTE_ONLY_1_ZONE` | `LEFT_ALLEY_BACK` |
| `PLAN_ROUTE_ONLY_1_ZONE_RIGHT_ALLEY_FRONT` | `ROUTE_ONLY_1_ZONE` | `RIGHT_ALLEY_FRONT` |
| `PLAN_ROUTE_ONLY_1_ZONE_RIGHT_ALLEY_MIDDLE` | `ROUTE_ONLY_1_ZONE` | `RIGHT_ALLEY_MIDDLE` |
| `PLAN_ROUTE_ONLY_1_ZONE_RIGHT_ALLEY_BACK` | `ROUTE_ONLY_1_ZONE` | `RIGHT_ALLEY_BACK` |

Only the two `_LEFT_ALLEY_MIDDLE`/`_RIGHT_ALLEY_MIDDLE` entries are strictly required to close the gap `sp-001`..
`sp-006` exercise today (those six draft requests only ever name the two middle stations). Proposing all six keeps
the catalogue's station coverage uniform — otherwise a request naming, say, `LEFT_ALLEY_FRONT` alone would still
escalate while `LEFT_ALLEY_MIDDLE` alone would not, which is its own lexical trap. A narrower alternative — ship
only the two middle-station entries now, defer the other four — is available if the operator wants to bound this
proposal's blast radius to exactly what `sp-001`..`sp-006` need.

**Justified alternative considered and not preferred:** a single parametric entry (e.g. `mode: "ROUTE_ONLY_1_ZONE"`
with a `station_steps` array of length 1, no per-station id) plus request-time station selection. Rejected here
because `legal_tools.load_catalogue_plans()` maps one catalogue entry to exactly one `SELECT_PLAN` candidate
(`legal_tools.py:98-101`) — the "operator/model picks a station" step does not exist in the current `SELECT_PLAN`
tool contract (`inspection.propose_plan` takes only `catalogue_plan_id`, no zone argument), so a parametric single
entry would need a wire/tool-contract change in addition to a catalogue change. Six concrete entries need only a
catalogue change.

**Catalogue revision and `sha256` implications.** The catalogue's `revision` field (currently `REV_1`) and
top-level `sha256` are both content identity: `revision` per the schema's `^[A-Z][A-Z0-9_]{2,63}$` pattern, `sha256`
a 64-hex-character digest (`survey-catalogue-v1.schema.json:13`). Adding entries changes the document's content, so:

- The catalogue's `revision` must bump (e.g. `REV_1` → `REV_2`).
- Each new entry needs its own `revision` (`REV_1`, being new) and its own `sha256`.
- The top-level `sha256` must be recomputed over the new document content.

Note for whoever implements this (not this document): the fixture's current `sha256` values
(`aaaa...`, `bbbb...`, `cccc...`) are placeholders, not real digests — there is no canonicalization function in
`catalogue.py` that produces or verifies the catalogue- or entry-level `sha256` today (`CatalogueResolver` computes
a *different*, resolution-time `plan_sha256` over `{plan_id, catalogue_id, catalogue_revision, station_ids}`,
`catalogue.py:58-69`, not over the entry document). Implementation must either define a real canonicalization for
the catalogue/entry `sha256` fields before computing them, or confirm the placeholder convention is intentional and
keep it consistent (new entries get equally-shaped placeholders, not real hashes, until that function exists). This
gap is pre-existing and not introduced by this proposal, but any change to `entries` makes it visible.

## 3. The naming problem

`PLAN_ROUTE_ONLY_2_ZONE`'s id (and its `ROUTE_ONLY_2_ZONE` mode) encodes a station *count* in the plan name. §1
showed this is exactly the confusion the WP1 review flagged: a station-count in a request reads as if it should
match a station-count in the plan name, when the plan name is describing the plan's own shape, not a filter over
request text. Proposing `PLAN_ROUTE_ONLY_1_ZONE_<STATION>` for the new entries **keeps that same pattern** rather
than fixing it, and doubles down on count-in-name for six new ids.

Two renaming options, neither adopted by this document:

1. **Rename by station identity, not count.** `PLAN_ROUTE_ONLY_2_ZONE` → `PLAN_ROUTE_ONLY_MIDDLE_PAIR` (or similar);
   the six new entries → `PLAN_ROUTE_ONLY_<STATION>` (drop `_1_ZONE`, since a single-station id needs no count
   qualifier once the pair plan no longer has one either). This removes the count-in-name collision at its root,
   matching the WP1 review's fix option (b) for `PLAN_ROUTE_ONLY_2_ZONE` specifically, generalized to every plan
   this proposal adds.
2. **Keep `PLAN_ROUTE_ONLY_2_ZONE`'s id unchanged** (it is already load-bearing: referenced by
   `turn_generator.py:30`, `tests/coordinator/test_operator_choice.py`, `tests/agent/test_live_gate_real_loop.py:214`,
   `tests/test_scenario_replay.py:32`, and elsewhere) and accept that the new `_1_ZONE` ids are internally
   consistent with each other even if not with the older plan's name. Lower churn, but leaves the original lexical
   trap this section describes unresolved for anyone reading `PLAN_ROUTE_ONLY_2_ZONE` against a 1-station request
   in the future (i.e. it only closes the *catalogue-coverage* gap in §1, not the *naming* half of it).

This document takes no position between the two; it is an open question for the operator to resolve alongside
accepting or rejecting §2's entries.

## 4. Effect on fixture labels

If the entries in §2 (all six, or just the two middle-station ones) are accepted and the catalogue is amended:

- `sp-001`..`sp-006` in `fixtures/decisions/turns.v1.jsonl` would relabel from `escalate` to the matching new plan
  id (`sp-001`/`sp-003`/`sp-005`, which all name `LEFT_ALLEY_MIDDLE`, → `PLAN_ROUTE_ONLY_1_ZONE_LEFT_ALLEY_MIDDLE`
  or its renamed equivalent; `sp-002`/`sp-004`/`sp-006`, which name `RIGHT_ALLEY_MIDDLE`, → the corresponding
  right-station plan). `turn_generator.py`'s single-station branch (`turn_generator.py:68-78`, currently forced to
  `"escalate"` by decision 3) would need to change to select the per-station plan id instead — this is the same
  code path decision 3 already touched once, so it would be touched again, not a new code path.
- `SELECT_PLAN`'s legal set gains candidates: `legal_set()` builds one candidate per catalogue entry plus one
  `escalate` (`legal_tools.py:98-101`), so today's `SELECT_PLAN` legal-set size is 3 (2 plans + escalate); with all
  six new entries it becomes 9 (8 plans + escalate), or 5 if only the two required middle-station entries ship.
  This changes what `pending_decision()`/`sentinel pending` shows the operator (`operator_choice.py`), what
  `ShadowLadder` records as `legal_labels` at every `SELECT_PLAN` judgment record (`shadow.py`), and the option set
  any advisor chooses among at that decision point — none of which is a code defect, but all of it is observable
  surface that changes shape.
- `fixtures/decisions/README.md`'s "Label status" section (currently recording gate `G-labels`'s decisions,
  including decision 3's *"a WP0 proposal for a 1-zone catalogue entry is drafted separately (Task C)"*) would need
  a follow-up entry recording the new relabelling and a fresh operator approval for it — this is a second labelling
  decision, not an edit folded into the existing G-labels record, since G-labels already closed.

## 5. What must be re-verified after the change

In dependency order:

1. **Schema amendment first** (see §6 — required before the fixture can even validate; out of scope for this
   document and for the task that produced it).
2. **Catalogue fixture edit** (`survey-catalogue-v1.fixture.json`) validated against the amended schema —
   `CatalogueResolver.from_fixture()` (`catalogue.py:26-38`) already runs `Draft202012Validator` over it, so
   `tests/coordinator/test_catalogue_plans.py` is the direct check.
3. **`legal_tools` candidate generation** — `tests/coordinator/test_legal_tools.py`: `load_catalogue_plans()`
   returns the new entries with correctly formatted descriptions (`legal_tools.py:72-76`), and `legal_set()`'s
   `SELECT_PLAN` branch includes them.
4. **Turn fixture regeneration** — `SENTINEL_REGEN_TURNS=1 uv run python -m pytest
   tests/coordinator/test_turn_fixture.py -q -o addopts=''`, after updating `turn_generator.py`'s single-station
   branch (§4). The golden test in `test_turn_fixture.py` compares `packet`/`candidates` by `turn_id`, so it will
   fail (candidates changed) until regenerated, and the relabelled `sp-001`..`sp-006` need a fresh operator
   approval recorded in `fixtures/decisions/README.md` per §4 (this is a second `G-labels`-shaped gate, not a
   silent regeneration).
5. **Non-interference and audit-shape tests** — `tests/coordinator/test_shadow.py`: `SELECT_PLAN`'s `legal_labels`
   list in judgment audit records grows; any test asserting an exact candidate/label list for `SELECT_PLAN` needs
   updating.
6. **Operator-choice and CLI tests** — `tests/coordinator/test_operator_choice.py`, `tests/test_cli_decide.py`:
   `pending_decision()` output gains options; any hardcoded expectation of exactly `{PLAN_MARKER_VERIFIED_6,
   PLAN_ROUTE_ONLY_2_ZONE, escalate}` at `SELECT_PLAN` breaks.
7. **Live-gate and replay fixtures** — `tests/agent/live_scenarios.json`, `tests/agent/test_live_gate_real_loop.py`
   (`initial_plan_one_station` expects `PLAN_ROUTE_ONLY_2_ZONE`, line 214), `tests/test_scenario_replay.py`: these
   encode the *current* lexical trap as an expected escalation/plan choice; once a matching 1-zone plan exists,
   `initial_plan_one_station`'s expected outcome and prose both need review against §1's finding that the trap is
   now closed by catalogue coverage rather than by prose alone.
8. **Shadow evaluation re-run** — Task 5/6's `shadow_eval`/`shadow_sweep`/rehearsal scoring (`P2 plan §Task 5-6`)
   treats `SELECT_PLAN` fixture labels as the judgment-benchmark truth (`fixtures/decisions/README.md`, "the
   `SELECT_PLAN` labels remain the judgment benchmark"); relabelling `sp-001`..`sp-006` changes that truth set, so
   any already-recorded shadow sweep or rehearsal scoring that scored against the old (`escalate`) labels is stale
   for those six turns and should be re-run, with fresh consent per Task 5 Step 5 / Task 6 Step 4's live-call
   gates (this document authorizes no such live re-run itself).
9. **Full suite** — `uv run python -m pytest tests/ -q -o addopts=''` from the repo root, 0 failures, as the final
   check after 1–8.

## 6. Schema impact (why this needs a schema amendment too, not just a fixture edit)

`survey-catalogue-v1.schema.json` currently constrains the catalogue more tightly than §2's entries can satisfy
without a schema change:

- `mode` is a closed enum: `["MARKER_VERIFIED", "ROUTE_ONLY_2_ZONE"]` (`survey-catalogue-v1.schema.json:42`). A
  `ROUTE_ONLY_1_ZONE` mode value does not validate today.
- The base `station_steps` array requires `minItems: 2` unconditionally (`survey-catalogue-v1.schema.json:45`),
  before either mode-specific `if/then` branch narrows it further. A 1-station entry (`station_steps` of length 1)
  fails this base constraint regardless of `mode`.
- The `ROUTE_ONLY_2_ZONE` conditional additionally restricts `zone_id` to exactly `{LEFT_ALLEY_MIDDLE,
  RIGHT_ALLEY_MIDDLE}` (`survey-catalogue-v1.schema.json:85-99`) and requires `marker_id: null`; a new
  `ROUTE_ONLY_1_ZONE` conditional would need `station_steps` `minItems`/`maxItems: 1`, `zone_id` open to all six
  zones (`$defs/zone_id`, `survey-catalogue-v1.schema.json:17-20`), and `marker_id: null`, mirrored from the
  existing `ROUTE_ONLY_2_ZONE` branch.

This document does **not** edit `survey-catalogue-v1.schema.json` or the catalogue fixture — both are explicitly
out of scope for the task that produced this proposal (docs only). Implementing §2 therefore requires, as a
prerequisite, a separate schema-amendment document in the same shape as
`docs/superpowers/specs/2026-09-19_wp0-amendment-decision-context-and-reads.md` (additive: relax the base
`minItems`, add the enum member, add the conditional branch — no existing entry's validity changes), consented to
on its own before this catalogue proposal can be implemented.

## 7. Consent statement

This document is a proposal only. It:

- does not modify `docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json`;
- does not modify `docs/superpowers/specs/contracts/survey-catalogue-v1.schema.json` or any other `*.schema.json`;
- does not modify `fixtures/decisions/turns.v1.jsonl`, `fixtures/decisions/README.md`, or any test;
- needs explicit operator Coherence consent before any of §2 (catalogue entries), §3 (renaming), §4 (relabelling),
  or §6 (schema amendment) is implemented, following the same "Proposal only, you consent" pattern the operator
  already applied to this catalogue at gate `G-labels` decision 4 (`fixtures/decisions/README.md` §4).

This document grants no such consent. It records the gap, the proposed entries, the naming question, the fixture
and legal-set impact, the schema prerequisite, and the re-verification list — the decision itself is the
operator's, not this document's.
