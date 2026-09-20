# Draft rich survey catalogue (survey-catalogue-v2.draft.json)

This is a **draft** of 14 coverage-named survey plans for operator review; it is content only and is never loaded by any production code path.

`sha256` fields are placeholders (`"0" * 64`); they are not real digests.

`route_bounds`, `timeout`, and `command_budget` on each entry are copied from the corresponding v1 plan and need the operator's review per plan.

Adopting this draft catalogue is proposal item **P-3** in `docs/superpowers/specs/2026-09-20_wp0-proposal-p3-decision-surface-amendments.md`.

Migration map from the v1 catalogue: `PLAN_ROUTE_ONLY_2_ZONE → PLAN_ROUTE_MIDDLE_PAIR`, `PLAN_MARKER_VERIFIED_6 → PLAN_VERIFIED_FULL_SITE`.
