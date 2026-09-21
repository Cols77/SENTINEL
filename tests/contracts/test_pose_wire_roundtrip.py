"""WP5 Task 3.2: schema <-> dataclass round-trip for DronePoseReport."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sentinel.contracts.pose import DronePoseReport, validate_drone_pose_report

ROOT = Path(__file__).parents[2]
CONTRACTS = ROOT / "docs" / "superpowers" / "specs" / "contracts"
FIXTURES = CONTRACTS / "fixtures"

_wire = json.loads((CONTRACTS / "sentinel-v0-wire.schema.json").read_text(encoding="utf-8"))
_validator = Draft202012Validator({"$ref": "#/$defs/DronePoseReport", "$defs": _wire["$defs"]})


def _valid_fixture() -> dict:
    return json.loads((FIXTURES / "drone-pose-report.valid.json").read_text(encoding="utf-8"))


def _invalid_fixture() -> dict:
    return json.loads(
        (FIXTURES / "drone-pose-report.judged-simulated.invalid.json").read_text(encoding="utf-8")
    )


def test_valid_fixture_round_trips_through_schema():
    wire = _valid_fixture()
    errors = list(_validator.iter_errors(wire))
    assert not errors, [e.message for e in errors]

    report = DronePoseReport.from_dict(wire)
    dumped = report.to_dict()
    assert dumped == wire, "to_dict(from_dict(wire)) must equal the original wire document"

    errors = list(_validator.iter_errors(dumped))
    assert not errors, [e.message for e in errors]

    validate_drone_pose_report(report)  # must not raise


def test_invalid_fixture_is_rejected_by_jsonschema_and_judged_path_validator():
    wire = _invalid_fixture()
    errors = list(_validator.iter_errors(wire))
    assert errors, "SIMULATED report must not validate against the DronePoseReport anchor"

    # judged_path is a semantic marker, not a dataclass field; strip it so the
    # dataclass accepts construction and the judged-path validator does the rejecting.
    without_marker = {k: v for k, v in wire.items() if k != "judged_path"}
    report = DronePoseReport.from_dict(without_marker)
    with pytest.raises(ValueError):
        validate_drone_pose_report(report, judged_path=True)


def test_from_dict_also_accepts_flat_test_helper_shape():
    report = DronePoseReport.from_dict(
        {
            "report_id": "6f6b0a1e-7c2d-4e8a-9b1f-0a2c3d4e5f63",
            "run_id": "4f6b0a1e-7c2d-4e8a-9b1f-0a2c3d4e5f61",
            "observed_at": "2026-09-18T10:00:00Z",
            "source_kind": "LIVE",
            "point": [12.5, 40.0],
            "heading_deg": 91.0,
            "altitude_m": 1.2,
            "method": "APRILTAG_36H11_PNP",
            "tag_observations": [
                {"tag_id": 0, "zone_id": "LEFT_ALLEY_FRONT",
                 "distance_m": 2.0, "reprojection_error_px": 0.8}
            ],
            "reprojection_error_px": 0.8,
        }
    )
    assert report.point == (12.5, 40.0)
    assert report.to_dict()["pose"]["point"] == {"x": 12.5, "y": 40.0}
