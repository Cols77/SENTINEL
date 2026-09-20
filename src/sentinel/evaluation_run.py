"""Reproducibility, bounded transports and explicit authorization for evaluations.

This module does not authorize itself or create a provider on import. A live
batch must match the digest printed by its dry run, as well as rehearsal's
existing provider consent gate. Raw records never include HTTP headers.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid
from typing import Any, Callable

CONSENT_ENV = "SENTINEL_PROPOSAL_EVAL_CONSENT"
VERSION = "proposal-eval-run.v1"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def manifest_digest(manifest: dict) -> str:
    return hashlib.sha256(canonical(manifest).encode()).hexdigest()


def require_authorization(manifest: dict) -> None:
    if manifest.get("mode") == "live" and os.environ.get(CONSENT_ENV) != manifest_digest(manifest):
        raise PermissionError(f"{CONSENT_ENV} must equal this dry-run manifest's SHA-256")


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def revision(root: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    status = git("status", "--porcelain")
    return {"revision": git("rev-parse", "HEAD"), "dirty": bool(status),
            "dirty_status_sha256": hashlib.sha256(status.encode()).hexdigest()}


def source_digest(root: Path) -> str:
    """Bind dirty source contents too: porcelain alone only names changed files."""
    files = sorted(p for directory in ("src", "scripts", "tests")
                   for p in (root / directory).rglob("*.py") if "__pycache__" not in p.parts)
    return manifest_digest({p.relative_to(root).as_posix(): file_digest(p) for p in files})


def build_manifest(*, root: Path, fixture: Path, catalogue: Path, arms: list[str],
                   mode: str, repeats: int, max_calls: int, configurations: dict,
                   cost_per_call_usd: float | None = None, recording: Path | None = None) -> dict:
    if mode not in {"scripted", "recorded", "live"}:
        raise ValueError("unknown execution mode")
    if repeats < 1 or max_calls < 0:
        raise ValueError("repeats must be positive and call budget nonnegative")
    if cost_per_call_usd is not None and (not math.isfinite(cost_per_call_usd) or cost_per_call_usd < 0):
        raise ValueError("cost estimate must be finite and nonnegative")
    return {
        "version": VERSION, **revision(root), "source_sha256": source_digest(root),
        "fixture_sha256": file_digest(fixture), "catalogue_sha256": file_digest(catalogue),
        "recording_sha256": file_digest(recording) if recording else None,
        "arms": arms, "mode": mode, "repeats": repeats, "max_calls": max_calls,
        "configurations": configurations,
        "thresholds": {"jev_act_at": 0.60, "jev_min_margin": 0.15},
        "estimated_cost_usd": None if cost_per_call_usd is None else max_calls * cost_per_call_usd,
        "cost_per_call_usd_assumption": cost_per_call_usd,
        "cost_estimate_status": "operator_supplied_estimate" if cost_per_call_usd is not None else "unknown",
        "release_thresholds": None, "approval_policy": "scripted operator decisions; simulated approval tests",
        "quality_claim": "fresh independently labelled cases only" if mode == "live" else "not a fresh model-quality measurement",
    }


def sanitized(value: Any) -> Any:
    """Exclude credential fields, known credential values, and non-JSON objects."""
    secret_names = {"authorization", "proxyauthorization", "apikey", "xapikey", "accesstoken", "refreshtoken", "credential", "credentials", "password", "cookie", "setcookie"}
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {sanitized(str(k)): sanitized(v) for k, v in value.items()
                if str(k).lower().replace("_", "").replace("-", "") not in secret_names}
    if isinstance(value, (list, tuple)):
        return [sanitized(v) for v in value]
    if isinstance(value, str):
        for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", os.environ.get("SENTINEL_LLM_API_KEY_ENV", "")):
            secret = os.environ.get(name) if name else None
            if secret:
                value = value.replace(secret, "<redacted>")
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return "<nonfinite>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "<unserializable>"


class RawStream:
    def __init__(self, path: Path):
        self.path = path

    def write(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical(sanitized(record)) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class BudgetExhausted(RuntimeError):
    pass


class CallBudget:
    def __init__(self, maximum: int):
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
            raise ValueError("call budget must be a nonnegative integer")
        self.maximum = maximum
        self.used = 0
        self.exhausted = False

    def invoke(self, call: Callable, *args, **kwargs):
        if self.used >= self.maximum:
            self.exhausted = True
            raise BudgetExhausted("evaluation call budget exhausted")
        self.used += 1  # provider failures and retries consume authorization
        return call(*args, **kwargs)


class RecordedTransport:
    """Replay adapter/judge outputs in order; exhaustion never invents an answer."""
    def __init__(self, arm: str, records: list[dict], *, strict: bool = False):
        self.arm = arm
        self.records = list(records)
        self.position = 0
        self.strict = strict
        self.exhausted = False
        self.invalid = False

    @property
    def remaining(self):
        return len(self.records) - self.position

    def validate_complete(self):
        if self.remaining:
            self.invalid = True
            raise RecordingError("recording_unused")

    def _next(self, request):
        if not self.remaining:
            self.exhausted = True
            raise RecordingError("recording_exhausted", "recording exhausted")
        row = self.records[self.position]
        self.position += 1
        if not isinstance(row, dict) or "result" not in row and not row.get("error_category"):
            self.invalid = True
            raise RecordingError("recording_invalid")
        if self.strict and "request" not in row:
            self.invalid = True
            raise RecordingError("recording_request_missing")
        if "request" in row and normalized_request(row["request"]) != normalized_request(sanitized(request)):
            self.invalid = True
            raise RecordingError("recording_request_mismatch")
        if row.get("error_category"):
            raise RecordingError("recorded_provider_error", "recorded provider_error")
        return row["result"]

    def choice(self, **kwargs):
        return self._next(kwargs)

    def propose(self, request, context):
        value = self._next(proposal_request(request, context))
        if isinstance(value, dict):
            value = {**value, "agent_run_id": request.agent_run_id}
        return value


class RecordingError(RuntimeError):
    def __init__(self, category, message=None):
        self.category = category
        super().__init__(message or category)


def proposal_request(request, context):
    return {"agent_request": request.to_dict(),
            "context": {"state": context.state.to_packet(), "legal": sanitized(context.legal)}}


def normalized_request(value):
    """Remap runtime identifiers consistently; preserve semantic string contents."""
    identifiers = {}
    runtime_ids = {"run_id", "agent_run_id", "event_id", "command_id", "task_id", "plan_id",
                   "request_id", "result_id", "perception_result_id", "assignment_id", "correlation_id", "causation_id"}

    def normalize(item, key=None):
        if isinstance(item, dict):
            if key == "event_context":
                event_type, summary = item.get("event_type"), item.get("summary")
                if isinstance(event_type, str) and event_type and isinstance(summary, str) and summary.startswith(event_type + ": "):
                    try:
                        payload = json.loads(summary[len(event_type) + 2:])
                    except (ValueError, TypeError):
                        payload = None
                    if isinstance(payload, dict):
                        # Only coordinator event summaries encode structured data.
                        # User prose and truncated/non-JSON summaries stay exact.
                        item = {**item, "summary": {"event_type": event_type, "payload": payload}}
            return {k: normalize(v, k) for k, v in sorted(item.items())}
        if isinstance(item, (tuple, list)):
            return [normalize(v, key) for v in item]
        if key == "deadline":
            return "<runtime-deadline>"
        if key in runtime_ids and isinstance(item, str):
            try:
                uuid.UUID(item)
            except ValueError:
                return item
            return identifiers.setdefault(item, f"<runtime-id-{len(identifiers)}>")
        return item

    return normalize(value)


class MeasuredTransport:
    """Wrap one adapter/judge call. Live providers must disable hidden retries."""
    def __init__(self, transport: Any, budget: CallBudget, stream: RawStream, *, arm: str, case_id: str):
        self.transport, self.budget, self.stream = transport, budget, stream
        self.arm, self.case_id = arm, case_id
        self.calls = 0
        self.budget_refusals = 0
        self.failures: list[str] = []
        self.latencies: list[float] = []

    def _call(self, method: str, request: Any, *args, **kwargs):
        self.stream.write({"record": "provider_request", "arm": self.arm, "case_id": self.case_id,
                           "request": request})
        start = time.perf_counter()
        try:
            result = self.budget.invoke(getattr(self.transport, method), *args, **kwargs)
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            if not isinstance(exc, BudgetExhausted):
                self.calls += 1
            else:
                self.budget_refusals += 1
            category = ("budget_exhausted" if isinstance(exc, BudgetExhausted) else
                        exc.category if isinstance(exc, RecordingError) else
                        "provider_timeout" if isinstance(exc, TimeoutError) else "provider_error")
            self.failures.append(category)
            self.latencies.append(elapsed)
            self.stream.write({"record": "provider_result", "arm": self.arm, "case_id": self.case_id,
                               "result": None, "latency_ms": elapsed,
                               "error_category": category})
            raise
        self.calls += 1
        elapsed = (time.perf_counter() - start) * 1000
        self.latencies.append(elapsed)
        document = sanitized(result)
        category = {"UNAVAILABLE": "provider_error", "MALFORMED": "malformed_result"}.get(
            document.get("status")) if isinstance(document, dict) else None
        if category:
            self.failures.append(category)
        self.stream.write({"record": "provider_result", "arm": self.arm, "case_id": self.case_id,
                           "result": result, "latency_ms": elapsed, "error_category": category})
        return result

    def choice(self, **kwargs):
        return self._call("choice", kwargs, **kwargs)

    def propose(self, request, context):
        return self._call("propose", proposal_request(request, context), request, context)

    def forget(self, agent_run_id):
        forget = getattr(self.transport, "forget", None)
        if callable(forget):
            forget(agent_run_id)
