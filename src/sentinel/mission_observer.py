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


def runner_started(observer: MissionObserver | None, run_id: str, runner: Any) -> None:
    """Required safety admission hook; unlike ``notify``, failures propagate."""
    hook = getattr(observer, "runner_started", None)
    if hook is not None:
        hook(run_id, runner)


def runner_finished(observer: MissionObserver | None, run_id: str, runner: Any) -> None:
    """Required safety release hook; unlike display telemetry, it is not swallowed."""
    hook = getattr(observer, "runner_finished", None)
    if hook is not None:
        hook(run_id, runner)


def pose_summary(estimate: Any) -> dict[str, Any]:
    report = getattr(estimate, "report", estimate)
    return {
        "report_id": getattr(report, "report_id", None),
        "run_id": getattr(report, "run_id", None),
        "point": list(getattr(report, "point", ())),
        "heading_deg": getattr(report, "heading_deg", None),
        "method": getattr(report, "method", None),
        "reprojection_error_px": getattr(report, "reprojection_error_px", None),
    }


def evidence_summary(evidence: Any) -> dict[str, Any]:
    if evidence is None:
        return {"artifact_id": None, "sha256": None, "width_px": None, "height_px": None, "tags": []}
    tags = []
    for tag in getattr(evidence, "tags", ()):
        tags.append({
            "tag_id": getattr(tag, "tag_id", None),
            "corners_xy_normalized": [list(point) for point in getattr(tag, "corners_xy_normalized", ())],
            "status": getattr(tag, "status", None),
            "reason_code": getattr(tag, "reason_code", None),
            "zone_id": getattr(tag, "zone_id", None),
            "reprojection_error_px": getattr(tag, "reprojection_error_px", None),
        })
    return {
        "artifact_id": getattr(evidence, "artifact_id", None),
        "sha256": getattr(evidence, "sha256", None),
        "width_px": getattr(evidence, "width_px", None),
        "height_px": getattr(evidence, "height_px", None),
        "tags": tags,
    }


def notify(observer: MissionObserver | None, event: MissionEvent) -> None:
    """Best-effort notification; observer/display failures are isolated."""
    if observer is None:
        return
    try:
        observer.observe(event)
    except Exception:
        # Telemetry is diagnostic. Never let it interrupt flight or localisation.
        return None
