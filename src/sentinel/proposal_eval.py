"""Outcome inspection over the real coordinator; synthetic fixtures are wiring evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import hashlib
import re
from pathlib import Path
import uuid

from sentinel.contracts import AgentModelResult
from sentinel.evaluation_run import sanitized
from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.architecture import ArchitectureRouter
from sentinel.coordinator.boundary import CoordinatorBoundary
from sentinel.coordinator.decision_state import read_decision_state
from sentinel.coordinator.legal_tools import load_catalogue_plans, SELECT_PLAN
from sentinel.coordinator.policies import RulePolicy
from sentinel.coordinator.policy import bind_context, one_call
from sentinel.coordinator.proposer_factory import build_proposer

ROUTE = "PLAN_ROUTE_ONLY_2_ZONE"
FULL = "PLAN_MARKER_VERIFIED_6"
DEFAULT_CASES = Path(__file__).resolve().parents[2] / "fixtures/proposal_eval/cases.json"
DEFAULT_CATALOGUE = Path(__file__).resolve().parents[2] / "docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json"


@dataclass(frozen=True)
class Case:
    case_id: str
    request: str
    acceptable_plans: tuple[str, ...] = ()
    expected_escalation: bool = False
    setup: str = "new"
    expected_terminal: str = "COMPLETED"
    expected_effects: tuple[str, ...] = ("plan", "task", "assignment")
    turn_limit: int = 24
    operator_decisions: tuple[str, ...] = ("CONFIRM", "CONFIRM")
    scripted_choices: tuple[str, ...] = ()
    provenance: str = "synthetic"
    label_status: str = "synthetic_declared"

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        for key in ("acceptable_plans", "expected_effects", "operator_decisions", "scripted_choices"):
            if key in value:
                if not isinstance(value[key], (list, tuple)) or not all(isinstance(item, str) for item in value[key]):
                    raise ValueError(f"{key} must be a sequence of strings")
                value[key] = tuple(value[key])
        try:
            case = cls(**value)
        except TypeError as exc:
            raise ValueError("invalid case fields") from exc
        if not isinstance(case.case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", case.case_id) or type(case.turn_limit) is not int or not 1 <= case.turn_limit <= 100:
            raise ValueError("case requires an ID and a bounded turn limit")
        if not isinstance(case.request, str) or not case.request.strip() or type(case.expected_escalation) is not bool:
            raise ValueError("request must be nonblank and escalation boolean")
        if any(not isinstance(getattr(case, key), str) for key in ("setup", "expected_terminal", "provenance", "label_status")):
            raise ValueError("setup, terminal, provenance and label status must be strings")
        if case.setup not in {"new", "pending_approval", "missing_evidence", "duplicate_recovery", "stale_evidence_retry", "audit_interruption"}:
            raise ValueError("unknown case setup")
        if case.expected_terminal not in {"NEW", "RUNNING", "WAITING_FOR_APPROVAL", "WAITING_FOR_OBSERVATION", "NEEDS_REVIEW", "COMPLETED", "FAILED", "EMERGENCY"}:
            raise ValueError("unknown expected terminal state")
        if set(case.expected_effects) - {"plan", "task", "assignment"} or set(case.operator_decisions) - {"CONFIRM", "REJECT"} or set(case.acceptable_plans) - {ROUTE, FULL}:
            raise ValueError("unknown effect, operator decision or acceptable plan")
        if not case.acceptable_plans and not case.expected_escalation:
            raise ValueError("case must declare acceptable plans or expected escalation")
        return case


def load_cases(path=None):
    return load_case_bundle(path)[0]


def load_case_bundle(path=None):
    """Return cases and explicit exclusions; preserve existing hand labels exactly."""
    source = Path(path or DEFAULT_CASES)
    text = source.read_text(encoding="utf-8")
    document = [json.loads(line) for line in text.splitlines() if line.strip()] if source.suffix == ".jsonl" else json.loads(text)
    if isinstance(document, dict) and document.get("version") != "proposal-outcomes.v1":
        raise ValueError("unsupported proposal fixture version")
    rows = document.get("cases", []) if isinstance(document, dict) else document
    cases, excluded = [], []
    approved_labels = {plan.plan_id for plan in load_catalogue_plans()} | {"escalate"}
    for row in rows:
        reason = None
        if "turn_id" in row:
            if row.get("decision_point") != SELECT_PLAN:
                reason = "outside_SELECT_PLAN_scope"
            elif not isinstance(row.get("candidates"), list) or any(not isinstance(candidate, dict) or not isinstance(candidate.get("label"), str) for candidate in row["candidates"]) or {candidate["label"] for candidate in row["candidates"]} != approved_labels:
                reason = "incompatible_or_draft_catalogue"
            elif row.get("label_source") != "hand" or row.get("label") not in {ROUTE, FULL, "escalate"}:
                reason = "draft_or_unlabelled_reference"
            elif row.get("packet", {}).get("facts", {}).get("plan_exists") or row.get("packet", {}).get("facts", {}).get("detection_present") or row.get("packet", {}).get("facts", {}).get("task_exists"):
                reason = "reference_state_not_equivalent_to_new_run"
            else:
                label = row["label"]
                row = {"case_id": row["turn_id"], "request": row["packet"]["operator_request"],
                       "acceptable_plans": [] if label == "escalate" else [label],
                       "expected_escalation": label == "escalate", "expected_terminal": "RUNNING" if label == "escalate" else "COMPLETED",
                       "expected_effects": [] if label == "escalate" else ["plan", "task", "assignment"],
                       "provenance": "request_adaptation_of_existing_hand_label:" + str(source), "label_status": "independently_labelled"}
        elif row.get("label_status", "synthetic_declared") not in {"synthetic_declared", "independently_labelled"}:
            reason = "draft_or_unlabelled_reference"
        if reason:
            excluded.append({"case_id": row.get("case_id", row.get("turn_id")), "reason": reason})
        else:
            cases.append(Case.from_dict(row))
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("duplicate case IDs")
    return cases, excluded


class ScriptedTransport:
    """Deterministic provider-shaped responses, never a model-quality measurement."""
    def __init__(self, case):
        self.case = case
        self.calls = []

    def _choice(self):
        choices = self.case.scripted_choices or self.case.acceptable_plans or ("escalate",)
        return choices[min(len(self.calls) - 1, len(choices) - 1)]

    def propose(self, request, context):
        self.calls.append({"request": request.to_dict(), "context": context.state.to_packet()})
        choice = self._choice()
        if choice == "provider_error":
            raise RuntimeError("synthetic provider failure")
        if choice == "malformed":
            return {"status": "TOOL_CALLS"}
        if choice == "escalate":
            return AgentModelResult.from_dict(one_call(request.to_dict(), "finish", {"status": "NEEDS_REVIEW", "summary": "request needs clarification"}))
        return AgentModelResult.from_dict(one_call(request.to_dict(), "inspection.propose_plan", {"catalogue_plan_id": choice, "reason": "Synthetic fixture response; factual support is unassessed."}))

    def choice(self, state, instruction, *, criteria, name):
        self.calls.append({"state": state, "instruction": instruction, "criteria": criteria, "name": name})
        choice = self._choice()
        if choice == "provider_error":
            raise RuntimeError("synthetic provider failure")
        if choice == "malformed":
            return {"value": []}
        return {"value": choice, "confidence": .99, "probabilities": {label: .99 if label == choice else .01 for label in criteria}}


def make_scripted_transport(arm, case):
    return ScriptedTransport(case)


class _Sink:
    def __init__(self, path):
        self.path, self.records = path, []

    def write(self, record):
        record = sanitized(json.loads(json.dumps(record, default=str)))
        self.records.append(record)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")


class _RecoveringSink:
    """Retain failed audit writes separately; retry once, then fail the case."""
    def __init__(self, primary, directory):
        self.primary = primary
        self.fallback = _Sink(directory / "recovery.jsonl")
        self.records = []
        self.recoveries = 0

    def write(self, record):
        record = sanitized(json.loads(json.dumps(record, default=str)))
        self.records.append(record)
        try:
            self.primary.write(record)
        except OSError as exc:
            failure = {"record": "audit_write_failure", "error_type": type(exc).__name__, "retained_record": record}
            self.records.append(failure)
            self.fallback.write(failure)
            try:
                self.primary.write(record)
            except OSError:
                permanent = {"record": "audit_write_permanent_failure", "retained_record_type": record.get("record")}
                # ArchitectureRouter may catch the raised exception and recover
                # through F. The audit failure remains independently fatal to
                # evaluation even if coordinator execution later completes.
                self.records.append(permanent)
                self.fallback.write(permanent)
                raise
            self.recoveries += 1
            recovery = {"record": "audit_write_recovery", "retained_record_type": record.get("record")}
            self.records.append(recovery)
            self.fallback.write(recovery)


def run_case(case, arm, output_dir, proposer_factory=None, sink_factory=None):
    """Fresh SQLite run; all commands and decisions are retained before scoring."""
    from tests.coordinator.harness import open_harness, envelope, FakeWorker
    from sentinel.adapter.llm.config import LLMConfig

    case = Case.from_dict(case if isinstance(case, dict) else asdict(case))
    arm = arm.lower()
    if arm not in {"f", "llm", "jev"}:
        raise ValueError("arm must be F, llm, or jev")
    directory = Path(output_dir) / case.case_id / arm
    directory.mkdir(parents=True, exist_ok=False)
    class InterruptedSink(_Sink):
        failed = False
        def write(self, record):
            if not self.failed and record.get("record") == "tool_attempt" and record["command"]["command_type"] == "OPERATOR_DECIDE":
                self.failed = True
                raise OSError("synthetic audit write interruption")
            super().write(record)
    primary_factory = sink_factory or (InterruptedSink if case.setup == "audit_interruption" else _Sink)
    sink_init_error = None
    try:
        primary = primary_factory(directory / "raw.jsonl")
    except Exception as exc:
        primary, sink_init_error = _Sink(directory / "raw.jsonl"), exc
    sink = _RecoveringSink(primary, directory)
    repo, gateway = open_harness(directory)
    run_id, agent_id = str(uuid.uuid4()), str(uuid.uuid4())
    catalogue = load_catalogue_plans()
    commands, requests, results, loops = [], [], [], []
    original_submit = gateway.submit

    def submit(command):
        attempt = {"record": "tool_attempt", "command": command, "before": repo.read_state(run_id).to_dict() if command["command_type"] != "RUN_CREATE" else None}
        try:
            outcome = original_submit(command)
            attempt.update(accepted=True, outcome=outcome)
            return outcome
        except Exception as exc:
            attempt.update(accepted=False, error_type=type(exc).__name__, error_code=getattr(exc, "code", None))
            raise
        finally:
            commands.append(attempt)
            recoveries_before = sink.recoveries
            sink.write(attempt)
            if attempt.get("accepted") and sink.recoveries > recoveries_before:
                replay = {"record": "tool_attempt", "command": command, "before": repo.read_state(run_id).to_dict(), "accepted": True,
                          "outcome": original_submit(command), "recovery": "audit_write_retry_same_command"}
                commands.append(replay)
                sink.write(replay)

    gateway.submit = submit
    operator_choices = iter(case.scripted_choices or case.acceptable_plans)

    class OperatorRules:
        def decide(self, request, context):
            if context.legal.decision_point == SELECT_PLAN:
                if case.expected_escalation:
                    return one_call(request, "finish", {"status": "NEEDS_REVIEW", "summary": "operator requests clarification"})
                choice = next(operator_choices, case.acceptable_plans[-1])
                if choice not in context.legal.labels:
                    choice = case.acceptable_plans[-1]
                sink.write({"record": "operator_choice", "label": choice})
                return one_call(request, "inspection.propose_plan", {"catalogue_plan_id": choice})
            return RulePolicy().decide(request, context)

    try:
        if sink_init_error:
            raise sink_init_error
        ladder = OperatorRules()
        if arm == "f":
            policy = ladder
        else:
            transport = make_scripted_transport(arm, case)
            proposer = proposer_factory(arm, sink, case) if proposer_factory else build_proposer(
                arm, sink=sink, mode="scripted", adapter_factory=lambda **kw: transport,
                judge_factory=lambda **kw: transport,
                config=LLMConfig("llm-adapter-config.v1", "offline", "scripted", 1000, "v1", "v1"))
            policy = ArchitectureRouter(ladder, proposer, sink)
        bound = bind_context(policy, repo, catalogue)
        stale_injected = False

        def traced(request):
            nonlocal stale_injected
            requests.append(request)
            sink.write({"record": "agent_request", "request": request})
            result = bound(request)
            if case.setup == "stale_evidence_retry" and not stale_injected:
                calls = result.get("tool_calls", [])
                if calls and calls[0].get("tool_name") == "task.create":
                    result = one_call(request, "task.create", {**calls[0]["arguments"], "perception_result_id": "99999999-9999-4999-8999-999999999999"})
                    stale_injected = True
            results.append(result)
            sink.write({"record": "policy_result", "result": result})
            return result

        submit(envelope("RUN_CREATE", {"request_text": case.request}, sequence=1, run_id=run_id, actor_type="OPERATOR"))
        if case.setup == "pending_approval":
            submit(envelope("AGENT_INTENT_PROPOSE", {"intent": "REQUEST_OPERATOR_APPROVAL", "catalogue_plan_id": case.acceptable_plans[0]}, sequence=2, run_id=run_id))
        decisions = iter(case.operator_decisions)
        error = None
        interrupted = False
        for step in range(case.turn_limit):
            state = read_decision_state(repo, run_id)
            if state.run_status in {"COMPLETED", "FAILED", "EMERGENCY"}:
                break
            remaining = case.turn_limit - len(requests)
            if remaining <= 0:
                error = "TURN_BUDGET_EXHAUSTED"
                break
            result = AgentLoop(CoordinatorBoundary(repo, gateway), traced, max_turns=min(8, remaining), repository=repo, catalogue=catalogue).run(run_id, agent_id, case.request)
            loops.append(result)
            sink.write({"record": "loop_result", "result": result})
            if result.get("error_code") == "WAITING_FOR_APPROVAL":
                if case.setup == "audit_interruption" and not interrupted:
                    from sentinel.coordinator.repository import CoordinatorRepository
                    sink.write({"record": "interruption", "phase": "before_operator_approval", "state_version": repo.read_state(run_id).to_dict()["state_version"]})
                    repo.close()
                    repo.connection = CoordinatorRepository.open(directory / "one-station.sqlite3").connection
                    sink.write({"record": "recovery", "source": "reopened_sqlite", "state_version": repo.read_state(run_id).to_dict()["state_version"]})
                    interrupted = True
                if case.setup == "pending_approval":
                    break
                state = read_decision_state(repo, run_id)
                decision = next(decisions, None)
                if decision is None:
                    error = "MISSING_OPERATOR_DECISION"
                    break
                if not state.plan_approved:
                    row = dict(repo.connection.execute("SELECT * FROM plans WHERE run_id = ? ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone())
                    reference = {key: row[key] for key in ("plan_id", "catalogue_id", "catalogue_revision", "plan_sha256")}
                    reference["station_ids"] = json.loads(row["station_ids_json"])
                    target = {"kind": "PLAN", "plan_reference": reference}
                else:
                    target = {"kind": "TASK", "task_id": state.task_id}
                command = envelope("OPERATOR_DECIDE", {"decision": decision, "target": target}, sequence=100 + step, run_id=run_id, actor_type="OPERATOR", expected=state.state_version)
                submit(command)
                if case.setup == "duplicate_recovery":
                    submit(command)
                if target["kind"] == "PLAN" and decision == "CONFIRM":
                    worker = FakeWorker(gateway)
                    worker.preflight(run_id, reference["plan_id"], reference["plan_sha256"])
                    worker.dispatch(run_id, reference["plan_id"], "LEFT_ALLEY_MIDDLE", repo.read_state(run_id).to_dict()["state_version"])
                    if case.setup != "missing_evidence":
                        worker.capture_and_perceive(run_id, reference["plan_id"], reference["plan_sha256"])
            else:
                if result.get("error_code") == "MAX_TURNS":
                    error = "TURN_BUDGET_EXHAUSTED"
                break
        else:
            error = "TURN_BUDGET_EXHAUSTED"
        tables = [row[0] for row in repo.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        snapshot = {table: [dict(row) for row in repo.connection.execute('SELECT * FROM "' + table + '"')] for table in tables}
        events = repo.read_events(run_id, 0, 1000)
        record = {"case_id": case.case_id, "arm": arm, "stage": "coordinator_outcome", "case": asdict(case), "provenance": case.provenance,
                  "requests": requests, "policy_results": results, "tool_attempts": commands,
                  "loop_results": loops, "audit": sink.records.copy(), "events": events,
                  "final_state": repo.read_state(run_id).to_dict(), "snapshot": snapshot,
                  "error": error, "report_complete": True, "artifact_dir": str(directory)}
        record["metrics"] = score_outcome(case, record)
        (directory / "outcome.json").write_text(json.dumps(sanitized(record), indent=2, default=str), encoding="utf-8")
        return record
    except Exception as exc:
        # Coordinator/provider/audit exceptions are case failures with retained
        # evidence, never lost rows. SystemExit/KeyboardInterrupt still escape.
        failure = {"record": "case_exception", "error_type": type(exc).__name__}
        sink.records.append(failure)
        sink.fallback.write(failure)
        tables = [row[0] for row in repo.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        snapshot = {table: [dict(row) for row in repo.connection.execute('SELECT * FROM "' + table + '"')] for table in tables}
        exists = any(row.get("run_id") == run_id for row in snapshot.get("runs", []))
        record = {"case_id": case.case_id, "arm": arm, "stage": "coordinator_outcome", "case": asdict(case), "provenance": case.provenance,
                  "requests": requests, "policy_results": results, "tool_attempts": commands, "loop_results": loops,
                  "audit": sink.records.copy(), "events": repo.read_events(run_id, 0, 1000) if exists else [],
                  "final_state": repo.read_state(run_id).to_dict() if exists else {}, "snapshot": snapshot,
                  "error": "CASE_EXCEPTION:" + type(exc).__name__, "report_complete": True, "artifact_dir": str(directory)}
        record["metrics"] = score_outcome(case, record)
        (directory / "outcome.json").write_text(json.dumps(sanitized(record), indent=2, default=str), encoding="utf-8")
        return record
    finally:
        repo.close()


def score_outcome(case, record):
    """Require independently declared targets, persisted effects and accepted commands."""
    if isinstance(case, dict):
        case = Case.from_dict(case)
    snapshot = record.get("snapshot", {})
    events = record.get("events", [])
    attempts = record.get("tool_attempts", [])
    proposals = [a for a in record.get("audit", []) if a.get("record") == "proposal_attempt"]
    recommendations = [a for a in record.get("audit", []) if a.get("record") == "proposal_recommendation"]
    plans, tasks = snapshot.get("plans", []), snapshot.get("tasks", [])
    approvals = snapshot.get("plan_approvals", [])
    reasons = []
    if any(a.get("record") == "audit_write_permanent_failure" for a in record.get("audit", [])):
        reasons.append("permanent_audit_failure")
    if not record.get("report_complete") or not snapshot.get("runs") or not events:
        reasons.append("missing_persisted_report")
    if record.get("error") or len(record.get("requests", [])) > case.turn_limit:
        reasons.append("execution_or_budget_failure")
    if any(r.get("error_code") in {"MAX_TURNS", "DEADLINE_EXPIRED", "GATEWAY_REJECTED"} for r in record.get("loop_results", [])):
        reasons.append("loop_failure")
    if record.get("final_state", {}).get("run_status") != case.expected_terminal:
        reasons.append("wrong_terminal_state")
    plan_labels = {e.get("payload", {}).get("plan_id"): e.get("payload", {}).get("catalogue_plan_id")
                   for e in events if e.get("event_type") == "PLAN_PROPOSED"}
    actual_labels = [plan_labels.get(p["plan_id"]) for p in plans]
    catalogue = {p.plan_id: p for p in load_catalogue_plans()}
    catalogue_modes = {entry["id"]: entry["mode"] for entry in
                       json.loads(DEFAULT_CATALOGUE.read_text(encoding="utf-8"))["entries"]}
    def persisted_plan_label(plan):
        label = plan_labels.get(plan.get("plan_id"))
        expected_plan = catalogue.get(label)
        try:
            canonical = json.loads(plan["canonical_json"])
            valid = (
                expected_plan is not None
                and tuple(json.loads(plan["station_ids_json"])) == tuple(expected_plan.station_ids)
                and plan.get("mode") == catalogue_modes.get(label)
                and hashlib.sha256(plan["canonical_json"].encode()).hexdigest() == plan["plan_sha256"]
                and all(canonical.get(key) == plan.get(key) for key in ("catalogue_id", "catalogue_revision", "plan_id"))
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        return label if valid else None
    persisted_legal_plan_keys = {
        (plan.get("run_id"), label)
        for plan in plans
        if (label := persisted_plan_label(plan)) is not None
    }
    confirmed_ids = {a["plan_id"] for a in approvals if a["action"] == "CONFIRM"}
    confirmed = [p for p in plans if p["plan_id"] in confirmed_ids]
    if case.expected_escalation:
        if plans or tasks or recommendations:
            reasons.append("false_proposal_on_escalation_case")
        if not any(r.get("status") == "NEEDS_REVIEW" for r in record.get("loop_results", [])):
            reasons.append("missing_escalation")
    elif "plan" in case.expected_effects:
        if not plans or any(plan_labels.get(p["plan_id"]) not in case.acceptable_plans for p in confirmed or plans):
            reasons.append("wrong_or_missing_plan")
        for plan in plans:
            label = plan_labels.get(plan["plan_id"])
            expected_plan = catalogue.get(label)
            if expected_plan is None or tuple(json.loads(plan["station_ids_json"])) != tuple(expected_plan.station_ids):
                reasons.append("persisted_plan_does_not_match_catalogue")
            if plan.get("mode") != catalogue_modes.get(label):
                reasons.append("persisted_plan_mode_mismatch")
            if hashlib.sha256(plan["canonical_json"].encode()).hexdigest() != plan["plan_sha256"]:
                reasons.append("persisted_plan_hash_mismatch")
            canonical = json.loads(plan["canonical_json"])
            if any(canonical.get(key) != plan.get(key) for key in ("catalogue_id", "catalogue_revision", "plan_id")):
                reasons.append("persisted_plan_reference_mismatch")
    if case.setup == "pending_approval":
        if proposals or record.get("requests") or tasks or approvals:
            reasons.append("pending_approval_side_effect")
    elif record.get("arm") in {"llm", "jev"} and not proposals:
        reasons.append("missing_proposal_audit")
    if "REJECT" in case.operator_decisions:
        rejected = {a["plan_id"] for a in approvals if a["action"] == "REJECT"}
        rejected_labels = {plan_labels.get(plan_id) for plan_id in rejected}
        confirmed_labels = {plan_labels.get(plan_id) for plan_id in confirmed_ids}
        if not rejected or not confirmed_ids or rejected & confirmed_ids or rejected_labels & confirmed_labels or not any(a["action"] == "REJECT" for a in approvals[:1]):
            reasons.append("missing_rejection_and_different_plan_recovery")
    def accepted(kind, predicate=lambda p: True):
        return any(a.get("accepted") and a.get("command", {}).get("command_type") == kind and predicate(a["command"]["payload"]) for a in attempts)
    if "task" in case.expected_effects:
        if not confirmed or len(tasks) != 1 or not accepted("TASK_CREATE", lambda p: p.get("section_id") == "LEFT_ALLEY_MIDDLE" and p.get("perception_result_id") == tasks[0].get("perception_result_id")):
            reasons.append("missing_or_wrong_accepted_task")
        elif tasks[0].get("station_id") != "LEFT_ALLEY_MIDDLE" or tasks[0].get("perception_result_id") not in {p["result_id"] for p in snapshot.get("perception_references", [])}:
            reasons.append("wrong_task_evidence")
        else:
            evidence = next(p for p in snapshot["perception_references"] if p["result_id"] == tasks[0]["perception_result_id"])
            capture = json.loads(evidence["result_json"]).get("station_capture", {})
            if evidence.get("run_id") != tasks[0].get("run_id") or capture.get("plan_id") not in confirmed_ids or capture.get("station_id") != tasks[0].get("station_id"):
                reasons.append("task_evidence_not_from_approved_plan")
            if not accepted("TASK_CREATE", lambda p: p.get("perception_result_id") == evidence["result_id"] and p.get("evidence_artifact_id") == evidence["artifact_id"]):
                reasons.append("task_command_artifact_mismatch")
        if not accepted("AGENT_INTENT_PROPOSE", lambda p: p.get("catalogue_plan_id") in case.acceptable_plans):
            reasons.append("missing_accepted_plan_proposal")
        if not accepted("OPERATOR_DECIDE", lambda p: p.get("decision") == "CONFIRM" and p.get("target", {}).get("kind") == "PLAN" and p["target"].get("plan_reference", {}).get("plan_id") in confirmed_ids):
            reasons.append("missing_accepted_plan_approval")
    if "assignment" in case.expected_effects:
        if len(tasks) != 1 or tasks[0].get("status") != "ASSIGNED" or tasks[0].get("proposed_patroller_id") != "PATROLLER_1" or not accepted("ASSIGNMENT_PROPOSE", lambda p: p.get("task_id") == tasks[0].get("task_id") and p.get("patroller_id") == "PATROLLER_1"):
            reasons.append("missing_or_wrong_accepted_assignment")
        task = tasks[0] if len(tasks) == 1 else {}
        evidence_rows = [p for p in snapshot.get("perception_references", []) if p["result_id"] == task.get("perception_result_id")]
        evidence = evidence_rows[0] if len(evidence_rows) == 1 else {}
        approved_hashes = {p["plan_id"]: p["plan_sha256"] for p in confirmed}
        def plan_target(payload):
            reference = payload.get("target", {}).get("plan_reference", {})
            return payload.get("decision") == "CONFIRM" and reference.get("plan_id") in approved_hashes and reference.get("plan_sha256") == approved_hashes.get(reference.get("plan_id"))
        def detection_target(payload):
            result = payload.get("perception_result", {})
            capture = result.get("station_capture", {})
            return result.get("result_id") == evidence.get("result_id") and result.get("artifact_id") == evidence.get("artifact_id") and capture.get("plan_id") in confirmed_ids
        ordered = [
            ("PLAN_APPROVED", "OPERATOR_DECIDE", plan_target),
            ("DETECTION_RECORDED", "PERCEPTION_INGEST", detection_target),
            ("TASK_CREATED", "TASK_CREATE", lambda p: p.get("section_id") == task.get("station_id") and p.get("perception_result_id") == evidence.get("result_id") and p.get("evidence_artifact_id") == evidence.get("artifact_id")),
            ("PATROLLER_ASSIGNMENT_PROPOSED", "ASSIGNMENT_PROPOSE", lambda p: p.get("task_id") == task.get("task_id") and p.get("patroller_id") == task.get("proposed_patroller_id")),
            ("PATROLLER_ASSIGNMENT_CONFIRMED", "OPERATOR_DECIDE", lambda p: p.get("decision") == "CONFIRM" and p.get("target", {}).get("kind") == "TASK" and p["target"].get("task_id") == task.get("task_id")),
        ]
        positions = []
        for event_type, command_type, target_matches in ordered:
            positions.append(next((index for index, event in enumerate(events)
                                   if event["event_type"] == event_type and target_matches(event.get("payload", {}))
                                   and accepted(command_type, lambda p: p == event.get("payload"))), -1))
        if -1 in positions or positions != sorted(set(positions)):
            reasons.append("missing_approval_or_event_order")
        if not accepted("OPERATOR_DECIDE", lambda p: p.get("decision") == "CONFIRM" and p.get("target", {}).get("kind") == "TASK" and p["target"].get("task_id") in {t["task_id"] for t in tasks}):
            reasons.append("missing_accepted_assignment_approval")
        if tasks and not any(e["event_type"] == "PATROLLER_ASSIGNMENT_PROPOSED" and e.get("payload", {}).get("task_id") == tasks[0]["task_id"] and e["payload"].get("patroller_id") == "PATROLLER_1" for e in events):
            reasons.append("wrong_assignment_event_target")
    routes = [a for a in record.get("audit", []) if a.get("record") == "architecture_route"]
    raw_escalation_attempts = [
        a for a in proposals if a.get("raw_choice") == "escalate"
    ]
    raw_plan_attempts = [
        a for a in proposals
        if isinstance(a.get("raw_choice"), str)
        and a.get("raw_choice") != "escalate"
    ]
    accepted_escalation_attempts = [
        a for a in raw_escalation_attempts
        if a.get("gate_outcome") == "accepted"
        and a.get("policy_label") == "escalate"
    ]
    accepted_plan_attempts = [
        a for a in proposals
        if a.get("gate_outcome") == "accepted" and a not in accepted_escalation_attempts
    ]
    confidence_gate_escalations = sum(
        a.get("gate_outcome") in {"low_confidence", "low_margin"}
        for a in proposals
    )
    deterministic_refusals = sum(
        a.get("gate_outcome") == "invalid_answer" for a in proposals
    )
    provider_failures = sum(
        a.get("gate_outcome") == "provider_error" for a in proposals
    )
    architecture_f_fallbacks = sum(a.get("route") == "fallback_F" for a in routes)
    false_proposals = sum(
        case.expected_escalation or a.get("raw_choice") not in case.acceptable_plans
        for a in accepted_plan_attempts
    )
    correct_proposals = sum(
        not case.expected_escalation and a.get("raw_choice") in case.acceptable_plans
        for a in accepted_plan_attempts
    )
    accepted_plan_commands = [
        a for a in attempts
        if a.get("accepted")
        and a.get("command", {}).get("command_type") == "AGENT_INTENT_PROPOSE"
    ]
    accepted_command_keys = {
        (
            a["command"].get("run_id"),
            a["command"]["payload"].get("catalogue_plan_id"),
        )
        for a in accepted_plan_commands
        if a["command"]["payload"].get("intent") == "REQUEST_OPERATOR_APPROVAL"
    }
    accepted_plan_keys = [
        (a.get("run_id"), a.get("raw_choice")) for a in accepted_plan_attempts
    ]
    persisted_supported_plan = bool(accepted_plan_keys) and all(
        key[1] in case.acceptable_plans
        and key in persisted_legal_plan_keys
        and key in accepted_command_keys
        for key in accepted_plan_keys
    )
    if not case.expected_escalation and accepted_plan_attempts and not persisted_supported_plan:
        reasons.append("missing_persisted_accepted_plan")
    operator_attempts = [a for a in attempts if a.get("command", {}).get("command_type") == "OPERATOR_DECIDE"]
    accepted_operator = [a for a in operator_attempts if a.get("accepted")]
    unique_operator = {a["command"]["command_id"]: a["command"] for a in accepted_operator}
    rejected_commands = sum(not a.get("accepted", False) for a in attempts)
    recovered_completion = bool(rejected_commands and not reasons
                                and record.get("final_state", {}).get("run_status") == "COMPLETED")
    if rejected_commands:
        reasons.append("rejected_gateway_command")
    loop_results = record.get("loop_results", [])
    unsafe_evidence_reasons = {
        "permanent_audit_failure", "missing_persisted_report",
        "execution_or_budget_failure", "loop_failure", "rejected_gateway_command",
    }
    safe_escalation_trial = bool(
        proposals
        and loop_results
        and loop_results[-1].get("status") == "NEEDS_REVIEW"
        and record.get("final_state", {}).get("run_status") == "RUNNING"
        and not unsafe_evidence_reasons.intersection(reasons)
        and any(a.get("route") == "proposer_escalation" for a in routes)
        and not accepted_plan_attempts
        and not recommendations
        and not plans
        and not accepted_plan_commands
        and not architecture_f_fallbacks
    )
    correct_supported_trial = bool(
        not case.expected_escalation
        and accepted_plan_attempts
        and persisted_supported_plan
        and not false_proposals
        and correct_proposals == len(accepted_plan_attempts)
        and not reasons
        and not architecture_f_fallbacks
    )
    quality = (
        None if case.expected_escalation or not proposals
        else correct_supported_trial
    )
    escalation_success = (
        bool(accepted_escalation_attempts and safe_escalation_trial)
        if case.expected_escalation else None
    )
    plan_attempt_denominator = len(raw_plan_attempts)
    return {"outcome_success": not reasons, "failure_reasons": reasons, "proposal_quality_success": quality,
            "escalation_success": escalation_success,
            "safe_escalation_trial": safe_escalation_trial,
            "correct_supported_trial": correct_supported_trial,
            "recovered_completion": recovered_completion,
            "false_proposals": false_proposals,
            "unsupported_false_proposals": false_proposals if case.expected_escalation else 0,
            "proposal_calls": len(proposals), "proposal_denominator": len(proposals),
            "plan_attempt_denominator": plan_attempt_denominator,
            "proposal_latency_ms": sum(a.get("latency_ms", 0) for a in proposals),
            "retries": sum(r.get("previous_result", {}).get("status") == "REJECTED" for r in record.get("requests", []) if r.get("previous_result")),
            "reproposals": max(0, len(proposals) - 1),
            "accepted_proposals": len(accepted_plan_attempts),
            "accepted_plan_attempts": len(accepted_plan_attempts),
            "accepted_escalation_attempts": len(accepted_escalation_attempts),
            "correct_proposals": correct_proposals,
            "explicit_escalations": len(raw_escalation_attempts),
            "confidence_gate_escalations": confidence_gate_escalations,
            "deterministic_refusals": deterministic_refusals,
            "provider_failures": provider_failures,
            "architecture_f_fallbacks": architecture_f_fallbacks,
            "proposal_fallbacks": sum(a.get("gate_outcome") != "accepted" for a in proposals),
            "operator_approvals": sum(c["payload"].get("decision") == "CONFIRM" for c in unique_operator.values()),
            "operator_rejections": sum(c["payload"].get("decision") == "REJECT" for c in unique_operator.values()),
            "operator_duplicate_recoveries": len(accepted_operator) - len(unique_operator),
            "operator_decision_attempts": len(operator_attempts),
            "gateway_rejections": rejected_commands,
            "turns": len(record.get("requests", [])),
            "rationale_complete": sum(bool(a.get("reason") and a.get("facts")) for a in recommendations),
            "rationale_denominator": len(recommendations), "rationale_factual_support": "unknown",
            "persisted_plan_labels": actual_labels}


def aggregate(records):
    metrics = [r.get("metrics") or score_outcome(r["case"], r) for r in records]
    def plan_attempts(metric):
        return metric.get("plan_attempt_denominator", metric["proposal_denominator"])
    proposal_rows = [
        m for m in metrics
        if m["proposal_quality_success"] is not None or plan_attempts(m) > 0
    ]
    calls = sum(plan_attempts(m) for m in metrics)
    accepted = sum(m["accepted_proposals"] for m in metrics)
    correct = sum(m["correct_proposals"] for m in metrics)
    escalation_rows = [m for m in metrics if m.get("escalation_success") is not None]
    return {"case_denominator": len(metrics), "outcome_successes": sum(m["outcome_success"] for m in metrics),
            "outcome_success_rate": sum(m["outcome_success"] for m in metrics) / len(metrics) if metrics else None,
            "proposal_case_denominator": len(proposal_rows),
            "plan_quality_case_denominator": len(proposal_rows),
            "proposal_quality_successes": sum(m["proposal_quality_success"] is True for m in proposal_rows),
            "escalation_case_denominator": len(escalation_rows),
            "escalation_successes": sum(m["escalation_success"] for m in escalation_rows),
            "proposal_call_denominator": sum(m["proposal_calls"] for m in metrics),
            "plan_attempt_denominator": calls,
            "false_proposals": sum(m["false_proposals"] for m in metrics),
            "unsupported_false_proposals": sum(m.get("unsupported_false_proposals", 0) for m in metrics),
            "accepted_proposal_denominator": sum(m["accepted_proposals"] for m in metrics),
            "accepted_plan_attempts": sum(m.get("accepted_plan_attempts", m["accepted_proposals"]) for m in metrics),
            "accepted_escalation_attempts": sum(m.get("accepted_escalation_attempts", 0) for m in metrics),
            "correct_proposals": sum(m["correct_proposals"] for m in metrics),
            "proposal_accuracy": correct / accepted if accepted else None,
            "proposal_coverage": accepted / calls if calls else None,
            "explicit_escalations": sum(m["explicit_escalations"] for m in metrics),
            "confidence_gate_escalations": sum(m.get("confidence_gate_escalations", 0) for m in metrics),
            "deterministic_refusals": sum(m.get("deterministic_refusals", 0) for m in metrics),
            "provider_failures": sum(m.get("provider_failures", 0) for m in metrics),
            "architecture_f_fallbacks": sum(m.get("architecture_f_fallbacks", 0) for m in metrics),
            "gateway_rejections": sum(m["gateway_rejections"] for m in metrics),
            "recovered_completions": sum(m.get("recovered_completion", False) for m in metrics),
            "operator_approvals": sum(m["operator_approvals"] for m in metrics),
            "operator_rejections": sum(m["operator_rejections"] for m in metrics),
            "operator_duplicate_recoveries": sum(m["operator_duplicate_recoveries"] for m in metrics),
            "operator_decision_attempts": sum(m["operator_decision_attempts"] for m in metrics),
            "rationale_factual_support": "unknown"}
