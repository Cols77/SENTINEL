# P2b interface pin (P3a Task 6, Step 3) — PRE-FILLED

**Filled 2026-09-20 by the parent session** from a direct reading of `feat/p2-shadow-advisors` @ `847d7af`, after sentinel-77 (the P1/P2 session) reported P2b had landed. Every row is verified against the code, not against their summary. A Wave 2 executor does **not** need to redo this.

## Branch-point correction (read this first)

`feat/p3-decision-surface` was **not** cut from `620e8ac`. `git merge-base` says the branch point is **`9f657ac`** — P2b's code commit, which the P2 session had already pushed to `feat/p2-shadow-advisors` when the worktree was created. Consequences:

- **The P2b code was already present throughout Wave 1.** `rule_choice`, `EVIDENCE_TASK_SCORE`, `JUDGMENT_POINTS`, `ADVISORY_POINTS` were all in the branch while Tasks 1–5 ran. Wave 1 never touched those files, so nothing is compromised; the Wave 2 "has P2b landed?" gate is simply already satisfied.
- **The baseline numbers now reconcile, and the handoff was right after all:**

  | Commit | Collected | Passed | Note |
  |---|---|---|---|
  | `620e8ac` | 475 | **472** | what `HANDOFF-P3.md` §4 said — **correct**, not stale |
  | `9f657ac` (branch point) | 495 | **492** | +20 tests from P2b Task A |
  | dry-run at `620e8ac` + Wave 1 code | 505 | **502** | = 472 + 30 new. Reconciles exactly. |

  The earlier claim in this plan that "472 was stale" was wrong, and the note that 502 "does not reconcile" was wrong. Both figures were right for their own commit; the error was assuming the worktree sat on `620e8ac`.
- **Still missing from P3** (3 commits): `4df8770` fixture relabel, `8cbe75b` single-zone proposal (docs), `847d7af` shadow report + plan updates (docs). Only `4df8770` touches code-adjacent state (`fixtures/decisions/turns.v1.jsonl`, its README, and the fixture tests).

## The pin

| # | This plan assumed | Actual at `847d7af` | Verdict |
|---|---|---|---|
| 1 | `LegalSet(decision_point, candidates, rule_choice: str \| None = None)`; `rule_choice` is a **label** | `legal_tools.py:42-49` — exactly that; `rule_choice: str \| None = None` is the third field | ✅ as assumed |
| 2 | `EVIDENCE_TASK_SCORE = 0.75` importable from `sentinel.coordinator.legal_tools` | `legal_tools.py:23` — `EVIDENCE_TASK_SCORE = 0.75`, distinct from `EVIDENCE_SCORE_THRESHOLD = 0.5` (the static veto, still in `command_gateway.py`) | ✅ as assumed |
| 3 | At `EVIDENCE_GATE`: `rule_choice = "create_task"` iff inside the approved plan and score ≥ 0.75, else `"escalate"` | `legal_tools.py:116-120` — `rule_choice = "create_task" if inside_plan and score >= EVIDENCE_TASK_SCORE else ESCALATE_LABEL`; candidates unchanged `(create, escalate)` | ✅ as assumed |
| 4 | At single-candidate points `rule_choice` is that candidate's label, or `None` | **`None`.** Single-candidate sets are built without the kwarg, so it defaults to `None`. `RulePolicy` handles them by the `len(candidates) == 1` branch *before* looking at `rule_choice` | ⚠️ note: `rule_choice is None` alone does **not** mean "needs a human" |
| 5 | `RulePolicy` acts on `rule_choice` when set; else single candidate acts; else escalate | `policies.py` `RulePolicy.decide` — order is: (a) one candidate → act, chooser `RULE`; (b) `rule_choice is not None` → act on that label, chooser `RULE`; (c) else → `escalate`, chooser `OPERATOR` | ✅ as assumed |
| 6 | How do `ShadowLadder` / `pending_decision` / `submit_choice` decide "this turn needs a human"? | **By set membership, not by `rule_choice`.** `operator_choice.py:29` and `:56` both test `legal.decision_point not in JUDGMENT_POINTS`, and `JUDGMENT_POINTS = frozenset({SELECT_PLAN})`. `shadow.py:132` routes on `decision_point in ADVISORY_POINTS` | ❗ **conflicts with this plan — see below** |
| 7 | What is in `ADVISORY_POINTS`, and who reads it | `legal_tools.py:30` — `ADVISORY_POINTS = frozenset({SELECT_PLAN, EVIDENCE_GATE})`. Read by `shadow.py:132` (where advisors run and a `judgment` record is written) and asserted by `policies.py:107` in `JevPolicy.decide`. Shadow `judgment` records gained a `rule_choice` field | ✅ exists; must be extended |
| 8 | Ground truth §12's `turn_generator.py` helpers still exist | All present and unchanged in signature at `847d7af:tests/coordinator/turn_generator.py`: `_run_id_for`, `_Sequencer`, `_submit`, `_state_version`, `_create_run`, `_propose_and_approve_route_plan`, `_perception_payload`, `_ingest_perception`, `_create_task`. `git diff` shows no changed `def` lines | ✅ Wave 1's `p3_helpers.py` is safe |

## The conflict row 6 creates, and the fix

P3a's design makes a **duplicate detection** a judgment point by setting `rule_choice = None` at `EVIDENCE_GATE`. Under P2b that produces a **dead end**:

1. `RulePolicy` falls through to branch (c) → `escalate`, chooser `OPERATOR` → the loop finishes `NEEDS_REVIEW`.
2. The operator then *cannot answer*, because `pending_decision` returns `None` and `submit_choice` raises `INVALID_OPERATOR_CHOICE` — `EVIDENCE_GATE ∉ JUDGMENT_POINTS`.

So the run stalls with no operator path, and Task 11's operator-load report would score it as an unanswerable escalation.

**Fix (make Task 8's conditional step unconditional):** replace the membership test with the dynamic predicate in `legal_tools.py`:

```python
def needs_judgment(legal: LegalSet) -> bool:
    """A turn needs a human iff no rule settled it and there is a real choice.
    Replaces `decision_point in JUDGMENT_POINTS` at the operator surface, so a
    decision point can be rule-settled on one turn and a judgment on the next
    (a duplicate detection at EVIDENCE_GATE)."""
    return legal.rule_choice is None and len(legal.candidates) > 1
```

Then in `operator_choice.py`, both `pending_decision` and `submit_choice` use `needs_judgment(legal)` instead of `legal.decision_point not in JUDGMENT_POINTS`.

**This is backward-compatible with every P2b test**, which is why it does not violate "never edit an existing P1/P2 test":

| Turn | `rule_choice` | candidates | `needs_judgment` | P2b behaviour | preserved? |
|---|---|---|---|---|---|
| `SELECT_PLAN` | `None` | 3 | **True** | operator asked | ✅ same |
| `EVIDENCE_GATE`, ordinary | `"create_task"` / `"escalate"` | 2 | **False** | `INVALID_OPERATOR_CHOICE` | ✅ same |
| `RANK` / `ASSIGN` / waits (P2b) | `None` | 1 | **False** | not a judgment | ✅ same |
| `EVIDENCE_GATE`, duplicate (P3a) | `None` | 2 | **True** | — | ✅ new, and now answerable |

Before relying on this, the Wave 2 executor must confirm that P2b's `test_operator_choice.py` EVIDENCE_GATE case uses a **non-duplicate** detection (it should: it creates one detection and no prior task). If any P2b test constructs a duplicate and asserts `INVALID_OPERATOR_CHOICE`, stop and report — do not edit their test.

## Consequent edits to Tasks 7–9 (apply before dispatching them)

1. **Task 7** — `DETECTION_TRIAGE`/`EVIDENCE_GATE` keeps P2b's `rule_choice` for the ordinary cases; P3a only adds the `None` case for `duplicates_open_task or repeat_of_handled`. Do **not** rewrite P2b's rule; extend it. Export `needs_judgment` from `legal_tools.py` in this task (Task 8 and Task 11 both use it).
2. **Task 8** — the `operator_choice.py` change is now **required, not conditional**. Also add `ASSIGN` and (Task 9) `REPLAN` to `ADVISORY_POINTS`, otherwise `JevPolicy.decide`'s `assert point in ADVISORY_POINTS` (`policies.py:107`) fires the moment an advisor is shadowed at a near-tie, and `INSTRUCTIONS[point]` raises `KeyError`. Both `INSTRUCTIONS["ASSIGN"]` and `INSTRUCTIONS["REPLAN"]` are already specified in the plan.
3. **Task 9** — same: `REPLAN` goes into `ADVISORY_POINTS`, and into `JUDGMENT_POINTS` only if anything still reads that set directly after the `needs_judgment` change.
4. **Do not touch `scripts/shadow_sweep.py`** — it keeps its own local `JUDGMENT_POINTS = (SELECT_PLAN, EVIDENCE_GATE)` at line 45, and it is on the Never-edit list (P2-owned). It will simply not know about the new points until the P2 session updates it; note that in the handoff rather than fixing it.

## Fixture consequences (Task 10)

`4df8770` relabelled `fixtures/decisions/turns.v1.jsonl` per operator decisions, **approved 2026-09-20** per `fixtures/decisions/README.md`:
- `sp-001..006` (single-station requests) are now **`escalate`** — no v1 catalogue plan covers exactly one station.
- `EVIDENCE_GATE` is 6 × `create_task` / 18 × `escalate` under the 0.75 cut.

For the v2 fixture this means the **rich draft catalogue changes the correct label** for single-station requests: with `PLAN_ROUTE_<STATION>` available, a single-station request is no longer `escalate` but the matching per-station plan. Task 10 must state this explicitly in its rationale strings, and the operator must see it at **G-labels-v2**: it is the clearest evidence that the rich catalogue (proposal item P-3) removes a whole class of escalation, and it is exactly the "operator load" argument.

Note also that `8cbe75b` proposes single-zone (`1_ZONE`) catalogue entries. Per the operator's 2026-09-20 decision the catalogue should be the **rich** set (12–15 plans, coverage-named), so `8cbe75b` is an **input to**, not a competitor of, proposal item P-3. Reconcile the two documents at gate **G-amend**; do not implement either without consent.
