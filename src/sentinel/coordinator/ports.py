"""Backend-independent coordinator protocol boundaries.

The protocols deliberately contain no provider, UI, or hardware imports. Wire
records are represented as mappings until their WP0 wrappers are available.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

WireDocument = Mapping[str, Any]


@runtime_checkable
class CoordinatorPort(Protocol):
    """The sole command and read boundary for coordinator-owned state."""

    def submit(self, command: WireDocument) -> WireDocument: ...

    def read_state(self, run_id: str) -> WireDocument: ...

    def read_events(self, run_id: str, after_sequence: int, limit: int) -> list[WireDocument]: ...

    def read_mountain(self, run_id: str) -> WireDocument: ...

    def read_evidence(self, artifact_id: str) -> WireDocument: ...

    def read_approval(self, task_id: str) -> WireDocument: ...

    def read_plan(self, plan_id: str) -> WireDocument: ...

    def read_preflight(self, run_id: str) -> WireDocument: ...

    def read_hazard(self, target_id: str) -> WireDocument: ...

    def read_perception(self, result_id: str) -> WireDocument: ...

    def read_video_status(self, run_id: str) -> WireDocument: ...

    def open_live_video(self, run_id: str) -> WireDocument: ...


@runtime_checkable
class WorkerPort(Protocol):
    """Typed worker side-effect and reconciliation boundary."""

    def dispatch(self, request: WireDocument) -> WireDocument: ...

    def reconcile(self, operation_id: str, idempotency_key: str) -> WireDocument: ...

    def read_video_status(self, run_id: str) -> WireDocument: ...

    def open_live_video(self, run_id: str) -> WireDocument: ...


@runtime_checkable
class PerceptionPort(Protocol):
    """Typed perception ingress boundary."""

    def observe(self, request: WireDocument) -> WireDocument: ...
