# Demo Conversational Outcomes and Survey Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the verified bounded inspection session with durable clarification, grounded room-count/drone-pose answers, legal suggestions, survey report finalization, and demo conversation evaluation.

**Architecture:** Deterministic request analysis and a manifest-bound `DemoContext` add `answer`, `suggestion`, `clarification`, and `report` candidates to the already verified shared registry. A controller finalizer handles those internal `NormalizedDecision` values before public wire parsing, persists append-only response evidence, and never sends read-only outcomes through the mutation gateway. Survey proposals retain approval and report bodies remain controller-generated from current merged results.

**Tech Stack:** Python 3.11, dataclasses, SQLite migrations, JSON Schema, pytest, existing coordinator AgentLoop/gateway, survey store/report functions, pose persistence.

**Hard dependency and execution order:** `docs/superpowers/plans/2026-09-21-bounded-inspection-decision-session.md` must pass its serial inspection trajectory gate. Record that report/manifest digest before Task 1. Then execute Integrated Survey Evaluation Tasks 1–5, this plan's Tasks 1–4, Integrated Survey Evaluation Task 6, this plan's Tasks 5–7, and finally Integrated Survey Evaluation Tasks 7–8. This ordering establishes the independent evaluator/strict replay foundation before changing survey production policy, while making the shared decision response machinery available before survey connection.

---

## File map

- Create `src/sentinel/coordinator/request_intent.py`: deterministic, demo-scoped request classification and explicit constraint gaps.
- Create `src/sentinel/coordinator/demo_context.py`: versioned `DemoContext`, capability registry, room-count and pose read packets, deterministic answer/comparison templates.
- Create `src/sentinel/coordinator/decision_responses.py`: terminal response packets and append-only response/finalization audit helpers.
- Modify `src/sentinel/coordinator/repository.py`, `src/sentinel/coordinator/migrations.py`, and `src/sentinel/coordinator/sql/schema.sql`: durable conversation/request/clarification linkage.
- Modify `src/sentinel/coordinator/agent_loop.py` and `src/sentinel/coordinator/driver.py`: finalize read-only decisions and return idempotent drive dispositions while workflow run status stays authoritative.
- Modify `src/sentinel/survey/store.py`, `src/sentinel/survey/sql/schema.sql`, and `src/sentinel/survey/loop.py`: pinned option registry and typed authoritative results reads used by demo context.
- Modify `src/sentinel/proposal_eval.py` and `scripts/proposal_sweep.py`: response/clarification/suggestion trajectory scoring for demo questions.
- Add focused tests under `tests/coordinator`, `tests/integration`, `tests/survey`, and `tests/pose` as named below.

### Task 1: Persist conversation and one-round clarification linkage

**Files:**
- Modify: `src/sentinel/coordinator/sql/schema.sql`
- Modify: `src/sentinel/coordinator/migrations.py`
- Modify: `src/sentinel/coordinator/repository.py`
- Create: `tests/coordinator/test_decision_conversations.py`

- [ ] **Step 1: Add failing fresh-schema, upgrade, append-only, and resume tests**

Assert a new database and a version-3 database both reach the new schema version and contain:

```sql
CREATE TABLE decision_requests (
    conversation_id TEXT NOT NULL,
    request_revision INTEGER NOT NULL CHECK (request_revision >= 1),
    root_request_id TEXT NOT NULL,
    agent_run_id TEXT NOT NULL,
    parent_agent_run_id TEXT,
    request_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, request_revision),
    UNIQUE (agent_run_id),
    UNIQUE (conversation_id, request_revision, root_request_id, agent_run_id)
);

CREATE TABLE decision_clarifications (
    clarification_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    root_request_id TEXT NOT NULL,
    request_revision INTEGER NOT NULL,
    agent_run_id TEXT NOT NULL,
    question_candidate_id TEXT NOT NULL,
    decision_packet_sha256 TEXT NOT NULL,
    clarification_round INTEGER NOT NULL CHECK (clarification_round = 1),
    created_at TEXT NOT NULL,
    UNIQUE (clarification_id, conversation_id, request_revision,
            root_request_id, agent_run_id),
    FOREIGN KEY (conversation_id, request_revision, root_request_id, agent_run_id)
        REFERENCES decision_requests(
            conversation_id, request_revision, root_request_id, agent_run_id)
);

CREATE TABLE decision_clarification_responses (
    clarification_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    root_request_id TEXT NOT NULL,
    expected_request_revision INTEGER NOT NULL,
    response_revision INTEGER NOT NULL,
    parent_agent_run_id TEXT NOT NULL,
    agent_run_id TEXT NOT NULL,
    response_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (clarification_id, conversation_id, expected_request_revision,
                 root_request_id, parent_agent_run_id)
        REFERENCES decision_clarifications(
            clarification_id, conversation_id, request_revision,
            root_request_id, agent_run_id),
    FOREIGN KEY (conversation_id, response_revision, root_request_id, agent_run_id)
        REFERENCES decision_requests(
            conversation_id, request_revision, root_request_id, agent_run_id)
);

CREATE TABLE decision_response_records (
    response_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    request_revision INTEGER NOT NULL,
    root_request_id TEXT NOT NULL,
    agent_run_id TEXT NOT NULL,
    decision_kind TEXT NOT NULL CHECK (
        decision_kind IN ('answer','suggestion','clarification')),
    candidate_id TEXT NOT NULL,
    clarification_id TEXT,
    decision_packet_sha256 TEXT NOT NULL,
    context_sha256 TEXT NOT NULL,
    response_sha256 TEXT NOT NULL,
    response_json TEXT NOT NULL,
    audit_record_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, request_revision),
    FOREIGN KEY (conversation_id, request_revision, root_request_id, agent_run_id)
        REFERENCES decision_requests(
            conversation_id, request_revision, root_request_id, agent_run_id),
    FOREIGN KEY (clarification_id, conversation_id, request_revision,
                 root_request_id, agent_run_id)
        REFERENCES decision_clarifications(
            clarification_id, conversation_id, request_revision,
            root_request_id, agent_run_id)
);
```

All four tables are append-only. A clarification remains an immutable awaiting record; the presence of its unique clarification-response row marks consumption. The response-record uniqueness key makes answer/suggestion/clarification finalization idempotent on drive re-entry. `audit_record_id` is a deterministic hash of the response identity and makes the database row the authoritative audit source. Assert clarification finalization atomically inserts both its `decision_clarifications` and `decision_response_records` rows or neither. Assert resume requires the expected current revision, atomically inserts exactly one clarification-response plus request revision `n+1`, links `parent_agent_run_id`, and refuses concurrent/repeated response or a second clarification round.

- [ ] **Step 2: Run the migration tests and verify failure**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_decision_conversations.py tests/coordinator/test_repository.py
```

- [ ] **Step 3: Implement migration version 4 and repository methods**

Increment `CURRENT_VERSION` to 4. For version 3 execute only the new DDL/triggers; for older versions retain the existing upgrade paths and finish with the new DDL. Add repository methods `start_decision_request(conversation_id, root_request_id, agent_run_id, request_text, parent_agent_run_id=None)`, `get_decision_request_by_agent_run_id(agent_run_id)`, `get_decision_response(conversation_id, request_revision)`, `get_decision_conversation_context(agent_run_id)`, `finalize_decision_response(response_record, clarification_record=None)`, and `resume_decision_request(clarification_id, conversation_id, expected_request_revision, agent_run_id, response_text)`. Each returns immutable rows as plain dictionaries. `finalize_decision_response` uses one SQLite transaction: for clarification it inserts the awaiting clarification first and its response record second; any conflict or injected interruption rolls back both. Answers and suggestions insert only their response record through the same method. Repository code checks that all response/clarification identity fields match the referenced request before insert; the composite foreign keys enforce the same relation at the database boundary.

Use transactions and compare-and-set queries so concurrent or stale finalization/resume fails without partial writes. Store the operator response as the new request revision while retaining the root request linkage. `get_decision_conversation_context` joins the root request, awaiting clarification response packet/question, operator response, parent run, and current request revision into a typed context used to rebuild the resumed decision packet after process restart. Add fault-injection tests immediately before and after each clarification/response insert boundary and prove rollback leaves neither row. Add a fresh-repository/fresh-selector test where a bare affirmative response to the persisted sweep clarification produces the intended legal sweep exactly once and never repeats kind `clarification`.

- [ ] **Step 4: Run persistence tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_decision_conversations.py tests/coordinator/test_repository.py tests/coordinator/test_idempotency.py
```

Expected: all pass.

- [ ] **Step 5: Commit durable clarification linkage**

```powershell
rtk git add src/sentinel/coordinator/sql/schema.sql src/sentinel/coordinator/migrations.py src/sentinel/coordinator/repository.py tests/coordinator/test_decision_conversations.py
rtk git commit -m "feat: persist bounded decision conversations"
```


### Task 2: Add manifest-bound demo context and authoritative reads

**Files:**
- Create: `src/sentinel/coordinator/demo_context.py`
- Create: `src/sentinel/coordinator/decision_responses.py`
- Create: `tests/coordinator/test_demo_context.py`
- Modify: `src/sentinel/survey/sql/schema.sql`
- Modify: `src/sentinel/survey/store.py`
- Modify: `src/sentinel/survey/loop.py`
- Modify: `tests/survey/test_store.py`
- Modify: `tests/survey/test_loop.py`
- Modify: `tests/pose/test_persistence.py`

- [ ] **Step 1: Add failing `DemoContext` and option-registry tests**

Pin this public shape:

```python
@dataclass(frozen=True)
class DemoContext:
    schema_version: int
    room_id: str
    survey_run_id: str
    pose_run_id: str | None
    option_registry_revision: str
    option_registry_sha256: str
    pose_stale_after_ms: int

    def canonical_sha256(self) -> str:
        payload = asdict(self)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()
```

Assert the hash changes for either run ID or threshold. Assert survey-run creation persists registry revision/hash before the first sweep; a missing hash, mismatched hash, or flown option absent from the pinned registry rejects the count read.

- [ ] **Step 2: Add failing count-semantics tests**

Cover aliases `computer/laptop`, `human/person/people`, and `phone/mobile phone`. Assert computer/phone intervals are `[confirmed, confirmed + uncertain]`, people are `[maximum_simultaneously_visible, None]`, `NOT_OBSERVABLE` is preserved, and zero with incomplete/unanalysed data is never rendered as “none in the room.” Assert every answer says “observed”, includes `latest_capture_at`, flown range, and unanalysed-frame qualification.

- [ ] **Step 3: Add failing comparison and pose tests**

Assert finite non-overlapping computer/phone intervals compare deterministically. Assert people can be proven greater only when their lower bound exceeds the other class’s finite maximum; the reverse is indeterminate. Assert pose reads use only `pose_run_id`, return source/method/quality/snapshot/staleness, distinguish stale last-known pose from current location, and never substitute `survey_run_id`. Assert battery, airborne, speed, temperature, waypoint, and mission progress return capability-registry unavailability.

- [ ] **Step 4: Run focused tests and verify missing module/metadata**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_demo_context.py tests/survey/test_store.py tests/survey/test_loop.py tests/pose/test_persistence.py
```

- [ ] **Step 5: Persist the survey option registry**

Add nullable `option_registry_revision` and `option_registry_sha256` columns to the SQL schema so existing databases can migrate without fabricated values. `SurveyStore.open` checks `PRAGMA table_info` and adds absent nullable columns transactionally. Change `create_run` so every new run requires and writes both nonblank values; legacy rows remain null and the new count read rejects them as `OPTION_REGISTRY_UNBOUND`. In `run_survey`, canonicalize the exact public option definitions before any sweep and persist their SHA-256.

- [ ] **Step 6: Implement authoritative read packets**

Create the base response type in `decision_responses.py` before implementing the renderers:

```python
@dataclass(frozen=True)
class ResponsePacket:
    kind: Literal["answer", "suggestion", "clarification"]
    text: str
    template_revision: str
    source_refs: Sequence[dict[str, Any]]
    unmet_constraints: Sequence[str] = ()
    clarification_target: Mapping[str, Any] | None = None
    terminal_disposition: str = "COMPLETED"
```

Implement five public functions: `read_room_counts(store, context, options)`, `read_latest_drone_pose(connection, context, now=None)`, `render_count_answer(packet, class_name)`, `render_pose_answer(packet, property_name)`, and `compare_count_intervals(left, right)`. The two reads return serializable dictionaries, the renderers return the base `ResponsePacket`, and comparison returns one of `left_greater`, `right_greater`, `equal`, or `indeterminate`.

`read_room_counts` must call `survey.report.class_counts`, derive the latest data digest only from observation-producing state (run and option-registry identity, sweeps, frames, analysis status, sightings, merged objects, and class rows), and return registry identity, capture time, analysed/unanalysed counts, flights, flown options/ranges, and class rows. Exclude report rows/finalization state so persisting a report cannot change results identity. `read_latest_drone_pose` calls the existing pose persistence `latest_pose` with the manifest-bound threshold and returns unavailable when `pose_run_id` is absent.

- [ ] **Step 7: Run demo read tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_demo_context.py tests/survey/test_store.py tests/survey/test_loop.py tests/survey/test_report.py tests/pose/test_persistence.py
```

Expected: all pass.

- [ ] **Step 8: Commit authoritative demo reads**

```powershell
rtk git add src/sentinel/coordinator/demo_context.py src/sentinel/coordinator/decision_responses.py src/sentinel/survey/sql/schema.sql src/sentinel/survey/store.py src/sentinel/survey/loop.py tests/coordinator/test_demo_context.py tests/survey/test_store.py tests/survey/test_loop.py tests/pose/test_persistence.py
rtk git commit -m "feat: add manifest-bound room and drone reads"
```


### Task 3: Build active candidates and deterministic terminal responses

**Files:**
- Modify: `src/sentinel/coordinator/decision_responses.py`
- Create: `src/sentinel/coordinator/request_intent.py`
- Create: `tests/coordinator/test_decision_candidates.py`
- Create: `tests/coordinator/test_request_intent.py`
- Modify: `src/sentinel/coordinator/decision_session.py`
- Modify: `src/sentinel/decision_prompt.py`
- Modify: `tests/coordinator/test_bounded_proposers.py`

- [ ] **Step 1: Add failing candidate-building tests**

For each request class, assert the exact active kinds:

```text
current supported fact       -> answer, escalation
declared resolvable gap      -> read, escalation
ambiguous supported target   -> clarification, escalation
unsupported action + legal partial plans -> suggestion, escalation
unsupported informational field -> answer(unavailable), escalation
irreconcilable constraints   -> escalation only
explicit supported action    -> proposal legal candidates, escalation
question with absent count   -> suggestion sweep, escalation; no proposal
positive sweep clarification -> proposal legal sweep candidates, escalation
```

Assert inspection state reads are suppressed when the initial packet is already current and complete. Assert a resumed clarification has no `clarification` candidate. Rebuild resumed intent from the durable conversation context, including root request, controller-authored clarification question/target, and operator response; do not parse a bare “yes” without that context.

- [ ] **Step 2: Add failing deterministic intent-analysis tests**

Pin a deliberately narrow parser whose output, rather than model confidence, authorizes response candidates:

```python
@dataclass(frozen=True)
class RequestIntent:
    mode: Literal["question", "action", "ambiguous"]
    subject: Literal["room_count", "drone_pose", "inspection", "unknown"]
    requested_classes: Sequence[Literal["computer", "person", "phone"]]
    requested_pose_fields: Sequence[Literal["position", "heading", "observed_at", "quality"]]
    unsupported_fields: Sequence[str]
    explicit_action: bool
    constraints: Sequence[str]
    ambiguity_code: str | None
```

Use bounded aliases and explicit action verbs from the approved demo scope. Cover singular/plural aliases, simple count comparisons, “drone state” ambiguity, named unsupported telemetry, explicit “survey/count the room” action intent, and ordinary inspection requests. Unknown phrasing returns `subject="unknown"` and offers escalation only. The parser never extracts executable identifiers or silently weakens a constraint.

- [ ] **Step 3: Run tests and verify missing candidate builder/parser**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_decision_candidates.py tests/coordinator/test_request_intent.py
```

- [ ] **Step 4: Implement the deterministic request parser**

Normalize Unicode, case, and whitespace; match only the approved alias tables and fixed pose/capability lexicon. Return a material ambiguity only when exactly one supported clarification can resolve the request. Build a non-mutating `suggestion` for an unanswered supported informational question, or for a partially satisfiable action, only when current legal alternatives exist and every unmet parsed constraint can be enumerated. Require explicit action intent, including an affirmative answer interpreted through a persisted action clarification, only for a mutating `proposal`. Otherwise build only `escalation`. This conservative fallback keeps authority in code and avoids treating confidence as evidence.

- [ ] **Step 5: Implement stable ID construction and response templates**

Use namespaced hashes:

```python
def candidate_id(kind: DecisionKind, tool_name: str | None,
                 identity: Mapping[str, Any]) -> str:
    digest = sha256(canonical_json(identity).encode("utf-8")).hexdigest()[:16]
    name = tool_name or "internal"
    return f"{kind}:{name}:{digest}"
```

Populate the base `ResponsePacket` created in Task 2 entirely in controller code. Suggestions list only current legal alternatives and every unmet original constraint. Clarifications ask one bounded question and persist a structured clarification target needed for fresh-process interpretation of a bounded affirmative response. Unsupported info answers name the missing persisted capability.

- [ ] **Step 6: Extend the one shared instruction revision**

Keep `sentinel.decision_prompt.BOUNDED_DECISION_INSTRUCTION_V1` from the prerequisite plan as the only prompt authority. Extend its content under a new revision in that neutral module with the demo answer, suggestion, and one-round clarification clauses, then make both adapters import the same revised symbol. Do not define a second constant in `decision_session.py`. Tests assert semantic clauses and that both adapters receive the same revision and text. Integrated Survey Evaluation Task 6 later adds the separate survey action/report clauses under its next revision.

- [ ] **Step 7: Run candidate tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_decision_candidates.py tests/coordinator/test_request_intent.py tests/coordinator/test_decision_session.py tests/coordinator/test_demo_context.py
```

Expected: all pass.

- [ ] **Step 8: Commit candidate and response construction**

```powershell
rtk git add src/sentinel/coordinator/decision_session.py src/sentinel/coordinator/decision_responses.py src/sentinel/coordinator/request_intent.py src/sentinel/decision_prompt.py tests/coordinator/test_decision_candidates.py tests/coordinator/test_request_intent.py tests/coordinator/test_bounded_proposers.py
rtk git commit -m "feat: build bounded decision candidates and responses"
```


### Task 4: Persist terminal responses and driver outcomes

**Files:**
- Modify: `src/sentinel/coordinator/decision_responses.py`
- Modify: `src/sentinel/coordinator/driver.py`
- Modify: `src/sentinel/coordinator/agent_loop.py`
- Create: `tests/coordinator/test_decision_responses.py`
- Modify: `tests/test_runner.py`
- Modify: `tests/coordinator/test_bounded_agent_loop.py`

- [ ] **Step 1: Add failing atomic audit and non-mutation tests**

Assert accepted answer/suggestion/clarification persists an authoritative `decision_response_records` row with deterministic `audit_record_id`, candidate/packet/context hashes, template revision, sources/versions, observation time or freshness, unmet constraints, and terminal disposition. Assert clarification atomically appends its durable awaiting row in the same transaction. Inject failure at both insert boundaries and prove neither row survives. Assert no gateway command is sent and no internal response name is parsed as `AgentToolCall`.

- [ ] **Step 2: Add failing zero-call re-entry tests**

Persist each response disposition, restart with a fresh driver/provider factory, and assert re-entry by `agent_run_id` returns the identical response and authoritative workflow status before provider initialization. Use a provider fake that raises if constructed.

- [ ] **Step 3: Run tests and verify the current driver lacks preflight replay**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_decision_responses.py tests/coordinator/test_bounded_agent_loop.py tests/test_runner.py
```

- [ ] **Step 4: Implement terminal response auditing**

Inject `finalize_decision(decision_resolution) -> DecisionFinalization` into `AgentLoop` as the `decision_finalizer` planned by the inspection core. It accepts only the resolver's current controller-built packet/candidate, calls the repository's atomic `finalize_decision_response`, writes or reuses the immutable `decision_response_records` row, and returns the typed fields `disposition`, `response_id`, and `response`. Allowed response dispositions are `ANSWERED`, `SUGGESTED`, and `AWAITING_RESPONSE`; `AgentLoop` returns the object immediately. It never accepts provider-authored response text.

Before constructing or calling `AgentLoop`, update `drive_run` to resolve `decision_requests` by `agent_run_id` and look up `decision_response_records` by its conversation/revision. If a row exists, reconstruct `DecisionFinalization` and return it immediately as `{"status": finalization.disposition, "run_status": repository.get_run(run_id).status, "response_id": finalization.response_id, "response": finalization.response}`. Test this preflight with a provider fake that raises if initialized or invoked. For a new `DecisionFinalization` returned by `AgentLoop`, query the authoritative workflow status after finalization and return the same shape. The finalizer never changes workflow lifecycle state; it remains `RUNNING` unless an existing lifecycle operation changed it. Only `NEEDS_REVIEW` with a real repository `pending_decision` enters the operator-choice path; clarification, answer, suggestion, and unavailable informational answer never synthesize an operator judgment.

Treat the immutable database response row as the authoritative audit record. Add an idempotent audit projection that renders `decision_response` records from those rows using `audit_record_id`; write an evaluation raw stream through a temporary file and atomic replace, and deduplicate any best-effort runtime sink replay by the same ID. A sink failure cannot erase the database evidence, and retry cannot create a second logical audit record. Tests interrupt projection before write, after write, and before replace, rerun it, and assert one logical record per ID and byte-stable output.

- [ ] **Step 5: Run response and driver tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_decision_responses.py tests/coordinator/test_bounded_agent_loop.py tests/test_runner.py
```

Expected: all pass.

- [ ] **Step 6: Commit terminal response handling**

```powershell
rtk git add src/sentinel/coordinator/decision_responses.py src/sentinel/coordinator/agent_loop.py src/sentinel/coordinator/driver.py tests/coordinator/test_decision_responses.py tests/coordinator/test_bounded_agent_loop.py tests/test_runner.py
rtk git commit -m "feat: finalize grounded decision responses"
```


### Task 5: Add end-to-end demo conversations

**Dependency:** Integrated Survey Evaluation Task 6 must have connected the bounded survey candidates, selectors, report finalizer, approval checks, and flight-budget checks before this task runs.

**Files:**
- Create: `tests/integration/test_bounded_demo_questions.py`
- Modify: `tests/coordinator/test_proposer_flow.py`
- Modify: `tests/survey/test_loop.py`

- [ ] **Step 1: Add identical Jev/LLM conversation fixtures**

Run both selectors against the same persisted room counts and pose data. Cover computer/laptop, phone/mobile phone, people/human lower bound, deterministic/indeterminate comparisons, fresh pose, stale pose, no pose, supported broad-drone-state clarification, and unsupported battery/airborne answers.

- [ ] **Step 2: Add action-intent and correction flows**

Cover: no survey result -> suggest sweep; explicit “prepare a survey” -> propose pending approval; rejected stale read -> refreshed legal read -> grounded answer; rejected proposal -> different legal proposal; unsupported computer-count action in the inspection catalogue -> escalation; approval wait -> zero additional calls.

- [ ] **Step 3: Run end-to-end tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/integration/test_bounded_demo_questions.py tests/coordinator/test_proposer_flow.py tests/survey/test_loop.py
```

Expected: all pass with at most three provider calls and two accepted reads per session.

- [ ] **Step 4: Commit vertical slices**

```powershell
rtk git add tests/integration/test_bounded_demo_questions.py tests/coordinator/test_proposer_flow.py tests/survey/test_loop.py
rtk git commit -m "test: cover bounded demo conversations end to end"
```


### Task 6: Evaluate demo response trajectories

**Files:**
- Modify: `src/sentinel/proposal_eval.py`
- Modify: `scripts/proposal_sweep.py`
- Modify: `tests/test_proposal_eval.py`
- Modify: `tests/test_proposal_sweep.py`

- [ ] **Step 1: Add failing response-scorer cases**

Prove these cannot score as success: stale or mismatched source version, incorrect count interval, treating a people lower bound as exact, unnecessary clarification, a suggestion counted as request fulfillment, undisclosed unmet constraint, absent `decision_response`, stale report finalization, or a fourth provider call. Add positive exact-fixture cases for grounded count/pose answers, necessary one-round clarification, legal suggestion, and current deterministic report.

- [ ] **Step 2: Add response trajectory metrics**

Report answer factual correctness, source-version grounding, historical-observation wording, stale-pose qualification, clarification necessity, suggestion legality and unmet-constraint disclosure, report finalization validity, provider calls, reads, corrections, first-pass/recovered outcome, and mutation/approval safety with explicit denominators. No report keeps report validity not applicable; suggestion and clarification never count as fulfilling the original request.

- [ ] **Step 3: Extend manifest and recording identity**

Bind demo-context hash, option-registry hash, pose-staleness threshold, request-intent revision, response-template revisions, and normalized candidate-request digests. Recorded mode rejects request mismatch, exhaustion, or unused required responses; scripted/recorded evidence remains labelled harness/reproducibility evidence.

- [ ] **Step 4: Run evaluator tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_proposal_eval.py tests/test_proposal_sweep.py tests/integration/test_bounded_demo_questions.py
```

Expected: all pass.

- [ ] **Step 5: Commit demo trajectory evaluation**

```powershell
rtk git add src/sentinel/proposal_eval.py scripts/proposal_sweep.py tests/test_proposal_eval.py tests/test_proposal_sweep.py tests/integration/test_bounded_demo_questions.py
rtk git commit -m "feat: evaluate grounded demo conversations"
```

### Task 7: Serial verification and iterative independent review

**Files:**
- Update after evidence exists: `docs/reports/inspection-capability-baseline.md`
- Coordinate with: `docs/superpowers/plans/2026-09-21-integrated-survey-evaluation.md`

- [ ] **Step 1: Run all focused bounded-protocol tests serially**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/coordinator/test_decision_conversations.py tests/coordinator/test_demo_context.py tests/coordinator/test_decision_candidates.py tests/coordinator/test_decision_responses.py tests/coordinator/test_bounded_agent_loop.py tests/integration/test_bounded_demo_questions.py tests/survey/test_store.py tests/survey/test_loop.py tests/survey/test_proposer.py tests/survey/test_report.py tests/pose/test_persistence.py tests/test_proposal_eval.py tests/test_proposal_sweep.py
```

Expected: all pass.

- [ ] **Step 2: Run the entire serial suite**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short
```

Expected: exit code 0; retain counts, skips, warnings, duration, and exact revision.

- [ ] **Step 3: Dispatch a specification reviewer**

Ask a fresh reviewer to map every approved Increment 2 acceptance criterion to code and a meaningful test, with special attention to run identity, people bounds, low-confidence call-three behavior, clarification resume, report authority, approval waits, and no F masking. Fix each confirmed important gap and rerun focused tests.

- [ ] **Step 4: Dispatch a separate quality reviewer**

Ask a fresh reviewer to inspect state/version races, idempotency, schema compatibility, audit completeness, secret leakage, transcript bounds, migration behavior, and scorer gaming. Fix every confirmed important issue in a new commit and rerun focused plus full tests.

- [ ] **Step 5: Repeat reviews until clear**

After fixes, send the updated diff and test evidence to fresh review/fix agents. Stop only when neither review reports an important unresolved issue. Record minor deferred observations with a reason; never classify a correctness or safety gap as minor.

- [ ] **Step 6: Publish bounded inspection/demo evidence**

Update the inspection report with one-shot versus bounded scripted/recorded results, explicit limitations, and links to retained artifacts. Do not claim live bounded quality until a separately authorized live manifest is executed. The integrated survey baseline belongs to the next plan.
