"""Schema loading, validation, and small frozen wire-record wrappers for WP0."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, ClassVar, Mapping

from jsonschema import Draft202012Validator, FormatChecker, RefResolver


PACKAGE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_DIR.parents[2]
CONTRACTS_DIR = REPOSITORY_ROOT / "docs" / "superpowers" / "specs" / "contracts"
ROOT_SCHEMA_PATH = CONTRACTS_DIR / "sentinel-v0-contract.schema.json"
WIRE_SCHEMA_PATH = CONTRACTS_DIR / "sentinel-v0-wire.schema.json"
MOUNTAIN_SCHEMA_PATH = CONTRACTS_DIR / "mountain-v0.schema.json"

ROOT_SCHEMA_URI = "https://sentinel.local/contracts/sentinel-v0-contract.schema.json"
WIRE_SCHEMA_URI = "https://sentinel.local/contracts/sentinel-v0-wire.schema.json"
MOUNTAIN_SCHEMA_URI = "https://sentinel.local/contracts/mountain-v0.schema.json"


class ContractValidationError(ValueError):
    """Raised when a document fails its normative JSON Schema."""

    code = "INVALID_CONTRACT"

    def __init__(self, message: str, errors: list[Any] | None = None):
        super().__init__(message)
        self.errors = errors or []


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema(name: str = "wire") -> dict[str, Any]:
    paths = {
        "root": ROOT_SCHEMA_PATH,
        "contract": ROOT_SCHEMA_PATH,
        "wire": WIRE_SCHEMA_PATH,
        "mountain": MOUNTAIN_SCHEMA_PATH,
    }
    try:
        return _load_json(paths[name])
    except KeyError as exc:
        raise ValueError(f"unknown schema: {name}") from exc


def _validator(name: str) -> Draft202012Validator:
    schema = load_schema(name)
    wire = load_schema("wire")
    root = load_schema("root")
    mountain = load_schema("mountain")
    store = {
        ROOT_SCHEMA_URI: root,
        WIRE_SCHEMA_URI: wire,
        MOUNTAIN_SCHEMA_URI: mountain,
        "sentinel-v0-wire.schema.json": wire,
        "sentinel-v0-contract.schema.json": root,
        "mountain-v0.schema.json": mountain,
    }
    if name in {"root", "contract"}:
        schema = {
            **schema,
            "$ref": WIRE_SCHEMA_URI,
            "type": "object",
            "required": ["wire_type"],
        }
    return Draft202012Validator(
        schema,
        resolver=RefResolver.from_schema(schema, store=store),
        format_checker=FormatChecker(),
    )


def iter_validation_errors(document: Any, schema: str = "root"):
    """Yield sorted JSON Schema errors for *document*."""
    validator = _validator(schema)
    yield from sorted(validator.iter_errors(document), key=lambda error: list(error.path))


def validate_document(
    document: Any, schema: str = "root", *, raise_on_error: bool = True
) -> list[Any]:
    """Validate a public document and return errors (or raise on the first batch)."""
    errors = list(iter_validation_errors(document, schema))
    if errors and raise_on_error:
        detail = "; ".join(error.message for error in errors[:4])
        raise ContractValidationError(f"{schema} validation failed: {detail}", errors)
    return errors


@dataclass(frozen=True)
class _WireRecord:
    wire_type: ClassVar[str]

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, LocalPoint):
                return [value.x, value.y]
            if is_dataclass(value):
                return {field.name: convert(getattr(value, field.name)) for field in fields(value)}
            if isinstance(value, tuple):
                return [convert(item) for item in value]
            if isinstance(value, list):
                return [convert(item) for item in value]
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            return value

        return convert(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class StateSnapshot(_WireRecord):
    run_id: str
    state_version: int
    run_status: str
    agent_phase: str
    legal_operator_actions: list[str]
    active_operation: dict[str, Any] | None
    latest_event_sequence: int
    mountain_snapshot_version: int
    updated_at: str
    contract_version: str = "sentinel.v0"
    wire_type: ClassVar[str] = "state_snapshot"


@dataclass(frozen=True)
class LocalPoint(_WireRecord):
    x: float
    y: float
    wire_type: ClassVar[str] = "local_point"

    def to_dict(self) -> list[float]:
        return [self.x, self.y]


@dataclass(frozen=True)
class LocalPose(_WireRecord):
    point: LocalPoint | list[float]
    heading_deg: float | None
    observed_at: str
    source_kind: str
    wire_type: ClassVar[str] = "local_pose"


@dataclass(frozen=True)
class SectionState(_WireRecord):
    section_id: str
    name: str
    polygon: list[list[float]]
    status: str
    coverage_percent: float | None = None
    last_observation_id: str | None = None
    stale: bool = False
    wire_type: ClassVar[str] = "section_state"


@dataclass(frozen=True)
class HazardMarker(_WireRecord):
    target_id: str
    section_id: str
    perception_result_id: str
    point: LocalPoint | list[float] | None
    status: str
    evidence_artifact_id: str
    wire_type: ClassVar[str] = "hazard_marker"


@dataclass(frozen=True)
class MountainStateSnapshot(_WireRecord):
    run_id: str
    snapshot_version: int
    as_of: str
    sections: list[SectionState | dict[str, Any]]
    source_kind: str
    stale: bool
    drone_pose: LocalPose | dict[str, Any] | None
    flight_path: list[LocalPose | dict[str, Any]]
    hazards: list[HazardMarker | dict[str, Any]]
    active_mission_id: str | None
    active_task_id: str | None
    contract_version: str = "sentinel.v0"
    wire_type: ClassVar[str] = "mountain_snapshot"


@dataclass(frozen=True)
class PerceptionRequest(_WireRecord):
    request_id: str
    run_id: str
    observation_id: str
    artifact: dict[str, Any]
    artifact_sha256: str
    target_class: str
    judged_path: bool
    provider_profile_id: str
    station_capture: dict[str, Any]
    contract_version: str = "sentinel.v0"
    wire_type: ClassVar[str] = "perception_request"


@dataclass(frozen=True)
class OperationRecord(_WireRecord):
    operation_id: str
    run_id: str
    mission_id: str
    command_id: str
    worker_id: str
    idempotency_key: str
    status: str
    dispatch_recorded_at: str | None = None
    reconciled_at: str | None = None
    contract_version: str = "sentinel.v0"
    wire_type: ClassVar[str] = "operation_record"


@dataclass(frozen=True)
class EmergencySignal(_WireRecord):
    signal_id: str
    worker_id: str
    latched_at: str
    source_kind: str
    reason_code: str
    run_id: str | None = None
    mission_id: str | None = None
    contract_version: str = "sentinel.v0"
    wire_type: ClassVar[str] = "emergency_signal"


@dataclass(frozen=True)
class ArtifactRef(_WireRecord):
    artifact_id: str
    run_id: str
    media_type: str
    byte_size: int
    sha256: str
    storage_key: str
    created_at: str
    source_kind: str
    contract_version: str = "sentinel.v0"
    wire_type: ClassVar[str] = "artifact_ref"


def as_wire_dict(value: Any) -> Any:
    """Convert a dataclass or mapping into JSON-compatible wire data."""
    if isinstance(value, _WireRecord):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {key: as_wire_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_wire_dict(item) for item in value]
    return value
