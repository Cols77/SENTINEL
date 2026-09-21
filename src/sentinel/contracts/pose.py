"""DronePoseReport contract: WP0 amendment SENT-POSE-V0."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

SourceKind = Literal["LIVE", "SIMULATED", "REPLAYED"]
MAX_REPROJECTION_ERROR_PX = 2.0


@dataclass(frozen=True)
class LocalPoint:
    """Position within the operating room, normalised to [0, 100] on both axes."""

    x: float
    y: float

    def __post_init__(self):
        for name in ("x", "y"):
            v = getattr(self, name)
            if not (0.0 <= v <= 100.0):
                raise ValueError(f"LocalPoint.{name}={v!r} outside [0, 100]")


@dataclass(frozen=True)
class TagObservation:
    tag_id: int
    zone_id: str
    distance_m: float
    reprojection_error_px: float

    def __post_init__(self):
        if self.reprojection_error_px > MAX_REPROJECTION_ERROR_PX:
            raise ValueError(
                f"tag reprojection_error_px={self.reprojection_error_px} exceeds "
                f"{MAX_REPROJECTION_ERROR_PX}: tag observation fails closed"
            )


@dataclass(frozen=True)
class DronePoseReport:
    report_id: str
    run_id: str
    observed_at: datetime
    source_kind: SourceKind
    point: tuple[float, float]
    heading_deg: float | None
    altitude_m: float | None
    method: Literal["APRILTAG_36H11_PNP"]
    tag_observations: tuple[TagObservation, ...]
    reprojection_error_px: float

    def __post_init__(self):
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware: pose report fails closed")
        LocalPoint(*self.point)
        if self.reprojection_error_px > MAX_REPROJECTION_ERROR_PX:
            raise ValueError(
                f"reprojection_error_px={self.reprojection_error_px} exceeds "
                f"{MAX_REPROJECTION_ERROR_PX}: pose report fails closed"
            )
        if self.altitude_m is not None and self.altitude_m < 0:
            raise ValueError(f"altitude_m={self.altitude_m} must be >= 0 (wire schema)")
        if not self.tag_observations:
            raise ValueError("DronePoseReport requires >=1 tag observation")

    def to_dict(self) -> dict:
        """Wire adapter: wire-shaped dict matching the DronePoseReport anchor."""
        return {
            "report_id": self.report_id,
            "run_id": self.run_id,
            "observed_at": self.observed_at.astimezone(timezone.utc)
            .isoformat().replace("+00:00", "Z"),
            "source_kind": self.source_kind,
            "pose": {
                "point": {"x": self.point[0], "y": self.point[1]},
                "heading_deg": self.heading_deg,
                "altitude_m": self.altitude_m,
            },
            "method": self.method,
            "tag_observations": [
                {
                    "tag_id": o.tag_id,
                    "zone_id": o.zone_id,
                    "distance_m": o.distance_m,
                    "reprojection_error_px": o.reprojection_error_px,
                }
                for o in self.tag_observations
            ],
            "reprojection_error_px": self.reprojection_error_px,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DronePoseReport":
        """Wire adapter: coerce dict tag observations into TagObservation."""
        d = dict(d)
        if isinstance(d.get("pose"), dict):
            pose = d.pop("pose")
            point = pose["point"]
            d["point"] = (point["x"], point["y"])
            d["heading_deg"] = pose.get("heading_deg")
            d["altitude_m"] = pose.get("altitude_m")
        if isinstance(d.get("observed_at"), str):
            d["observed_at"] = datetime.fromisoformat(
                d["observed_at"].replace("Z", "+00:00")
            )
        raw_obs = d.get("tag_observations") or ()
        d["tag_observations"] = tuple(
            o if isinstance(o, TagObservation) else TagObservation(**o)
            for o in raw_obs
        )
        if isinstance(d.get("point"), (list, tuple)):
            d["point"] = tuple(d["point"])
        return cls(**d)


def validate_drone_pose_report(report: DronePoseReport, *, judged_path: bool = False) -> None:
    """Semantic validation; LIVE-only on judged paths (SR-061), fail-closed."""
    if judged_path and report.source_kind != "LIVE":
        raise ValueError(f"judged_path requires LIVE, got {report.source_kind}")
