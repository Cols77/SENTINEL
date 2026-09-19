"""ShadowLadder (P2 Task 2): the F executor decides what is executed; a set
of named advisor policies run alongside it, in shadow, at judgment points
only. Their picks are recorded to an append-only audit trail and are never
acted on -- an advisor's exception, timeout or disagreement can never change
what the ladder returns.

This is the anti-automation-bias seam: every judgment-point audit record
carries ``operator_choice: null`` until Task 3's ``submit_choice`` appends a
matching ``operator_choice`` record, and Task 4's chat withholds the
``advisors`` field until that record exists.
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from .legal_tools import JUDGMENT_POINTS
from .policies import Decision
from .policy import ContextualPolicy, PolicyContext

_EMPTY_ADVISOR_ENTRY = {"label": None, "confidence": None, "band": None, "model": None,
                        "latency_ms": None, "error": None}


@runtime_checkable
class AuditSink(Protocol):
    """Anything that can durably record one audit record at a time."""

    def write(self, record: dict) -> None:
        ...


class JsonlAuditSink:
    """Append-only JSONL audit sink. Every ``write`` opens the file in
    append mode and writes exactly one line, so it never truncates existing
    records -- including across separate ``JsonlAuditSink`` instances over
    the same path (e.g. a resumed run)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


class MemoryAuditSink:
    """In-process audit sink for tests and short-lived tools."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def write(self, record: dict) -> None:
        self.records.append(record)


def _canonical_sha256(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _advisor_entry(decision: Decision | None, *, fallback_latency_ms: int, error: str | None) -> dict:
    if error is not None:
        return {**_EMPTY_ADVISOR_ENTRY, "latency_ms": fallback_latency_ms, "error": error}
    if decision is None:
        return {**_EMPTY_ADVISOR_ENTRY, "latency_ms": fallback_latency_ms,
                "error": "advisor returned without recording a decision"}
    return {
        "label": decision.label, "confidence": decision.confidence, "band": decision.band,
        "model": decision.model,
        "latency_ms": decision.latency_ms if decision.latency_ms is not None else fallback_latency_ms,
        "error": decision.error,
    }


class ShadowLadder:
    """Wraps an ``executor`` (P1's ``RulePolicy``) that decides what is
    executed, and a list of ``(name, advisor)`` shadow advisors that are
    called only at judgment points (``JUDGMENT_POINTS``), never at RULE
    points and never in a way that can affect ``decide``'s return value.

    Every call writes exactly one audit record through ``sink`` before
    returning: a ``judgment`` record (with both the executor's decision and
    every advisor's decision, or its error) at a judgment point, or a
    ``rule`` record everywhere else.
    """

    def __init__(self, executor: ContextualPolicy, advisors: Sequence[tuple[str, ContextualPolicy]],
                 sink: AuditSink, *, advisor_timeout_s: float = 5.0) -> None:
        self.executor = executor
        self.advisors = list(advisors)
        self.sink = sink
        self.advisor_timeout_s = advisor_timeout_s

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        decision_point = context.legal.decision_point
        if decision_point in JUDGMENT_POINTS:
            return self._judgment_step(request, context, decision_point)
        return self._rule_step(request, context, decision_point)

    # -- RULE points: no advisors, executor decides, one audit record -------

    def _rule_step(self, request: dict[str, Any], context: PolicyContext, decision_point: str) -> dict[str, Any]:
        result = self.executor.decide(request, context)
        executor_decision = self.executor.last
        self.sink.write({
            "record": "rule",
            "run_id": context.state.run_id,
            "state_version": context.state.state_version,
            "decision_point": decision_point,
            "label": executor_decision.label if executor_decision is not None else None,
        })
        return result

    # -- judgment points: advisors run in shadow first, executor still wins --

    def _judgment_step(self, request: dict[str, Any], context: PolicyContext, decision_point: str) -> dict[str, Any]:
        advisor_entries = {
            name: self._run_advisor(advisor, request, context)
            for name, advisor in self.advisors
        }

        # The executor's own decide() is the only call whose result is ever
        # returned -- it runs after (and regardless of) the advisors above,
        # so nothing an advisor does (including raising or hanging) can
        # reach it.
        result = self.executor.decide(request, context)
        executor_decision = self.executor.last

        packet = context.state.to_packet()
        self.sink.write({
            "record": "judgment",
            "run_id": context.state.run_id,
            "state_version": context.state.state_version,
            "decision_point": decision_point,
            "packet_sha256": _canonical_sha256(packet),
            "legal_labels": list(context.legal.labels),
            "executor": ({"chooser": executor_decision.chooser, "label": executor_decision.label}
                         if executor_decision is not None else None),
            "advisors": advisor_entries,
            "operator_choice": None,
        })
        return result

    def _run_advisor(self, advisor: ContextualPolicy, request: dict[str, Any], context: PolicyContext) -> dict:
        start = time.perf_counter()
        pool = ThreadPoolExecutor(max_workers=1)
        error: str | None = None
        try:
            future = pool.submit(advisor.decide, request, context)
            try:
                future.result(timeout=self.advisor_timeout_s)
            except FutureTimeoutError:
                error = f"advisor timed out after {self.advisor_timeout_s:.3f}s"
            except Exception as exc:  # noqa: BLE001 -- any advisor failure is swallowed here
                error = f"{type(exc).__name__}: {exc}"
        finally:
            # Never block on a hung advisor thread: a timeout must return
            # immediately, so the pool is torn down without waiting for it.
            pool.shutdown(wait=False)
        latency_ms = int((time.perf_counter() - start) * 1000)
        decision = None if error is not None else getattr(advisor, "last", None)
        return _advisor_entry(decision, fallback_latency_ms=latency_ms, error=error)


__all__ = ["AuditSink", "JsonlAuditSink", "MemoryAuditSink", "ShadowLadder"]
