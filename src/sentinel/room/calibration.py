"""Offline calibration-profile generation, holdout promotion, and the fixed-route
recording flow for the room agent (Plan 06 Task 3, count-and-return scope).

Every function here except ``record`` is a pure, offline transformation over
already-collected evidence: parsing, canonicalising, aggregating and hashing documents.
``record`` drives one controller-owned fixed route through an *injected* executor --
nothing in this module ever constructs a provider, drone client or network object
itself. ``record`` cannot invoke Jev or any detector: the only side-effecting object it
touches is whatever ``executor_factory`` returns, and the executor's public surface
(``start``/``execute``/``return_home``/``reanchor``/``land``/``close``) has no
Jev/detector call anywhere in it. On any exception after ``start`` is invoked (start
itself included), ``record`` makes a best-effort ``land`` call before ``close`` --
never relies on ``close`` alone to put the aircraft down.

A trial's evidence is only as trustworthy as its provenance: ``record`` marks a trial
"HARDWARE" only when the injected executor declares an explicit
``hardware_provenance`` (a ``HardwareProvenance``) bound to the route's own
aircraft/firmware, and "SIMULATION" otherwise. ``fit`` refuses to call a profile
CALIBRATION unless every training trial is "HARDWARE" -- anything else is
SIMULATION_ONLY, and SIMULATION_ONLY can never be promoted. ``promote`` never trusts a
candidate file as written: it re-derives the candidate from the same predeclared
training evidence in the evidence index and requires an exact match, refuses any
holdout trial whose measured content (by content hash, not by run id) duplicates a
training trial's, and refuses holdout evidence that is not itself "HARDWARE".

Reuses ``sentinel.room.artifacts`` for canonical JSON and atomic writes (one
definition of "canonical form" in this packet, not a second one that could drift), and
``sentinel.cube.checkpoint``'s ``plan_checkpoint``/``CheckpointPlan``/``HomeDefinition``
(the Plan 02 connected-checkpoint command graph) so a calibration route is executed by
the exact same command graph a live flight would use, not a parallel, unverified one.

Battery-burn/timing and pose-drift/recovery calibration (the ``flight`` profile kind)
are fully implemented. Tracker (following/yaw-tracking) calibration is Plan 05 and is
deferred by operator ruling (2026-09-23, count-and-return only): the ``tracker`` kind
is kept as an accepted enum value and schema slot everywhere a ``kind`` is validated,
but ``fit``/``promote`` refuse it outright with an explicit deferral message.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Callable, Mapping, Sequence

from sentinel.cube.checkpoint import CheckpointPlan, CommandLimits, HomeDefinition, plan_checkpoint
from sentinel.models import Position, Telemetry

from . import artifacts
from .artifacts import atomic_write_bytes, authorization_digest, canonical_bytes

SCHEMA_VERSION = "room-calibration-profile-v1"

_RECORD_MANIFEST_SCHEMA_VERSION = "room-calibration-record-manifest-v1"
_TRIAL_SCHEMA_VERSION = "room-calibration-trial-v1"
_ROUTE_SCHEMA_VERSION = "room-calibration-route-v1"
_EVIDENCE_INDEX_SCHEMA_VERSION = "room-calibration-evidence-index-v1"

PROFILE_KINDS = ("flight", "tracker")
_PROFILE_KINDS = frozenset(PROFILE_KINDS)
_PROFILE_STATES = frozenset({"SIMULATION_ONLY", "CALIBRATION", "PROMOTED"})
_TRIAL_ROLES = frozenset({"training", "holdout"})

#: The only routes this build recognises as controller-owned fixed calibration
#: routes, mapped to the evidence kind each one supplies. ``record`` refuses any
#: route document declaring a route_id outside this map: an operator cannot smuggle
#: an arbitrary, uncontrolled route through ``--route``.
_ALLOWED_ROUTE_IDS: Mapping[str, str] = {
    "return-route": "forced_return",
    "tag-free-hover": "tag_free",
}

TRACKER_DEFERRED_MESSAGE = "tracker calibration deferred (Plan 05)"

# Conservative constants for the flight battery bound. Not calibrated per-site; they
# widen the worst *observed* rate the same way for every fit, which is what makes the
# bound "worst observed plus a declared margin and quantization allowance" rather than
# a bare average of trials. The quantization allowance is an absolute-percent reading
# error, so it is folded into each trial's own rate (allowance / that trial's own
# elapsed time) before taking the worst rate across trials -- never added to a rate a
# second time as a bare percent (that was a unit bug; see BatteryBound below).
_BATTERY_MARGIN_PERCENT_PER_S = 0.02
_BATTERY_QUANTIZATION_ALLOWANCE_PERCENT = 1.0
_BATTERY_LANDING_FLOOR_PERCENT = 15.0

# Pose envelope combination buckets. A holdout leg's wall-clock timing will not land on
# the exact float a training leg happened to report, so the envelope key rounds each
# dimension to a fixed bucket before matching -- still "only sampled combinations, no
# interpolation" (a holdout combination outside every trained bucket is still refused),
# just without demanding bit-identical timing between two flights of the "same" leg.
_DISTANCE_BUCKET_CM = 5.0
_TIME_BUCKET_S = 0.5
_YAW_BUCKET_DEG = 5.0

_TRIAL_PROVENANCES = frozenset({"HARDWARE", "SIMULATION"})


class CalibrationError(ValueError):
    """A calibration manifest, route, trial, evidence index or profile is refused."""


@dataclass(frozen=True)
class HardwareProvenance:
    """A controller-signed declaration that a trial was flown on real hardware.

    ``record`` never constructs this itself: it can only come from whatever
    ``executor_factory`` returns, as an ``executor.hardware_provenance`` attribute.
    Anything else -- absent, the wrong type, or bound to a different aircraft/firmware
    than the route actually being flown -- marks the trial ``SIMULATION``. An executor
    cannot claim hardware provenance for a route it isn't bound to, and a caller cannot
    get ``HARDWARE`` provenance by accident: it has to be deliberately declared.
    """

    aircraft_id: str
    firmware_version: str
    controller_signoff: str

    def __post_init__(self) -> None:
        for name in ("aircraft_id", "firmware_version", "controller_signoff"):
            if not getattr(self, name):
                raise CalibrationError(f"hardware provenance requires a nonempty {name}")


def _trial_provenance(executor: object, route: "CalibrationRoute") -> str:
    declared = getattr(executor, "hardware_provenance", None)
    if (
        isinstance(declared, HardwareProvenance)
        and declared.aircraft_id == route.aircraft_id
        and declared.firmware_version == route.firmware_version
    ):
        return "HARDWARE"
    return "SIMULATION"


# ---------------------------------------------------------------------------
# preview: bind target_mode/trial_role/split_id into the canonical authorization
# digest, without touching the filesystem.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationRecordManifest:
    """The immutable, digest-bound description of one record-mode trial slot."""

    schema_version: str
    target_mode: str
    trial_role: str
    split_id: str

    def __post_init__(self) -> None:
        if self.schema_version != _RECORD_MANIFEST_SCHEMA_VERSION:
            raise CalibrationError(f"unsupported schema_version {self.schema_version!r}")
        if self.target_mode != "record":
            raise CalibrationError(f"unsupported target_mode {self.target_mode!r}")
        if self.trial_role not in _TRIAL_ROLES:
            raise CalibrationError(f"unknown trial_role {self.trial_role!r}")
        if not self.split_id:
            raise CalibrationError("split_id is required")

    def to_document(self) -> dict:
        body = {field.name: getattr(self, field.name) for field in fields(self)}
        digest = authorization_digest(body)
        return {**body, artifacts.DIGEST_FIELD: digest}

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_document())


_RECORD_MANIFEST_FIELDS = frozenset(field.name for field in fields(CalibrationRecordManifest))


def preview(*, target_mode: str, trial_role: str, split_id: str) -> bytes:
    """Bind target_mode/trial_role/split_id into the canonical authorization digest.

    Returns the exact canonical bytes to print (and optionally persist via
    ``--manifest-out``). Never touches the filesystem itself.
    """
    manifest = CalibrationRecordManifest(
        schema_version=_RECORD_MANIFEST_SCHEMA_VERSION, target_mode=target_mode,
        trial_role=trial_role, split_id=split_id,
    )
    return manifest.canonical_bytes()


def parse_record_manifest(raw: bytes) -> CalibrationRecordManifest:
    """Parse, then fail closed: unknown/missing fields, wrong schema_version, digest mismatch."""
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise CalibrationError("manifest document must be a JSON object")
    if document.get("schema_version") != _RECORD_MANIFEST_SCHEMA_VERSION:
        raise CalibrationError(f"unsupported schema_version {document.get('schema_version')!r}")
    known = _RECORD_MANIFEST_FIELDS | {artifacts.DIGEST_FIELD}
    unknown = set(document.keys()) - known
    if unknown:
        raise CalibrationError(f"unknown manifest field(s): {sorted(unknown)}")
    missing = _RECORD_MANIFEST_FIELDS - set(document.keys())
    if missing:
        raise CalibrationError(f"missing manifest field(s): {sorted(missing)}")
    supplied_digest = document.get(artifacts.DIGEST_FIELD)
    if not isinstance(supplied_digest, str):
        raise CalibrationError("manifest is missing its authorization_digest")
    body = {key: value for key, value in document.items() if key != artifacts.DIGEST_FIELD}
    recomputed = authorization_digest(body)
    if recomputed != supplied_digest:
        raise CalibrationError("manifest authorization_digest does not match its content")
    try:
        return CalibrationRecordManifest(**body)
    except TypeError as exc:
        raise CalibrationError(f"manifest fields do not match CalibrationRecordManifest: {exc}") from exc


# ---------------------------------------------------------------------------
# Controller-owned fixed calibration routes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationCheckpointSpec:
    checkpoint_id: str
    target: Position
    speed_cm_s: int
    looks_deg: tuple[int, ...] = ()
    capture_count: int = 0
    #: This is the controller-owned forced-return leg: ``record`` flies it through
    #: ``executor.return_home`` (the RETURNING latch), never a plain ``execute``, so a
    #: promoted profile's forced-return evidence actually exercised the return path.
    is_return_leg: bool = False
    #: This leg ends with a tag observation: ``record`` flies it through
    #: ``executor.reanchor``, and pose drift is measured as the reanchor correction
    #: (propagated belief vs. the anchor-corrected ground truth), never the estimator's
    #: own propagated pose compared to the commanded target.
    reanchor: bool = False


@dataclass(frozen=True)
class CalibrationRoute:
    """A controller-owned fixed calibration route: one specific site/aircraft binding
    plus the sequence of checkpoints ``record`` flies, unmodified, start to land."""

    schema_version: str
    route_id: str
    route_kind: str
    site_sha256: str
    aircraft_id: str
    firmware_version: str
    camera_sha256: str
    tag_map_sha256: str
    home: HomeDefinition
    checkpoints: tuple[CalibrationCheckpointSpec, ...]


def _position_from_document(document: object) -> Position:
    if not isinstance(document, Mapping):
        raise CalibrationError("position must be an object")
    try:
        return Position(x_cm=int(document["x_cm"]), y_cm=int(document["y_cm"]), z_cm=int(document["z_cm"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationError(f"invalid position: {exc}") from exc


def parse_route(raw: bytes) -> CalibrationRoute:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"route is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise CalibrationError("route document must be a JSON object")
    if document.get("schema_version") != _ROUTE_SCHEMA_VERSION:
        raise CalibrationError(f"unsupported route schema_version {document.get('schema_version')!r}")
    route_id = document.get("route_id")
    if route_id not in _ALLOWED_ROUTE_IDS:
        raise CalibrationError(
            f"route_id {route_id!r} is not a controller-owned fixed calibration route"
        )
    route_kind = document.get("route_kind")
    if route_kind != _ALLOWED_ROUTE_IDS[route_id]:
        raise CalibrationError(
            f"route_kind {route_kind!r} does not match the declared route_id {route_id!r}"
        )
    for name in ("site_sha256", "aircraft_id", "firmware_version", "camera_sha256", "tag_map_sha256"):
        if not document.get(name):
            raise CalibrationError(f"route is missing required binding {name!r}")
    home_doc = document.get("home")
    if not isinstance(home_doc, Mapping):
        raise CalibrationError("route home must be an object")
    try:
        home = HomeDefinition(
            pad_id=home_doc["pad_id"], nominal_pose=_position_from_document(home_doc["nominal_pose"]),
            heading_deg=float(home_doc["heading_deg"]), route_id=home_doc["route_id"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationError(f"invalid route home: {exc}") from exc
    if home.route_id != route_id:
        raise CalibrationError("route home.route_id must match the route's own route_id")
    raw_checkpoints = document.get("checkpoints")
    if not isinstance(raw_checkpoints, list) or not raw_checkpoints:
        raise CalibrationError("route must declare at least one checkpoint")
    checkpoints = []
    for entry in raw_checkpoints:
        if not isinstance(entry, Mapping):
            raise CalibrationError("route checkpoint must be an object")
        try:
            checkpoints.append(CalibrationCheckpointSpec(
                checkpoint_id=entry["checkpoint_id"],
                target=_position_from_document(entry["target"]),
                speed_cm_s=int(entry["speed_cm_s"]),
                looks_deg=tuple(int(value) for value in entry.get("looks_deg", ())),
                capture_count=int(entry.get("capture_count", 0)),
                is_return_leg=bool(entry.get("is_return_leg", False)),
                reanchor=bool(entry.get("reanchor", False)),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise CalibrationError(f"invalid route checkpoint: {exc}") from exc
    if route_kind == "forced_return":
        return_legs = [checkpoint for checkpoint in checkpoints if checkpoint.is_return_leg]
        if len(return_legs) != 1:
            raise CalibrationError(
                "a forced-return route must mark exactly one checkpoint is_return_leg=true, "
                "so record() actually exercises the return path (StepExecutor.return_home), "
                "not a plain checkpoint"
            )
    if route_kind == "tag_free":
        reanchor_legs = [checkpoint for checkpoint in checkpoints if checkpoint.reanchor]
        if len(reanchor_legs) != 1:
            raise CalibrationError(
                "a tag-free route must mark exactly one checkpoint reanchor=true, so pose "
                "drift is measured against a tag-anchored ground truth, not the estimator's "
                "own propagated belief"
            )
    return CalibrationRoute(
        schema_version=_ROUTE_SCHEMA_VERSION, route_id=route_id, route_kind=route_kind,
        site_sha256=document["site_sha256"], aircraft_id=document["aircraft_id"],
        firmware_version=document["firmware_version"], camera_sha256=document["camera_sha256"],
        tag_map_sha256=document["tag_map_sha256"], home=home, checkpoints=tuple(checkpoints),
    )


def load_route(path: str | Path) -> CalibrationRoute:
    return parse_route(Path(path).read_bytes())


#: Every checkpoint plan holds canonical heading throughout: calibration routes
#: exercise translation/hover/yaw-excursion, never a sustained off-canonical heading.
_CANONICAL_HEADING_DEG = CommandLimits().canonical_heading_deg


def _build_plans(route: CalibrationRoute) -> tuple[CheckpointPlan, ...]:
    limits = CommandLimits()
    plans = []
    current = route.home.nominal_pose
    for spec in route.checkpoints:
        plans.append(plan_checkpoint(
            checkpoint_id=spec.checkpoint_id, start=current, target=spec.target,
            start_heading_deg=_CANONICAL_HEADING_DEG, terminal_heading_deg=_CANONICAL_HEADING_DEG,
            speed_cm_s=spec.speed_cm_s, looks_deg=spec.looks_deg, capture_count=spec.capture_count,
            limits=limits,
        ))
        current = spec.target
    return tuple(plans)


# ---------------------------------------------------------------------------
# record: fly one controller-owned fixed route through an injected executor.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegSample:
    checkpoint_id: str
    route_kind: str
    distance_cm: float
    elapsed_s: float
    yaw_deg: float
    battery_percent: int | None
    drift_cm: float | None


@dataclass(frozen=True)
class CalibrationTrial:
    """The measured, append-only record of one flown calibration trial."""

    schema_version: str
    run_id: str
    route_id: str
    route_kind: str
    trial_role: str
    split_id: str
    manifest_authorization_digest: str
    terminal_state: str
    total_elapsed_s: float
    site_sha256: str
    aircraft_id: str
    firmware_version: str
    camera_sha256: str
    tag_map_sha256: str
    speeds_cm_s: tuple[int, ...]
    #: Trial-level battery readings: one taken at/just before takeoff (from
    #: ``executor.start``'s return) and one at landing (``TerminalOutcome.battery``).
    #: The battery bound is derived from these two readings over the trial's own
    #: ``total_elapsed_s`` window, not from a mismatched slice of the per-leg samples.
    battery_before_percent: int | None
    battery_after_percent: int | None
    #: "HARDWARE" only when the executor that produced this trial declared, and was
    #: bound to, real-hardware provenance (see ``HardwareProvenance``); otherwise
    #: "SIMULATION". A profile fit only from "SIMULATION" evidence is SIMULATION_ONLY
    #: and can never be promoted (see ``_fit_flight_profile``/``promote``).
    provenance: str
    samples: tuple[LegSample, ...]

    def __post_init__(self) -> None:
        if self.provenance not in _TRIAL_PROVENANCES:
            raise CalibrationError(f"unknown trial provenance {self.provenance!r}")

    def to_document(self) -> dict:
        body = {
            "aircraft_id": self.aircraft_id,
            "battery_after_percent": self.battery_after_percent,
            "battery_before_percent": self.battery_before_percent,
            "camera_sha256": self.camera_sha256,
            "firmware_version": self.firmware_version,
            "manifest_authorization_digest": self.manifest_authorization_digest,
            "provenance": self.provenance,
            "route_id": self.route_id,
            "route_kind": self.route_kind,
            "run_id": self.run_id,
            "samples": [
                {
                    "battery_percent": sample.battery_percent,
                    "checkpoint_id": sample.checkpoint_id,
                    "distance_cm": sample.distance_cm,
                    "drift_cm": sample.drift_cm,
                    "elapsed_s": sample.elapsed_s,
                    "route_kind": sample.route_kind,
                    "yaw_deg": sample.yaw_deg,
                }
                for sample in self.samples
            ],
            "schema_version": self.schema_version,
            "site_sha256": self.site_sha256,
            "speeds_cm_s": list(self.speeds_cm_s),
            "split_id": self.split_id,
            "tag_map_sha256": self.tag_map_sha256,
            "terminal_state": self.terminal_state,
            "total_elapsed_s": self.total_elapsed_s,
            "trial_role": self.trial_role,
        }
        digest = hashlib.sha256(canonical_bytes(body)).hexdigest()
        return {**body, "trial_sha256": digest}

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_document())


_TRIAL_DOCUMENT_FIELDS = frozenset(CalibrationTrial(
    schema_version=_TRIAL_SCHEMA_VERSION, run_id="r", route_id="return-route", route_kind="forced_return",
    trial_role="training", split_id="s", manifest_authorization_digest="0" * 64, terminal_state="LANDED",
    total_elapsed_s=0.0, site_sha256="0" * 64, aircraft_id="a", firmware_version="1", camera_sha256="0" * 64,
    tag_map_sha256="0" * 64, speeds_cm_s=(), battery_before_percent=None, battery_after_percent=None,
    provenance="SIMULATION", samples=(),
).to_document().keys()) - {"trial_sha256"}


def canonical_trial_sha256(trial: CalibrationTrial) -> str:
    return trial.to_document()["trial_sha256"]


def _evidence_signature(trial: CalibrationTrial) -> str:
    """A hash of everything a trial actually *measured*, excluding its identity
    (run id / role / split id / manifest binding / its own trial_sha256).

    Two trials with the same signature are the same underlying flight data -- reused
    or copied evidence wearing a different role label -- not independent evidence.
    ``promote`` refuses a holdout trial whose signature matches a training trial's:
    binding source trials by content, not by ``run_id`` (which is derived from the
    manifest, not the measurement, and so cannot detect a copied/replayed trial).
    """
    body = trial.to_document()
    for key in ("run_id", "trial_role", "split_id", "manifest_authorization_digest", "trial_sha256"):
        body.pop(key, None)
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def parse_trial(raw: bytes) -> CalibrationTrial:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"trial is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise CalibrationError("trial document must be a JSON object")
    if document.get("schema_version") != _TRIAL_SCHEMA_VERSION:
        raise CalibrationError(f"unsupported trial schema_version {document.get('schema_version')!r}")
    known = _TRIAL_DOCUMENT_FIELDS | {"trial_sha256"}
    unknown = set(document.keys()) - known
    if unknown:
        raise CalibrationError(f"unknown trial field(s): {sorted(unknown)}")
    missing = _TRIAL_DOCUMENT_FIELDS - set(document.keys())
    if missing:
        raise CalibrationError(f"missing trial field(s): {sorted(missing)}")
    supplied = document.get("trial_sha256")
    if not isinstance(supplied, str):
        raise CalibrationError("trial is missing its trial_sha256")
    try:
        samples = tuple(
            LegSample(
                checkpoint_id=entry["checkpoint_id"], route_kind=entry["route_kind"],
                distance_cm=entry["distance_cm"], elapsed_s=entry["elapsed_s"],
                yaw_deg=entry["yaw_deg"], battery_percent=entry["battery_percent"],
                drift_cm=entry["drift_cm"],
            )
            for entry in document["samples"]
        )
        trial = CalibrationTrial(
            schema_version=document["schema_version"], run_id=document["run_id"],
            route_id=document["route_id"], route_kind=document["route_kind"],
            trial_role=document["trial_role"], split_id=document["split_id"],
            manifest_authorization_digest=document["manifest_authorization_digest"],
            terminal_state=document["terminal_state"], total_elapsed_s=document["total_elapsed_s"],
            site_sha256=document["site_sha256"], aircraft_id=document["aircraft_id"],
            firmware_version=document["firmware_version"], camera_sha256=document["camera_sha256"],
            tag_map_sha256=document["tag_map_sha256"], speeds_cm_s=tuple(document["speeds_cm_s"]),
            battery_before_percent=document["battery_before_percent"],
            battery_after_percent=document["battery_after_percent"],
            provenance=document["provenance"],
            samples=samples,
        )
    except (KeyError, TypeError) as exc:
        raise CalibrationError(f"trial fields are malformed: {exc}") from exc
    if canonical_trial_sha256(trial) != supplied:
        raise CalibrationError("trial_sha256 does not match its content")
    return trial


def load_trial(path: str | Path) -> CalibrationTrial:
    return parse_trial(Path(path).read_bytes())


def write_trial(path: str | Path, trial: CalibrationTrial) -> None:
    raw = trial.canonical_bytes()
    parse_trial(raw)
    atomic_write_bytes(path, raw)


def _trial_path_for(manifest_path: str | Path) -> Path:
    manifest_path = Path(manifest_path)
    return manifest_path.with_name(manifest_path.stem + ".trial.json")


def record(
    *,
    manifest_path: str | Path,
    authorization_digest: str,
    route_path: str | Path,
    executor_factory: Callable[[], object],
    clock: Callable[[], float] = time.monotonic,
) -> CalibrationTrial:
    """Fly one controller-owned fixed calibration route and persist its trial record.

    ``executor_factory`` is a required keyword with no default: this function never
    constructs a client or executor itself, so a real Tello/provider client can only
    reach this code if a caller deliberately injects one. It never invokes Jev or any
    detector -- the executor's public surface has no such call. Never invoke against
    real hardware; every test in this packet injects a fake.

    The manifest names the role (training/holdout) and split id this trial is bound
    to; there is no caller override of that role. ``authorization_digest`` must equal
    the digest ``preview`` bound the manifest to, as a defense-in-depth check that the
    operator is recording under the manifest they think they are.
    """
    manifest = parse_record_manifest(Path(manifest_path).read_bytes())
    bound_digest = manifest.to_document()[artifacts.DIGEST_FIELD]
    if bound_digest != authorization_digest:
        raise CalibrationError(
            "authorization_digest does not match the manifest bound at preview time; refusing to record"
        )
    route = load_route(route_path)
    trial_path = _trial_path_for(manifest_path)
    if trial_path.exists():
        # A manifest (trial_role + split_id) authorizes exactly one physical
        # recording, and run artifacts are append-only: never silently overwrite an
        # existing trial just because the same manifest was recorded again.
        raise CalibrationError(
            f"trial {trial_path!s} already exists; a manifest authorizes exactly one recording"
        )
    plans = _build_plans(route)
    executor = executor_factory()
    samples: list[LegSample] = []
    battery_before_percent: int | None = None
    provenance = "SIMULATION"
    terminal = None
    try:
        start_wall = clock()
        home_reading = executor.start(route.home)
        if isinstance(home_reading, Telemetry):
            battery_before_percent = home_reading.battery_percent
        provenance = _trial_provenance(executor, route)
        for spec, plan in zip(route.checkpoints, plans):
            leg_start = clock()
            if spec.reanchor:
                outcome = executor.reanchor(plan)
            elif spec.is_return_leg:
                outcome = executor.return_home(plan)
            else:
                outcome = executor.execute(plan)
            leg_elapsed = clock() - leg_start
            if outcome.status != "COMPLETED":
                raise CalibrationError(
                    f"calibration leg {spec.checkpoint_id!r} did not complete cleanly: {outcome.status}"
                )
            battery_percent = (
                outcome.telemetry.battery_percent if isinstance(outcome.telemetry, Telemetry) else None
            )
            distance_cm = math.dist(
                (plan.start.x_cm, plan.start.y_cm, plan.start.z_cm),
                (plan.target.x_cm, plan.target.y_cm, plan.target.z_cm),
            )
            yaw_deg = float(max((abs(look) for look in spec.looks_deg), default=0))
            if spec.reanchor:
                propagated = getattr(outcome, "propagated_pose", None)
                anchored = outcome.pose
                if propagated is None or anchored is None:
                    raise CalibrationError(
                        f"reanchor leg {spec.checkpoint_id!r} must report both a propagated pose "
                        "and an anchor-corrected pose; a tag-free drift measurement needs real "
                        "ground truth, not just the estimator's own belief"
                    )
                drift_cm = math.dist(
                    (propagated.x_cm, propagated.y_cm, propagated.z_cm),
                    (anchored.x_cm, anchored.y_cm, anchored.z_cm),
                )
            else:
                drift_cm = None
            samples.append(LegSample(
                checkpoint_id=spec.checkpoint_id, route_kind=route.route_kind,
                distance_cm=distance_cm, elapsed_s=leg_elapsed, yaw_deg=yaw_deg,
                battery_percent=battery_percent, drift_cm=drift_cm,
            ))
        terminal = executor.land()
    except Exception:
        # Best-effort: get the aircraft down before closing out, even though a leg
        # failed (or start() itself failed) -- never rely on close() alone to land.
        try:
            executor.land()
        except Exception:
            pass
        raise
    finally:
        executor.close()
    if terminal.state != "LANDED":
        raise CalibrationError(f"calibration route did not land cleanly: {terminal.state}")
    battery_after_percent = getattr(terminal, "battery", None)
    total_elapsed_s = clock() - start_wall
    # One manifest (trial_role + split_id) authorizes exactly one physical trial
    # recording, so the manifest's own digest is a stable, unique run id for it.
    trial = CalibrationTrial(
        schema_version=_TRIAL_SCHEMA_VERSION, run_id=bound_digest, route_id=route.route_id,
        route_kind=route.route_kind, trial_role=manifest.trial_role, split_id=manifest.split_id,
        manifest_authorization_digest=bound_digest, terminal_state=terminal.state,
        total_elapsed_s=total_elapsed_s, site_sha256=route.site_sha256, aircraft_id=route.aircraft_id,
        firmware_version=route.firmware_version, camera_sha256=route.camera_sha256,
        tag_map_sha256=route.tag_map_sha256,
        speeds_cm_s=tuple(sorted({checkpoint.speed_cm_s for checkpoint in route.checkpoints})),
        battery_before_percent=battery_before_percent, battery_after_percent=battery_after_percent,
        provenance=provenance, samples=tuple(samples),
    )
    write_trial(trial_path, trial)
    return trial


# ---------------------------------------------------------------------------
# CalibrationProfile: the fitted/promoted candidate itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatteryBound:
    """Battery bound = worst observed consumption rate plus a declared margin and a
    quantization allowance -- never a bare average, and never relaxed by promotion."""

    worst_observed_percent_per_s: float
    margin_percent_per_s: float
    quantization_allowance_percent: float
    landing_floor_percent: float

    def __post_init__(self) -> None:
        for name in fields(self):
            value = getattr(self, name.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise CalibrationError(f"{name.name} must be a finite, nonnegative number")

    @property
    def conservative_percent_per_s(self) -> float:
        # quantization_allowance_percent is already folded into worst_observed_percent_per_s
        # (see _battery_bound_from_trials): it is an absolute-percent reading error, so it is
        # divided by each trial's own elapsed time -- a rate -- before the worst-case is taken,
        # never added again here as a bare percent (that mixed units: %/s + %/s + %).
        return self.worst_observed_percent_per_s + self.margin_percent_per_s


@dataclass(frozen=True)
class PoseEnvelopePoint:
    """One sampled (distance, time, yaw) combination and its measured drift.

    The envelope is a set of these -- exactly the combinations sampled during
    calibration -- never an interpolated or extrapolated continuous model.
    """

    distance_cm: float
    time_s: float
    yaw_deg: float
    drift_cm: float


@dataclass(frozen=True)
class CalibrationProfile:
    schema_version: str
    kind: str
    state: str
    site_sha256: str | None
    aircraft_id: str | None
    firmware_version: str | None
    camera_sha256: str | None
    tag_map_sha256: str | None
    speeds_cm_s: tuple[int, ...]
    battery: BatteryBound | None
    pose_envelope: tuple[PoseEnvelopePoint, ...]
    training_run_ids: tuple[str, ...]
    holdout_run_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise CalibrationError(f"unsupported schema_version {self.schema_version!r}")
        if self.kind not in _PROFILE_KINDS:
            raise CalibrationError(f"unknown profile kind {self.kind!r}")
        if self.state not in _PROFILE_STATES:
            raise CalibrationError(f"unknown profile state {self.state!r}")
        if tuple(sorted(self.training_run_ids)) != tuple(self.training_run_ids):
            raise CalibrationError("training_run_ids must be a sorted tuple")
        if tuple(sorted(self.holdout_run_ids)) != tuple(self.holdout_run_ids):
            raise CalibrationError("holdout_run_ids must be a sorted tuple")
        if set(self.training_run_ids) & set(self.holdout_run_ids):
            raise CalibrationError("a run id cannot be both training and holdout evidence")
        if self.state == "PROMOTED" and not self.holdout_run_ids:
            raise CalibrationError("a PROMOTED profile must record the holdout evidence it was promoted with")

    def to_document(self) -> dict:
        body = {
            "aircraft_id": self.aircraft_id,
            "battery": None if self.battery is None else {
                "landing_floor_percent": self.battery.landing_floor_percent,
                "margin_percent_per_s": self.battery.margin_percent_per_s,
                "quantization_allowance_percent": self.battery.quantization_allowance_percent,
                "worst_observed_percent_per_s": self.battery.worst_observed_percent_per_s,
            },
            "camera_sha256": self.camera_sha256,
            "firmware_version": self.firmware_version,
            "holdout_run_ids": list(self.holdout_run_ids),
            "kind": self.kind,
            "pose_envelope": [
                {
                    "distance_cm": point.distance_cm, "drift_cm": point.drift_cm,
                    "time_s": point.time_s, "yaw_deg": point.yaw_deg,
                }
                for point in self.pose_envelope
            ],
            "schema_version": self.schema_version,
            "site_sha256": self.site_sha256,
            "speeds_cm_s": list(self.speeds_cm_s),
            "state": self.state,
            "tag_map_sha256": self.tag_map_sha256,
            "training_run_ids": list(self.training_run_ids),
        }
        digest = hashlib.sha256(canonical_bytes(body)).hexdigest()
        return {**body, "profile_sha256": digest}

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_document())


def canonical_profile_sha256(profile: CalibrationProfile) -> str:
    return profile.to_document()["profile_sha256"]


_PROFILE_DOCUMENT_FIELDS = frozenset(CalibrationProfile(
    schema_version=SCHEMA_VERSION, kind="flight", state="SIMULATION_ONLY", site_sha256=None,
    aircraft_id=None, firmware_version=None, camera_sha256=None, tag_map_sha256=None, speeds_cm_s=(),
    battery=None, pose_envelope=(), training_run_ids=(),
).to_document().keys()) - {"profile_sha256"}


def parse_profile(raw: bytes) -> CalibrationProfile:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"profile is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise CalibrationError("profile document must be a JSON object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise CalibrationError(f"unsupported profile schema_version {document.get('schema_version')!r}")
    known = _PROFILE_DOCUMENT_FIELDS | {"profile_sha256"}
    unknown = set(document.keys()) - known
    if unknown:
        raise CalibrationError(f"unknown profile field(s): {sorted(unknown)}")
    missing = _PROFILE_DOCUMENT_FIELDS - set(document.keys())
    if missing:
        raise CalibrationError(f"missing profile field(s): {sorted(missing)}")
    supplied = document.get("profile_sha256")
    if not isinstance(supplied, str):
        raise CalibrationError("profile is missing its profile_sha256")
    try:
        battery_doc = document["battery"]
        battery = None if battery_doc is None else BatteryBound(
            worst_observed_percent_per_s=battery_doc["worst_observed_percent_per_s"],
            margin_percent_per_s=battery_doc["margin_percent_per_s"],
            quantization_allowance_percent=battery_doc["quantization_allowance_percent"],
            landing_floor_percent=battery_doc["landing_floor_percent"],
        )
        pose_envelope = tuple(
            PoseEnvelopePoint(
                distance_cm=point["distance_cm"], time_s=point["time_s"],
                yaw_deg=point["yaw_deg"], drift_cm=point["drift_cm"],
            )
            for point in document["pose_envelope"]
        )
        profile = CalibrationProfile(
            schema_version=document["schema_version"], kind=document["kind"], state=document["state"],
            site_sha256=document["site_sha256"], aircraft_id=document["aircraft_id"],
            firmware_version=document["firmware_version"], camera_sha256=document["camera_sha256"],
            tag_map_sha256=document["tag_map_sha256"], speeds_cm_s=tuple(document["speeds_cm_s"]),
            battery=battery, pose_envelope=pose_envelope,
            training_run_ids=tuple(document["training_run_ids"]),
            holdout_run_ids=tuple(document["holdout_run_ids"]),
        )
    except (KeyError, TypeError) as exc:
        raise CalibrationError(f"profile fields are malformed: {exc}") from exc
    if canonical_profile_sha256(profile) != supplied:
        raise CalibrationError("profile_sha256 does not match its content")
    return profile


def load_profile(path: str | Path) -> CalibrationProfile:
    return parse_profile(Path(path).read_bytes())


def write_profile(path: str | Path, profile: CalibrationProfile) -> None:
    raw = profile.canonical_bytes()
    parse_profile(raw)
    atomic_write_bytes(path, raw)


# ---------------------------------------------------------------------------
# Evidence index: which manifests/trials are predeclared training vs holdout.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceEntry:
    manifest_path: str
    trial_path: str
    role: str

    def __post_init__(self) -> None:
        if self.role not in _TRIAL_ROLES:
            raise CalibrationError(f"unknown evidence role {self.role!r}")


@dataclass(frozen=True)
class CalibrationEvidenceIndex:
    schema_version: str
    entries: tuple[EvidenceEntry, ...]


def parse_evidence_index(raw: bytes) -> CalibrationEvidenceIndex:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"evidence index is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise CalibrationError("evidence index document must be a JSON object")
    if document.get("schema_version") != _EVIDENCE_INDEX_SCHEMA_VERSION:
        raise CalibrationError(f"unsupported evidence index schema_version {document.get('schema_version')!r}")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise CalibrationError("evidence index 'entries' must be a JSON array")
    entries = []
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            raise CalibrationError("evidence index entry must be an object")
        try:
            entries.append(EvidenceEntry(
                manifest_path=entry["manifest_path"], trial_path=entry["trial_path"], role=entry["role"],
            ))
        except KeyError as exc:
            raise CalibrationError(f"evidence index entry is missing a field: {exc}") from exc
    return CalibrationEvidenceIndex(schema_version=_EVIDENCE_INDEX_SCHEMA_VERSION, entries=tuple(entries))


def load_evidence_index(path: str | Path) -> CalibrationEvidenceIndex:
    return parse_evidence_index(Path(path).read_bytes())


def _load_and_check_pair(entry: EvidenceEntry, *, expected_role: str) -> tuple[CalibrationRecordManifest, CalibrationTrial]:
    if entry.role != expected_role:
        raise CalibrationError(
            f"evidence index entry for {entry.manifest_path!r} is not predeclared {expected_role!r}"
        )
    manifest = parse_record_manifest(Path(entry.manifest_path).read_bytes())
    if manifest.trial_role != expected_role:
        raise CalibrationError(
            f"manifest {entry.manifest_path!r} is bound to trial_role {manifest.trial_role!r}, "
            f"not the predeclared {expected_role!r}; a training/holdout role cannot be changed after the fact"
        )
    trial = parse_trial(Path(entry.trial_path).read_bytes())
    manifest_digest = manifest.to_document()[artifacts.DIGEST_FIELD]
    if trial.manifest_authorization_digest != manifest_digest:
        raise CalibrationError(
            f"trial {entry.trial_path!r} was not recorded under manifest {entry.manifest_path!r}"
        )
    if trial.trial_role != expected_role or trial.split_id != manifest.split_id:
        raise CalibrationError(f"trial {entry.trial_path!r} does not match its manifest binding")
    return manifest, trial


# ---------------------------------------------------------------------------
# fit: consume only predeclared training manifests/trials, write a CALIBRATION candidate.
# ---------------------------------------------------------------------------


def _consistent_binding(trials: Sequence[CalibrationTrial], field_name: str) -> object:
    values = {getattr(trial, field_name) for trial in trials}
    if len(values) != 1:
        raise CalibrationError(f"training trials disagree on {field_name}: {sorted(map(str, values))}")
    return next(iter(values))


def _battery_bound_from_trials(trials: Sequence[CalibrationTrial]) -> BatteryBound:
    worst_rate = 0.0
    for trial in trials:
        if trial.battery_before_percent is None or trial.battery_after_percent is None:
            raise CalibrationError(
                f"forced-return trial {trial.run_id!r} is missing a pre-takeoff or landing battery reading"
            )
        if trial.total_elapsed_s <= 0:
            raise CalibrationError(f"forced-return trial {trial.run_id!r} reports nonpositive elapsed time")
        consumed = trial.battery_before_percent - trial.battery_after_percent
        if consumed < 0:
            raise CalibrationError(f"forced-return trial {trial.run_id!r} reports battery increasing; refused")
        # The allowance is an absolute-percent reading error; dividing it by this
        # trial's own elapsed time turns it into the rate contribution it actually
        # represents, over the same before/after-takeoff interval as `consumed`.
        rate = (consumed + _BATTERY_QUANTIZATION_ALLOWANCE_PERCENT) / trial.total_elapsed_s
        worst_rate = max(worst_rate, rate)
    return BatteryBound(
        worst_observed_percent_per_s=worst_rate, margin_percent_per_s=_BATTERY_MARGIN_PERCENT_PER_S,
        quantization_allowance_percent=_BATTERY_QUANTIZATION_ALLOWANCE_PERCENT,
        landing_floor_percent=_BATTERY_LANDING_FLOOR_PERCENT,
    )


def _pose_envelope_key(distance_cm: float, elapsed_s: float, yaw_deg: float) -> tuple[float, float, float]:
    def _bucket(value: float, size: float) -> float:
        return round(value / size) * size

    return (
        _bucket(distance_cm, _DISTANCE_BUCKET_CM), _bucket(elapsed_s, _TIME_BUCKET_S),
        _bucket(yaw_deg, _YAW_BUCKET_DEG),
    )


def _pose_envelope_from_trials(trials: Sequence[CalibrationTrial]) -> tuple[PoseEnvelopePoint, ...]:
    # Only samples from a reanchor leg carry a ground-truth-anchored drift measurement
    # (see `record`); other legs' samples have drift_cm == None and are not evidence
    # for the pose envelope at all, so they are skipped rather than treated as an error.
    buckets: dict[tuple[float, float, float], float] = {}
    for trial in trials:
        for sample in trial.samples:
            if sample.drift_cm is None:
                continue
            key = _pose_envelope_key(sample.distance_cm, sample.elapsed_s, sample.yaw_deg)
            # Conservative: a bucket's bound is the worst (largest) drift observed for
            # any sample landing in it, never an average.
            buckets[key] = max(buckets.get(key, 0.0), sample.drift_cm)
    if not buckets:
        raise CalibrationError("tag-free training evidence has no measured (reanchor) drift")
    return tuple(
        PoseEnvelopePoint(distance_cm=key[0], time_s=key[1], yaw_deg=key[2], drift_cm=drift)
        for key, drift in sorted(buckets.items())
    )


def _fit_flight_profile(trials: Sequence[CalibrationTrial]) -> CalibrationProfile:
    forced_return = [trial for trial in trials if trial.route_kind == "forced_return"]
    tag_free = [trial for trial in trials if trial.route_kind == "tag_free"]
    if not forced_return:
        raise CalibrationError("flight fit requires at least one forced-return training trial")
    if not tag_free:
        raise CalibrationError("flight fit requires at least one tag-free training trial")
    bindings = {
        name: _consistent_binding(trials, name)
        for name in ("site_sha256", "aircraft_id", "firmware_version", "camera_sha256", "tag_map_sha256")
    }
    speeds_cm_s = tuple(sorted({speed for trial in trials for speed in trial.speeds_cm_s}))
    battery = _battery_bound_from_trials(forced_return)
    pose_envelope = _pose_envelope_from_trials(tag_free)
    training_run_ids = tuple(sorted({trial.run_id for trial in trials}))
    # Synthetic or self-validating evidence cannot produce a CALIBRATION (and so never
    # a PROMOTED) profile: a candidate fit from any non-hardware-provenanced trial is
    # SIMULATION_ONLY, full stop -- not relabelled later, not overridable by promote.
    state = "CALIBRATION" if all(trial.provenance == "HARDWARE" for trial in trials) else "SIMULATION_ONLY"
    return CalibrationProfile(
        schema_version=SCHEMA_VERSION, kind="flight", state=state, speeds_cm_s=speeds_cm_s,
        battery=battery, pose_envelope=pose_envelope, training_run_ids=training_run_ids,
        holdout_run_ids=(), **bindings,
    )


def fit(*, profile_kind: str, evidence_index_path: str | Path, output_path: str | Path) -> CalibrationProfile:
    if profile_kind not in _PROFILE_KINDS:
        raise CalibrationError(f"unknown profile_kind {profile_kind!r}")
    if profile_kind == "tracker":
        raise CalibrationError(TRACKER_DEFERRED_MESSAGE)
    index = load_evidence_index(evidence_index_path)
    training_entries = [entry for entry in index.entries if entry.role == "training"]
    if not training_entries:
        raise CalibrationError("fit requires at least one training evidence entry")
    trials = []
    seen_run_ids: set[str] = set()
    for entry in training_entries:
        _, trial = _load_and_check_pair(entry, expected_role="training")
        if trial.run_id in seen_run_ids:
            raise CalibrationError(f"duplicate training run id {trial.run_id!r}")
        seen_run_ids.add(trial.run_id)
        trials.append(trial)
    profile = _fit_flight_profile(trials)
    write_profile(output_path, profile)
    return profile


# ---------------------------------------------------------------------------
# promote: offline, holdout-only, refuses any overlap with the training evidence.
# ---------------------------------------------------------------------------


def _validate_flight_holdout(candidate: CalibrationProfile, holdout_trials: Sequence[CalibrationTrial]) -> None:
    forced_return = [trial for trial in holdout_trials if trial.route_kind == "forced_return"]
    tag_free = [trial for trial in holdout_trials if trial.route_kind == "tag_free"]
    if not forced_return:
        raise CalibrationError("flight promotion requires forced-return holdout evidence")
    if not tag_free:
        raise CalibrationError("flight promotion requires tag-free/reanchor holdout evidence")
    for name, expected in (
        ("site_sha256", candidate.site_sha256), ("aircraft_id", candidate.aircraft_id),
        ("firmware_version", candidate.firmware_version), ("camera_sha256", candidate.camera_sha256),
        ("tag_map_sha256", candidate.tag_map_sha256),
    ):
        for trial in holdout_trials:
            if getattr(trial, name) != expected:
                raise CalibrationError(
                    f"holdout trial {trial.run_id!r} disagrees with the candidate on {name}"
                )
    for trial in holdout_trials:
        unbound_speeds = set(trial.speeds_cm_s) - set(candidate.speeds_cm_s)
        if unbound_speeds:
            raise CalibrationError(
                f"holdout trial {trial.run_id!r} was flown at speed(s) {sorted(unbound_speeds)} "
                "the candidate was never calibrated for"
            )
    assert candidate.battery is not None
    for trial in forced_return:
        if trial.battery_before_percent is None or trial.battery_after_percent is None:
            raise CalibrationError(f"holdout trial {trial.run_id!r} has insufficient battery telemetry")
        if trial.total_elapsed_s <= 0:
            raise CalibrationError(f"holdout trial {trial.run_id!r} reports nonpositive elapsed time")
        if trial.terminal_state != "LANDED":
            raise CalibrationError(f"holdout trial {trial.run_id!r} did not report a clean forced return")
        if trial.battery_after_percent < candidate.battery.landing_floor_percent:
            raise CalibrationError(
                f"holdout trial {trial.run_id!r} landed at {trial.battery_after_percent}%, below the "
                f"candidate's landing floor of {candidate.battery.landing_floor_percent}%"
            )
        observed_rate = (trial.battery_before_percent - trial.battery_after_percent) / trial.total_elapsed_s
        if observed_rate > candidate.battery.conservative_percent_per_s:
            raise CalibrationError(
                f"holdout trial {trial.run_id!r} exceeded the candidate's conservative battery bound"
            )
    envelope = {(point.distance_cm, point.time_s, point.yaw_deg): point.drift_cm for point in candidate.pose_envelope}
    for trial in tag_free:
        for sample in trial.samples:
            if sample.drift_cm is None:
                continue
            key = _pose_envelope_key(sample.distance_cm, sample.elapsed_s, sample.yaw_deg)
            bound = envelope.get(key)
            if bound is None or sample.drift_cm > bound:
                raise CalibrationError(f"holdout trial {trial.run_id!r} exceeded the candidate's pose envelope")


def admissible_for(profile: CalibrationProfile, *, grant_state: str) -> bool:
    """Whether *profile* may legally back a capability grant in state *grant_state*.

    A CALIBRATION profile is legal only inside a TRIAL/acceptance grant with matching
    bindings -- never live use. A PROMOTED profile is legal only for live use.
    SIMULATION_ONLY is never admissible for either. This is a pure predicate over
    calibration's own state; wiring it into ``sentinel.room.capabilities``' admission
    path (``admit_live``) is a Task 4 obligation -- see the Plan 06 ledger.
    """
    if grant_state == "TRIAL":
        return profile.state == "CALIBRATION"
    if grant_state == "PROMOTED":
        return profile.state == "PROMOTED"
    return False


def promote(
    *, profile_kind: str, candidate_path: str | Path, evidence_index_path: str | Path,
    output_path: str | Path,
) -> CalibrationProfile:
    if profile_kind not in _PROFILE_KINDS:
        raise CalibrationError(f"unknown profile_kind {profile_kind!r}")
    if profile_kind == "tracker":
        raise CalibrationError(TRACKER_DEFERRED_MESSAGE)
    candidate = load_profile(candidate_path)
    if candidate.kind != profile_kind:
        raise CalibrationError(
            f"--profile-kind {profile_kind!r} does not match candidate kind {candidate.kind!r}"
        )
    if candidate.state != "CALIBRATION":
        raise CalibrationError(f"only a CALIBRATION candidate can be promoted (state={candidate.state!r})")

    index = load_evidence_index(evidence_index_path)

    # A candidate profile is a plain file, hashed with an unkeyed profile_sha256: it
    # cannot be trusted as written. Re-derive it from the same predeclared training
    # evidence it claims to be fit from (in this same evidence index) and require an
    # exact match -- a profile record can never approve itself.
    training_entries = [entry for entry in index.entries if entry.role == "training"]
    if not training_entries:
        raise CalibrationError(
            "promote requires the candidate's training evidence in the same evidence index, "
            "so the candidate can be re-derived rather than trusted as written"
        )
    training_trials = []
    seen_training_ids: set[str] = set()
    for entry in training_entries:
        _, trial = _load_and_check_pair(entry, expected_role="training")
        if trial.run_id in seen_training_ids:
            raise CalibrationError(f"duplicate training run id {trial.run_id!r}")
        seen_training_ids.add(trial.run_id)
        training_trials.append(trial)
    refit = _fit_flight_profile(training_trials)
    if refit != candidate:
        raise CalibrationError(
            "candidate does not match a fresh fit of its declared training evidence "
            "(bindings, margins, quantization/landing-floor constants, or the evidence "
            "itself have drifted); a profile record cannot approve itself"
        )

    holdout_entries = [entry for entry in index.entries if entry.role == "holdout"]
    if len(holdout_entries) < 2:
        raise CalibrationError("promote requires distinct holdout manifests in the evidence index")
    holdout_trials = []
    seen_holdout_ids: set[str] = set()
    for entry in holdout_entries:
        _, trial = _load_and_check_pair(entry, expected_role="holdout")
        if trial.run_id in candidate.training_run_ids:
            raise CalibrationError(f"holdout run {trial.run_id!r} was already used during fitting; refused")
        if trial.run_id in seen_holdout_ids:
            raise CalibrationError(f"duplicate holdout run id {trial.run_id!r}")
        seen_holdout_ids.add(trial.run_id)
        holdout_trials.append(trial)

    training_signatures = {_evidence_signature(trial) for trial in training_trials}
    for trial in holdout_trials:
        if _evidence_signature(trial) in training_signatures:
            raise CalibrationError(
                f"holdout trial {trial.run_id!r} duplicates a training trial's measured content; "
                "reused or copied evidence cannot serve as independent holdout"
            )
        if trial.provenance != "HARDWARE":
            raise CalibrationError(
                f"holdout trial {trial.run_id!r} is not hardware-provenanced; simulated evidence "
                "can never promote a profile"
            )

    _validate_flight_holdout(candidate, holdout_trials)
    promoted = replace(candidate, state="PROMOTED", holdout_run_ids=tuple(sorted(seen_holdout_ids)))
    write_profile(output_path, promoted)
    return promoted
