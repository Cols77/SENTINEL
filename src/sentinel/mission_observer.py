"""Typed, best-effort mission telemetry for UI and operator displays.

Observers are deliberately outside the flight control path.  A display, journal,
or projection may subscribe to these events, but an observer exception must never
change a mission's navigation or localisation result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class MissionEvent(Protocol):
    """Marker protocol implemented by every event payload."""


class MissionObserver(Protocol):
    def observe(self, event: MissionEvent) -> None:
        """Consume an event. Implementations may raise; ``notify`` isolates it."""


@dataclass(frozen=True)
class RunnerLifecycleEvent:
    run_id: str = ""
    stage: str = ""
    status: str = ""
    message: str | None = None
    runner: Any | None = None


@dataclass(frozen=True)
class StopProgressEvent:
    run_id: str = ""
    stop_index: int | None = None
    stop: str | None = None
    status: str = ""
    payload: dict[str, Any] | None = None
    option_id: str | None = None
    x_cm: float | None = None
    y_cm: float | None = None


@dataclass(frozen=True)
class PoseEstimateEvent:
    run_id: str = ""
    pose: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    estimate: Any | None = None


@dataclass(frozen=True)
class PoseHealthEvent:
    run_id: str = ""
    health: dict[str, Any] | None = None
    value: Any | None = None


@dataclass(frozen=True)
class FrameCapturedEvent:
    run_id: str = ""
    frame_id: str | None = None
    artifact_id: str | None = None
    image_path: str | None = None
    sha256: str | None = None
    pose: dict[str, Any] | None = None


@dataclass(frozen=True)
class DetectorResultEvent:
    run_id: str = ""
    frame_id: str | None = None
    status: str = ""
    detections: tuple[dict[str, Any], ...] = ()
    latency_ms: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class MergeEvent:
    run_id: str = ""
    sweep_id: str | None = None
    objects: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ReportEvent:
    run_id: str = ""
    status: str = ""
    body: str | None = None
    counts: dict[str, Any] | None = None


@dataclass(frozen=True)
class AbortEvent:
    run_id: str = ""
    stage: str = ""
    reason: str = ""


@dataclass(frozen=True)
class PublicEscalationEvent:
    run_id: str = ""
    point: str = ""
    reason: str = ""
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class NoOpMissionObserver:
    """Default observer used when telemetry is not requested."""

    def observe(self, event: MissionEvent) -> None:
        return None


def notify(observer: MissionObserver | None, event: MissionEvent) -> None:
    """Best-effort notification; observer/display failures are isolated."""
    if observer is None:
        return
    try:
        observer.observe(event)
    except Exception:
        # Telemetry is diagnostic. Never let it interrupt flight or localisation.
        return None
