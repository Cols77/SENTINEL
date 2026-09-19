"""Semantic checks that JSON Schema cannot express for the WP0 contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .schemas import ContractValidationError, as_wire_dict, validate_document


class ContractViolation(ContractValidationError):
    """A stable, machine-readable semantic contract rejection."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def validate_artifact_hash(request: Mapping[str, Any]) -> Mapping[str, Any]:
    """Require the request hash to equal the nested selected artifact hash."""
    artifact = request.get("artifact")
    if not isinstance(artifact, Mapping) or request.get("artifact_sha256") != artifact.get("sha256"):
        raise ContractViolation("ARTIFACT_HASH_MISMATCH", "request artifact_sha256 differs from artifact.sha256")
    return request


def validate_bbox(bbox: Any) -> Any:
    """Validate normalized ``x1,y1,x2,y2`` geometry, including positive area."""
    if bbox is None:
        return bbox
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ContractViolation("INVALID_BBOX", "bbox must contain four normalized coordinates")
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise ContractViolation("INVALID_BBOX", "bbox requires 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1")
    return bbox


def validate_perception(document: Mapping[str, Any]) -> Mapping[str, Any]:
    validate_document(document)
    if document.get("wire_type") == "perception_request":
        validate_artifact_hash(document)
    if document.get("wire_type") == "perception_result":
        validate_bbox(document.get("bbox_xyxy_normalized"))
    return document


_PREFLIGHT_CHECKS = {
    "WIFI_INTERFACE",
    "DRONE_LINK",
    "BATTERY_TELEMETRY",
    "LIVE_VIDEO",
    "LAYOUT_MARKERS",
    "BOUNDED_AREA",
    "HEIGHT_ENVELOPE",
    "EMERGENCY_LATCH",
}


def validate_preflight_report(report: Mapping[str, Any]) -> Mapping[str, Any]:
    """Enforce the immutable report's exact check set and aggregate result."""
    if not isinstance(report, Mapping):
        raise ContractViolation("INVALID_PREFLIGHT_REPORT", "report must be an object")
    checks = report.get("checks")
    names = [check.get("name") for check in checks] if isinstance(checks, list) else []
    if set(names) != _PREFLIGHT_CHECKS or len(names) != len(_PREFLIGHT_CHECKS):
        raise ContractViolation("INVALID_PREFLIGHT_REPORT", "report must contain exactly the eight named checks")
    if report.get("passed") != all(check.get("passed") is True for check in checks):
        raise ContractViolation("INVALID_PREFLIGHT_REPORT", "passed must equal the conjunction of check results")
    return report


def _orientation(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, c):
    return min(a[0], c[0]) <= b[0] <= max(a[0], c[0]) and min(a[1], c[1]) <= b[1] <= max(a[1], c[1])


def _segments_intersect(a, b, c, d):
    o1, o2, o3, o4 = (_orientation(a, b, c), _orientation(a, b, d), _orientation(c, d, a), _orientation(c, d, b))
    if o1 == 0 and _on_segment(a, c, b):
        return True
    if o2 == 0 and _on_segment(a, d, b):
        return True
    if o3 == 0 and _on_segment(c, a, d):
        return True
    if o4 == 0 and _on_segment(c, b, d):
        return True
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _validate_polygon(polygon: Any) -> None:
    if not isinstance(polygon, list) or len(polygon) < 4 or polygon[0] != polygon[-1]:
        raise ContractViolation("INVALID_MOUNTAIN_GEOMETRY", "polygon must be a closed ring")
    for point in polygon:
        if not isinstance(point, list) or len(point) != 2 or not all(0 <= value <= 100 for value in point):
            raise ContractViolation("INVALID_MOUNTAIN_GEOMETRY", "polygon point is outside local 100x100 bounds")
    edges = [(polygon[i], polygon[i + 1]) for i in range(len(polygon) - 1)]
    for first, (a, b) in enumerate(edges):
        for second in range(first + 1, len(edges)):
            if second == first + 1 or (first == 0 and second == len(edges) - 1):
                continue
            if _segments_intersect(a, b, edges[second][0], edges[second][1]):
                raise ContractViolation("INVALID_MOUNTAIN_GEOMETRY", "polygon ring self-intersects")


def validate_mountain_fixture(mountain: Mapping[str, Any]) -> Mapping[str, Any]:
    validate_document(mountain, schema="mountain")
    expected = [
        "LEFT_ALLEY_FRONT", "LEFT_ALLEY_MIDDLE", "LEFT_ALLEY_BACK",
        "RIGHT_ALLEY_FRONT", "RIGHT_ALLEY_MIDDLE", "RIGHT_ALLEY_BACK",
    ]
    actual = [section["section_id"] for section in mountain["sections"]]
    if actual != expected:
        raise ContractViolation("INVALID_MOUNTAIN_FIXTURE", "fixture must contain the ordered six-zone catalogue")
    for section in mountain["sections"]:
        _validate_polygon(section["polygon"])
    modes = {mode["mode"]: mode for mode in mountain["layout_modes"]}
    if modes.get("ROUTE_ONLY_2_ZONE", {}).get("active_zone_ids") != ["LEFT_ALLEY_MIDDLE", "RIGHT_ALLEY_MIDDLE"]:
        raise ContractViolation("INVALID_MOUNTAIN_FIXTURE", "route-only mode must contain exactly the two middle zones")
    if modes.get("ROUTE_ONLY_2_ZONE", {}).get("marker_verified") is not False:
        raise ContractViolation("INVALID_MOUNTAIN_FIXTURE", "route-only mode cannot claim marker verification")
    return mountain


def validate_event_stream(events: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    previous_run = None
    previous_sequence = 0
    for event in events:
        try:
            validate_document(event)
        except ContractValidationError as exc:
            raise ContractViolation("INVALID_CONTRACT", str(exc)) from exc
        if previous_run is not None and event["run_id"] != previous_run:
            raise ContractViolation("EVENT_SEQUENCE_INVALID", "event stream contains multiple runs")
        if event["sequence_no"] <= previous_sequence:
            raise ContractViolation("EVENT_SEQUENCE_INVALID", "event sequence must increase monotonically")
        previous_run = event["run_id"]
        previous_sequence = event["sequence_no"]
    return events


def replay_after_cursor(events: list[Mapping[str, Any]], after_sequence: int, limit: int | None = None):
    if after_sequence < 0:
        raise ContractViolation("REPLAY_NOT_ALLOWED", "restart cursor cannot be negative")
    ordered = validate_event_stream(events)
    replay = [event for event in ordered if event["sequence_no"] > after_sequence]
    return replay if limit is None else replay[:limit]


def ensure_expected_state_version(expected: int | None, current: int) -> None:
    if expected is not None and expected != current:
        raise ContractViolation("STALE_STATE_VERSION", f"expected {expected}, current {current}")


def _fingerprint(command: Mapping[str, Any]) -> tuple[Any, ...]:
    payload = json.dumps(as_wire_dict(command.get("payload", {})), sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return command.get("command_type"), command.get("expected_state_version"), payload_hash


class IdempotencyRegistry:
    """In-memory contract model for run-scoped idempotency uniqueness."""

    def __init__(self):
        self._records: dict[tuple[str, str, str, str], tuple[tuple[Any, ...], Any]] = {}

    @staticmethod
    def scope(command: Mapping[str, Any]) -> tuple[str, str, str, str]:
        return (command["run_id"], command["actor_type"], command["actor_id"], command["idempotency_key"])

    def remember(self, command: Mapping[str, Any], result: Any) -> Any:
        key = self.scope(command)
        fingerprint = _fingerprint(command)
        prior = self._records.get(key)
        if prior is None:
            self._records[key] = (fingerprint, result)
            return result
        if prior[0] != fingerprint:
            raise ContractViolation("IDEMPOTENCY_KEY_CONFLICT", "scope was reused with a different command fingerprint")
        return prior[1]


_ALLOWED_OPERATION_TRANSITIONS = {
    "PENDING": {"DISPATCHED", "IN_DOUBT"},
    "DISPATCHED": {"SUCCEEDED", "FAILED", "IN_DOUBT"},
    "IN_DOUBT": {"SUCCEEDED", "FAILED"},
    "SUCCEEDED": set(),
    "FAILED": set(),
}


def validate_operation_transition(current: str, target: str) -> str:
    if target not in _ALLOWED_OPERATION_TRANSITIONS.get(current, set()):
        raise ContractViolation("INVALID_OPERATION_TRANSITION", f"{current} -> {target} is not legal")
    return target


@dataclass
class OperationStateMachine:
    status: str

    def transition(self, target: str) -> str:
        self.status = validate_operation_transition(self.status, target)
        return self.status


def validate_emergency_signal(signal: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        validate_document(signal)
    except ContractValidationError as exc:
        raise ContractViolation("INVALID_CONTRACT", str(exc)) from exc
    return signal
