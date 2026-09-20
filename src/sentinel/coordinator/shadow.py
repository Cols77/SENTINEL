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
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from .legal_tools import ADVISORY_POINTS, needs_judgment
from .policies import Decision
from .policy import ContextualPolicy, PolicyContext

_EMPTY_ADVISOR_ENTRY = {"label": None, "confidence": None, "band": None, "judge_label": None,
                        "model": None, "latency_ms": None, "error": None}


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
        # The judge's own pick, alongside what the policy did with it. These
        # differ on every non-HIGH answer, and recording only the former is
        # what made the 2026-09-20 sweep read as "Jev abstained on every turn".
        "judge_label": decision.judge_label,
        "model": decision.model,
        "latency_ms": decision.latency_ms if decision.latency_ms is not None else fallback_latency_ms,
        "error": decision.error,
    }


class ShadowLadder:
    """Wraps an ``executor`` (P1's ``RulePolicy``) that decides what is
    executed, and a list of ``(name, advisor)`` shadow advisors that are
    called only at advisory points (``ADVISORY_POINTS`` -- ``SELECT_PLAN``,
    which the operator decides, and ``EVIDENCE_GATE``, which a deterministic
    rule decides but still shadows advisors), never at RULE points and
    never in a way that can affect ``decide``'s return value.

    Every call writes exactly one audit record through ``sink`` before
    returning: a ``judgment`` record (with the executor's decision, every
    advisor's decision or error, and the rule's own ``rule_choice`` where one
    was computed) at an advisory point, or a ``rule`` record everywhere else.
    """

    def __init__(self, executor: ContextualPolicy, advisors: Sequence[tuple[str, ContextualPolicy]],
                 sink: AuditSink, *, advisor_timeout_s: float = 5.0) -> None:
        self.executor = executor
        self.advisors = list(advisors)
        self.sink = sink
        self.advisor_timeout_s = advisor_timeout_s
        # Advisor instances are constructed once and reused across multiple
        # judgment points in the same run (e.g. SELECT_PLAN then
        # EVIDENCE_GATE), and each records its decision on its own shared
        # ``.last`` attribute. A timed-out call cannot be force-cancelled --
        # its thread keeps running `advisor.decide()` in the background --
        # so a fresh pool per call would let that straggler run
        # *concurrently* with a later call on the same advisor instance,
        # racing the straggler's eventual write to `.last` against the
        # later call's own write and read. One persistent, single-worker
        # pool per advisor *instance* instead makes `decide()` calls on
        # that instance strictly serialized: a later call's job cannot even
        # start until every earlier one (including a straggler) has
        # finished, so `.last` is always read right after the call that
        # itself just wrote it, with nothing else running concurrently.
        self._advisor_pools: dict[int, ThreadPoolExecutor] = {}
        self._advisor_pools_lock = threading.Lock()

    def _pool_for(self, advisor: ContextualPolicy) -> ThreadPoolExecutor:
        key = id(advisor)
        with self._advisor_pools_lock:
            pool = self._advisor_pools.get(key)
            if pool is None:
                pool = ThreadPoolExecutor(max_workers=1)
                self._advisor_pools[key] = pool
            return pool

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        decision_point = context.legal.decision_point
        # Shadow the declared advisory points unconditionally (P2b), plus any
        # turn that no rule settled -- P3's duplicate detections and near-tie
        # rankings are judgment points only *sometimes*, so membership in a
        # static set cannot express them. See ADVISORY_POINTS in legal_tools.
        if decision_point in ADVISORY_POINTS or needs_judgment(context.legal):
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

    # -- advisory points (SELECT_PLAN, EVIDENCE_GATE): advisors run in
    # shadow first, executor (rule or operator escalation) still wins --

    def _judgment_step(self, request: dict[str, Any], context: PolicyContext, decision_point: str) -> dict[str, Any]:
        # Non-interference is about LATENCY as well as outcome. Advisors are
        # submitted to their pools first but NOT waited on: submission is
        # O(1), so the executor runs essentially immediately and its latency
        # no longer depends on any advisor. Previously each advisor was
        # awaited in turn *before* the executor ran, so N advisors could
        # delay execution by N x advisor_timeout_s.
        started = [
            (name, advisor, self._pool_for(advisor).submit(advisor.decide, request, context), time.perf_counter())
            for name, advisor in self.advisors
        ]

        # The executor's own decide() is the only call whose result is ever
        # returned, and nothing an advisor does -- raising, hanging, or
        # simply being slow -- can reach it or delay it.
        result = self.executor.decide(request, context)
        executor_decision = self.executor.last

        # Collect the advisors against ONE shared deadline, so the audit
        # record costs at most a single advisor_timeout_s of *waiting* rather
        # than one per advisor. They ran concurrently (a pool per advisor
        # instance), so this usually costs nothing at all. The deadline starts
        # after the executor returns, so an advisor's total budget is the
        # executor's own time plus advisor_timeout_s -- more generous to the
        # advisor, and it leaves executor latency independent of advisors,
        # which is the property that matters.
        deadline = time.monotonic() + self.advisor_timeout_s
        advisor_entries = {
            name: self._collect_advisor(advisor, future, start, deadline)
            for name, advisor, future, start in started
        }

        packet = context.state.to_packet()
        self.sink.write({
            "record": "judgment",
            "run_id": context.state.run_id,
            "state_version": context.state.state_version,
            "decision_point": decision_point,
            "packet_sha256": _canonical_sha256(packet),
            "legal_labels": list(context.legal.labels),
            "rule_choice": context.legal.rule_choice,
            "executor": ({"chooser": executor_decision.chooser, "label": executor_decision.label}
                         if executor_decision is not None else None),
            "advisors": advisor_entries,
            "operator_choice": None,
        })
        return result

    def _collect_advisor(self, advisor: ContextualPolicy, future: "Future", start: float, deadline: float) -> dict:
        """Wait for one already-submitted advisor, bounded by the shared
        ``deadline``. Never blocks on a hung advisor thread: the job stays on
        that advisor's own persistent single-worker pool, so a later call on
        the same instance queues strictly behind the straggler instead of
        racing it for ``.last`` (see ``_pool_for``)."""
        error: str | None = None
        try:
            future.result(timeout=max(0.0, deadline - time.monotonic()))
        except FutureTimeoutError:
            error = f"advisor timed out after {self.advisor_timeout_s:.3f}s"
        except Exception as exc:  # noqa: BLE001 -- any advisor failure is swallowed here
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = int((time.perf_counter() - start) * 1000)
        decision = None if error is not None else getattr(advisor, "last", None)
        return _advisor_entry(decision, fallback_latency_ms=latency_ms, error=error)

    def close(self) -> None:
        """Release the advisor pools without joining their threads, so queued
        work cannot accumulate across a long-lived ladder and an idle pool is
        not held open.

        **Honest limit:** `shutdown(wait=False)` cannot kill a thread that is
        already running -- Python has no such primitive -- and
        `ThreadPoolExecutor` workers are non-daemon, so a genuinely wedged
        advisor call still blocks interpreter exit via the stdlib's atexit
        join. Bounding that needs isolation the thread pool cannot give
        (a subprocess, or an advisor transport with its own hard timeout).
        This method removes the accumulation, not that last case."""
        with self._advisor_pools_lock:
            pools = list(self._advisor_pools.values())
            self._advisor_pools = {}
        for pool in pools:
            pool.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> "ShadowLadder":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.close()
        return False


__all__ = ["AuditSink", "JsonlAuditSink", "MemoryAuditSink", "ShadowLadder"]
