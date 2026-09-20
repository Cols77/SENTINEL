"""Non-interference includes LATENCY, not just the returned decision.

The pre-existing non-interference tests prove an advisor cannot change what
the executor decides. They do not prove an advisor cannot *delay* it — and it
could: advisors were awaited one at a time before the executor ran, so N slow
advisors postponed execution by up to N x advisor_timeout_s. These tests pin
the two properties that fix restored.
"""
from __future__ import annotations

import time

from sentinel.coordinator.legal_tools import LegalSet
from sentinel.coordinator.policy import PolicyContext
from sentinel.coordinator.shadow import MemoryAuditSink, ShadowLadder

from .test_shadow import _dummy_state, _evidence_gate_legal, _request

TIMEOUT_S = 0.3
ADVISOR_SLEEP_S = 5.0          # far longer than the timeout; never completes in time
ADVISOR_COUNT = 3


class _GlacialAdvisor:
    """Always exceeds the timeout. Distinct instances get distinct pools."""

    def __init__(self) -> None:
        self.last = None
        self.calls = 0

    def decide(self, request, context):
        self.calls += 1
        time.sleep(ADVISOR_SLEEP_S)
        return {}


class _TimestampingExecutor:
    """Records when it was actually called, so we can measure the delay the
    advisors imposed on execution rather than on the whole turn."""

    def __init__(self, legal: LegalSet) -> None:
        self.legal = legal
        self.called_at: float | None = None
        self.last = None

    def decide(self, request, context):
        self.called_at = time.perf_counter()
        from sentinel.coordinator.policies import Decision, RulePolicy

        policy = RulePolicy()
        result = policy.decide(request, context)
        self.last = policy.last
        assert isinstance(self.last, Decision)
        return result


def _judgment_context() -> tuple[PolicyContext, LegalSet]:
    """A two-candidate EVIDENCE_GATE turn with no rule_choice — a real
    judgment point (the duplicate-detection shape), so ShadowLadder takes its
    advisor path and RulePolicy escalates rather than acting."""
    legal = _evidence_gate_legal(rule_choice=None)
    return PolicyContext(_dummy_state(), legal), legal


def test_executor_runs_immediately_despite_slow_advisors():
    """The executor must be called without waiting for any advisor. Before the
    fix it was called only after every advisor had timed out in turn, i.e.
    after ADVISOR_COUNT x TIMEOUT_S."""
    context, legal = _judgment_context()
    executor = _TimestampingExecutor(legal)
    advisors = [(f"slow-{i}", _GlacialAdvisor()) for i in range(ADVISOR_COUNT)]
    ladder = ShadowLadder(executor, advisors, MemoryAuditSink(), advisor_timeout_s=TIMEOUT_S)
    try:
        start = time.perf_counter()
        ladder.decide(_request(), context)
        delay_before_execution = executor.called_at - start
        # Generous bound: submission is O(1), so this should be ~0. Anything
        # approaching a single timeout means the executor is waiting again.
        assert delay_before_execution < TIMEOUT_S, (
            f"executor waited {delay_before_execution:.3f}s on advisors "
            f"(timeout {TIMEOUT_S}s x {ADVISOR_COUNT} advisors)")
    finally:
        ladder.close()


def test_total_turn_cost_is_one_timeout_not_one_per_advisor():
    """Advisors run concurrently against a single shared deadline, so the audit
    record costs at most one advisor_timeout_s regardless of advisor count."""
    context, legal = _judgment_context()
    executor = _TimestampingExecutor(legal)
    advisors = [(f"slow-{i}", _GlacialAdvisor()) for i in range(ADVISOR_COUNT)]
    ladder = ShadowLadder(executor, advisors, MemoryAuditSink(), advisor_timeout_s=TIMEOUT_S)
    try:
        start = time.perf_counter()
        ladder.decide(_request(), context)
        elapsed = time.perf_counter() - start
        # Sequential waiting would cost ADVISOR_COUNT x TIMEOUT_S (0.9s here);
        # the shared deadline costs one (0.3s). The midpoint separates them
        # with room for scheduling noise on a loaded machine.
        assert elapsed < TIMEOUT_S * (ADVISOR_COUNT + 1) / 2, (
            f"turn took {elapsed:.3f}s; expected ~{TIMEOUT_S}s, not "
            f"{TIMEOUT_S * ADVISOR_COUNT:.3f}s")
    finally:
        ladder.close()


def test_slow_advisors_are_recorded_as_timed_out_and_never_change_the_decision():
    """The latency fix must not weaken the original guarantee."""
    context, legal = _judgment_context()
    executor = _TimestampingExecutor(legal)
    advisors = [(f"slow-{i}", _GlacialAdvisor()) for i in range(ADVISOR_COUNT)]
    sink = MemoryAuditSink()
    ladder = ShadowLadder(executor, advisors, sink, advisor_timeout_s=TIMEOUT_S)
    try:
        result = ladder.decide(_request(), context)
        call = result["tool_calls"][0]
        assert call["tool_name"] == "finish"          # the executor escalated, as RulePolicy must
        record = sink.records[-1]
        assert record["record"] == "judgment"
        assert record["executor"]["chooser"] == "OPERATOR"
        for name, _ in advisors:
            assert "timed out" in record["advisors"][name]["error"]
            assert record["advisors"][name]["label"] is None
    finally:
        ladder.close()


def test_close_releases_the_pools_and_is_idempotent():
    context, legal = _judgment_context()
    ladder = ShadowLadder(_TimestampingExecutor(legal), [("slow", _GlacialAdvisor())],
                          MemoryAuditSink(), advisor_timeout_s=TIMEOUT_S)
    ladder.decide(_request(), context)
    assert ladder._advisor_pools
    ladder.close()
    assert ladder._advisor_pools == {}
    ladder.close()  # must not raise


def test_shadow_ladder_works_as_a_context_manager():
    context, legal = _judgment_context()
    with ShadowLadder(_TimestampingExecutor(legal), [], MemoryAuditSink()) as ladder:
        ladder.decide(_request(), context)
    assert ladder._advisor_pools == {}
