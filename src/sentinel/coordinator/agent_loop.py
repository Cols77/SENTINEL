"""Bounded, provider-neutral coordinator agent loop."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sentinel.contracts import AgentModelResult, AgentRequest, AgentToolResult, validate_document

from .decision_state import read_decision_state, score_band
from .errors import CoordinatorError
from .legal_tools import legal_set, load_catalogue_plans


_TOOLS = ["state.get_summary", "state.get_recent_events", "inspection.propose_plan", "task.create", "patroller.rank_for_task", "assignment.propose", "wait_for_event", "finish"]
_TERMINAL = {"COMPLETED", "FAILED", "EMERGENCY"}
_COMMAND_TYPES = {"inspection.propose_plan": "AGENT_INTENT_PROPOSE", "task.create": "TASK_CREATE", "patroller.rank_for_task": "TASK_RANK", "assignment.propose": "ASSIGNMENT_PROPOSE"}
_RESULT_KINDS = {"inspection.propose_plan": "plan", "task.create": "task", "patroller.rank_for_task": "ranking", "assignment.propose": "assignment"}

# D1/D2 context-summary redaction budget: nested facts (e.g. a station id buried
# inside a detection payload) are recursed into and kept ahead of verbose flat
# provenance metadata, so a bounded 512-char summary still surfaces what matters.
_SUMMARY_MAX = 512
_BLOB_CHARS = 200
_SHORT_KEYS = {"status", "class", "canonical_class", "source_class", "kind", "score", "raw_score",
               "station_id", "zone_id", "marker_id", "reason", "decision", "summary", "mode"}
_ID_SUFFIXES = ("_id", "_ids")
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _field_priority(key: str, value: Any) -> int:
    """Nested structures first (they may hide the fact a policy needs), then short
    id-like/status-like scalars, then bare ``*_id`` fields, then everything else."""
    if isinstance(value, (dict, list)):
        return 0
    if key in _SHORT_KEYS:
        return 1
    if key.endswith(_ID_SUFFIXES):
        return 2
    return 3


def _redacted(payload: Any, *, budget: int = _SUMMARY_MAX - 40) -> Any:
    """Keep ids and scalar fields; drop oversized blobs once a level stops fitting
    the bounded summary budget. Recurses depth-first and priority-ordered so small
    identifying facts survive ahead of verbose flat metadata (D1)."""
    if isinstance(payload, list):
        return [_redacted(item, budget=budget) if isinstance(item, (dict, list)) else item for item in payload]
    if not isinstance(payload, dict):
        return payload
    out: dict[str, Any] = {}
    used = 2  # "{}"
    for key, value in sorted(payload.items(), key=lambda kv: (_field_priority(kv[0], kv[1]), kv[0])):
        remaining = max(budget - used, 40)
        if isinstance(value, dict):
            candidate: Any = _redacted(value, budget=remaining)
        elif isinstance(value, list):
            candidate = [_redacted(item, budget=remaining) if isinstance(item, (dict, list)) else item for item in value]
        elif isinstance(value, str) and len(value) > _BLOB_CHARS:
            candidate = f"<{len(value)} chars omitted>"
        else:
            candidate = value
        piece_len = len(json.dumps({key: candidate}, separators=(",", ":")))
        if out and used + piece_len > budget:
            continue
        out[key] = candidate
        used += piece_len
    return out


def _event_summary(event: dict) -> dict:
    event_type = str(event.get("event_type", "")) or "EVENT"
    payload = event.get("payload") or {}
    if payload:
        body = json.dumps(_redacted(payload), separators=(",", ":"))
        summary = f"{event_type}: {body}"[:_SUMMARY_MAX]
    else:
        summary = event_type[:_SUMMARY_MAX]
    return {"sequence_no": event.get("sequence_no", 0), "event_type": event_type[:64], "summary": summary or event_type[:64] or "EVENT"}


def command_for(tool_name: str, arguments: dict[str, Any], run_id: str, actor_type: str, actor_id: str,
                 key: str) -> dict[str, Any] | None:
    """Build the ``command_envelope`` for one legal-candidate tool call: the
    single tool-name -> ``command_type`` mapping (D7), including the
    ``inspection.propose_plan`` payload wrapper. Returns ``None`` for a tool
    with no command mapping (``wait_for_event``, ``finish``); callers decide
    what "no command" means for their own tool set. ``key`` is the caller's
    idempotency key -- ``command_id`` and ``correlation_id`` are both
    deterministic UUIDs derived from it, so this never depends on
    ``actor_id`` happening to be a UUID (an operator actor id like
    ``"OPERATOR_1"`` is not one). Shared by ``AgentLoop._execute`` and P2
    Task 3's operator choice path so there is exactly one source of truth
    for the mapping."""
    command_type = _COMMAND_TYPES.get(tool_name)
    if command_type is None:
        return None
    payload = dict(arguments)
    if tool_name == "inspection.propose_plan":
        payload = {"intent": "REQUEST_OPERATOR_APPROVAL", **payload}
    return {
        "contract_version": "sentinel.v0", "wire_type": "command_envelope",
        "command_id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
        "command_type": command_type, "run_id": run_id,
        "actor_type": actor_type, "actor_id": actor_id,
        "correlation_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "correlation:" + key)),
        "idempotency_key": key, "issued_at": "2999-01-01T00:00:00Z", "payload": payload,
    }


def _normalize_error_code(code: Any) -> str:
    candidate = re.sub(r"[^A-Z0-9_]", "_", str(code or "").upper()).strip("_") or "GATEWAY_ERROR"
    if not candidate[:1].isalpha():
        candidate = "E_" + candidate
    candidate = candidate[:64]
    if len(candidate) < 3:
        candidate = (candidate + "_ERR")[:64]
    return candidate if _ERROR_CODE_RE.match(candidate) else "GATEWAY_ERROR"


class AgentLoop:
    """Run a policy through typed requests, one validated tool call per turn."""

    def __init__(self, port, policy: Callable[[dict[str, Any]], dict[str, Any]] | None = None, *,
                 max_turns: int = 8, repository=None, catalogue=None, model: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        resolved = policy if policy is not None else model
        if resolved is None:
            raise TypeError("AgentLoop requires a policy (positionally, or via the legacy model= kwarg)")
        self.port = port
        self.policy = resolved
        self.model = resolved  # backwards-compatible alias
        self.max_turns = min(max(1, max_turns), 8)
        self.repository = repository
        self.catalogue = catalogue if catalogue is not None else (load_catalogue_plans() if repository is not None else None)

    def run(self, run_id: str, agent_run_id: str, user_request: str, *, deadline: str = "2999-01-01T00:00:00Z") -> dict[str, Any]:
        try:
            return self._run_turns(run_id, agent_run_id, user_request, deadline)
        finally:
            # D8/D9: tell the policy the run is over for this agent_run_id on
            # *every* return path -- a finish call, WAITING_FOR_APPROVAL, a
            # terminal run_status, DEADLINE_EXPIRED, EMERGENCY, MAX_TURNS
            # exhaustion, or corrective-retry-budget exhaustion -- not only
            # when the model itself proposes ``finish``. A policy that keeps
            # bounded per-agent_run_id state across turns (the LLM adapter)
            # must not see stale pre-boundary state on a resumed run that
            # reuses the same agent_run_id, e.g. after WAITING_FOR_APPROVAL.
            forget = getattr(self.policy, "forget", None)
            if callable(forget):
                forget(agent_run_id)

    def _run_turns(self, run_id: str, agent_run_id: str, user_request: str, deadline: str) -> dict[str, Any]:
        previous = None
        cursor = 0
        corrective_retries = 0
        for _turn in range(self.max_turns):
            state = self.port.read_state(run_id)
            if hasattr(state, "to_dict"):
                state = state.to_dict()
            if self._deadline_expired(deadline):
                return {"status": "FAILED", "error_code": "DEADLINE_EXPIRED", "summary": "agent deadline expired"}
            if state.get("emergency_latched") or state.get("run_status") == "EMERGENCY":
                return {"status": "EMERGENCY", "error_code": "EMERGENCY_LATCHED", "summary": "emergency stop is latched"}
            if state.get("run_status") == "WAITING_FOR_APPROVAL":
                events = self.port.read_events(run_id, cursor, 32)
                cursor = max([cursor, *[int(event.get("sequence_no", cursor)) for event in events]])
                return AgentToolResult.from_dict({
                    "contract_version": "sentinel.v0", "wire_type": "agent_tool_result", "tool_name": "wait_for_event",
                    "status": "ACCEPTED", "error_code": "WAITING_FOR_APPROVAL",
                    "state_version": state.get("state_version", 0), "event_cursor": cursor,
                    "legal_tools": [], "safe_summary": "operator approval is required", "result": {"kind": "wait"},
                }).to_dict()
            if state.get("run_status") in _TERMINAL:
                return {"status": state["run_status"], "summary": "run reached a terminal state", "state_version": state.get("state_version", 0)}
            events = self.port.read_events(run_id, cursor, 32)
            cursor = max([cursor, *[int(event.get("sequence_no", cursor)) for event in events]])
            request = AgentRequest(agent_run_id=agent_run_id, run_id=run_id, state_version=int(state.get("state_version", 0)), allowed_intent_schema_id="https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult", deadline=deadline, phase=str(state.get("agent_phase", "PLAN")), event_context=[_event_summary(event) for event in events], evidence_context=self._evidence_context(run_id), legal_tools=self._legal_tools(run_id), previous_result=previous, user_request=user_request).to_dict()
            try:
                validate_document(request)
                raw_result = self.policy(request)
                if raw_result.get("status") == "TOOL_CALLS" and len(raw_result.get("tool_calls", ())) > 1:
                    return {"status": "FAILED", "error_code": "MULTIPLE_TOOL_CALLS", "summary": "V0 accepts exactly one tool call per turn"}
                model_result = AgentModelResult.from_dict(raw_result)
                if model_result.agent_run_id != agent_run_id:
                    raise ValueError("agent result belongs to a different agent run")
            except Exception:
                # Bounded corrective feedback: a schema-invalid result earns the
                # model a REJECTED previous_result for the same turn budget,
                # up to twice per run, before INVALID_AGENT_RESULT is terminal.
                if corrective_retries >= 2:
                    return {"status": "FAILED", "error_code": "INVALID_AGENT_RESULT", "summary": "agent result failed contract validation"}
                corrective_retries += 1
                previous = AgentToolResult.from_dict({
                    "contract_version": "sentinel.v0", "wire_type": "agent_tool_result", "tool_name": "state.get_summary",
                    "status": "REJECTED", "error_code": "INVALID_AGENT_RESULT",
                    "state_version": int(state.get("state_version", 0)), "event_cursor": cursor,
                    "legal_tools": _TOOLS,
                    "safe_summary": "previous agent result was rejected as schema-invalid; emit exactly one valid agent_result with the matching agent_run_id",
                    "result": {"kind": "empty"},
                }).to_dict()
                continue
            if model_result.status == "INTENT":
                return {"status": "NEEDS_REVIEW", "summary": "agent intent requires operator review"}
            if model_result.status in {"NO_ACTION", "MALFORMED", "UNAVAILABLE"}:
                return {"status": "FAILED", "summary": model_result.diagnostic or "agent did not produce an actionable result"}
            if len(model_result.tool_calls) != 1:
                return {"status": "FAILED", "error_code": "MULTIPLE_TOOL_CALLS", "summary": "V0 accepts exactly one tool call per turn"}
            call = model_result.tool_calls[0]
            if call.tool_name == "finish":
                return {"status": call.arguments["status"], "summary": call.arguments["summary"]}
            outcome = self._execute(call.tool_name, call.arguments, run_id, agent_run_id, model_result.agent_run_id, state, cursor)
            if outcome.get("status") == "REJECTED":
                # D6: a gateway rejection (or an unmapped tool, D7) shares the same
                # bounded corrective budget as a schema-invalid result.
                if corrective_retries >= 2:
                    return {"status": "FAILED", "error_code": "GATEWAY_REJECTED", "summary": "gateway correction budget exhausted"}
                corrective_retries += 1
            previous = outcome
        return {"status": "NEEDS_REVIEW", "error_code": "MAX_TURNS", "summary": "agent turn budget exhausted"}

    @staticmethod
    def _deadline_expired(deadline: str) -> bool:
        try:
            expiry = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return True
        return datetime.now(timezone.utc) >= expiry.astimezone(timezone.utc)

    def _legal_tools(self, run_id: str) -> list[str]:
        """D3: the computed legal action set, plus the two always-available reads.
        Without a repository the legacy fixed tool list is kept unchanged."""
        if self.repository is None:
            return _TOOLS
        try:
            decision_state = read_decision_state(self.repository, run_id)
        except KeyError:
            return _TOOLS
        legal = legal_set(decision_state, self.catalogue or ())
        if not legal.candidates:
            return _TOOLS
        names = {candidate.tool_name for candidate in legal.candidates} | {"state.get_summary", "state.get_recent_events"}
        return sorted(names)

    def _evidence_context(self, run_id: str) -> list[dict]:
        """D2: the run's perception evidence, most recent first, capped at 32."""
        if self.repository is None:
            return []
        rows = self.repository.connection.execute(
            """SELECT pr.result_id, pr.artifact_id, pr.result_json, pr.created_at, o.station_id, a.source_kind
               FROM perception_references pr
               JOIN observations o ON o.observation_id = pr.observation_id
               JOIN artifacts a ON a.artifact_id = pr.artifact_id
               WHERE pr.run_id = ?
               ORDER BY pr.created_at DESC, pr.result_id DESC
               LIMIT 32""",
            (run_id,),
        ).fetchall()
        items = []
        for row in rows:
            try:
                result = json.loads(row["result_json"])
            except (TypeError, ValueError):
                result = {}
            band = score_band(result.get("raw_score"))
            summary = f"station={row['station_id']} class={result.get('canonical_class')} score_band={band} result_id={row['result_id']}"[:512]
            items.append({
                "artifact_id": row["artifact_id"],
                "source_kind": row["source_kind"],
                "status": str(result.get("status") or "UNKNOWN")[:64],
                "summary": summary,
            })
        return items

    def _execute(self, tool_name, arguments, run_id, agent_run_id, result_agent_run_id, state, cursor):
        safe_summary = "bounded coordinator result"
        if tool_name == "state.get_summary":
            value = self.port.read_state(run_id)
            result = {"kind": "state_summary", "run_status": str(value.get("run_status", "UNKNOWN")), "phase": str(value.get("agent_phase", "UNKNOWN")), "state_version": int(value.get("state_version", 0))}
        elif tool_name == "state.get_recent_events":
            value = self.port.read_events(run_id, arguments["after_sequence"], arguments["limit"])
            result = {"kind": "event_list", "events": [_event_summary(item) for item in value]}
        elif tool_name == "wait_for_event":
            result = {"kind": "wait"}
        else:
            payload = dict(arguments)
            fingerprint = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            key = "agent-" + hashlib.sha256((agent_run_id + ":" + tool_name + ":" + fingerprint).encode()).hexdigest()
            command = command_for(tool_name, arguments, run_id, "AGENT", agent_run_id, key)
            if command is None:
                # D7: an unmapped tool name is rejected without ever calling submit.
                return AgentToolResult.from_dict({
                    "contract_version": "sentinel.v0", "wire_type": "agent_tool_result", "tool_name": tool_name,
                    "status": "REJECTED", "error_code": "UNKNOWN_TOOL",
                    "state_version": int(state.get("state_version", 0)), "event_cursor": cursor,
                    "legal_tools": self._legal_tools(run_id),
                    "safe_summary": f"unknown tool: {tool_name}"[:512],
                    "result": {"kind": "empty"},
                }).to_dict()
            command_type = command["command_type"]
            try:
                submit_result = self.port.submit(command)
            except CoordinatorError as exc:
                # D6: a gateway rejection is fed back instead of escaping run().
                return AgentToolResult.from_dict({
                    "contract_version": "sentinel.v0", "wire_type": "agent_tool_result", "tool_name": tool_name,
                    "status": "REJECTED", "error_code": _normalize_error_code(exc.code),
                    "state_version": int(state.get("state_version", 0)), "event_cursor": cursor,
                    "legal_tools": self._legal_tools(run_id),
                    "safe_summary": str(exc)[:512],
                    "result": {"kind": "empty"},
                }).to_dict()
            result = {"kind": _RESULT_KINDS.get(tool_name, "empty")}
            # D5 (within schema): ids stay out of `result` (locked to {"kind"} by
            # the current wire schema; typed ids are a Task 6 amendment) but do
            # surface in `safe_summary`, which is a free-form bounded string.
            ids = {}
            if self.repository is not None:
                try:
                    ids = read_decision_state(self.repository, run_id).ids()
                except KeyError:
                    ids = {}
            status_word = submit_result.get("status", "COMPLETED") if isinstance(submit_result, dict) else "COMPLETED"
            safe_summary = f"{command_type} {status_word}; ids: {ids}"[:512]
        current = self.port.read_state(run_id)
        events = self.port.read_events(run_id, cursor, 32)
        next_cursor = max([cursor, *[int(event.get("sequence_no", cursor)) for event in events]])
        legal_tools = [] if current.get("run_status") == "WAITING_FOR_APPROVAL" else _TOOLS
        typed = {"contract_version": "sentinel.v0", "wire_type": "agent_tool_result", "tool_name": tool_name, "status": "ACCEPTED", "error_code": None, "state_version": int(current.get("state_version", state.get("state_version", 0))), "event_cursor": next_cursor, "legal_tools": legal_tools, "safe_summary": safe_summary, "result": result}
        return AgentToolResult.from_dict(typed).to_dict()
