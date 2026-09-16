import json
from pathlib import Path
from jsonschema import Draft202012Validator


ROOT = Path(__file__).parents[1]
CONTRACTS = ROOT / "docs" / "superpowers" / "specs" / "contracts"


def _load(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_mountain_fixture_is_the_fixed_six_zone_catalogue():
    fixture = _load("mountain-v0.fixture.json")
    assert [s["section_id"] for s in fixture["sections"]] == [
        "LEFT_ALLEY_FRONT",
        "LEFT_ALLEY_MIDDLE",
        "LEFT_ALLEY_BACK",
        "RIGHT_ALLEY_FRONT",
        "RIGHT_ALLEY_MIDDLE",
        "RIGHT_ALLEY_BACK",
    ]


def test_wire_contract_exposes_immutable_plan_preflight_and_capture_records():
    wire = _load("sentinel-v0-wire.schema.json")
    defs = wire["$defs"]
    for definition in (
        "SurveyPlanReference",
        "PreflightReport",
        "StationCaptureProvenance",
        "ForceRunApproval",
    ):
        assert definition in defs


def test_wire_contract_uses_indoor_outcome_vocabulary():
    wire = _load("sentinel-v0-wire.schema.json")
    serialized = json.dumps(wire)
    for outcome in ("FAILED", "NEEDS_REVIEW", "COMPLETED", "EMERGENCY"):
        assert outcome in serialized


def test_agent_proposal_is_catalogue_bound_and_force_run_is_typed():
    defs = _load("sentinel-v0-wire.schema.json")["$defs"]
    assert "AgentModelResult" in defs
    assert "CommandForceRunCreatePayload" in defs
    assert "PlanReadModel" in defs
    proposal = defs["AgentModelResult"]
    assert proposal["allOf"][1]["properties"]["catalogue_plan_id"]["$ref"] == "#/$defs/configured_id"


def test_preflight_has_named_structured_checks():
    defs = _load("sentinel-v0-wire.schema.json")["$defs"]
    checks = defs["PreflightReport"]["properties"]["checks"]
    assert checks["minItems"] == 8
    assert set(checks["items"]["properties"]) >= {"name", "passed", "checked_at"}


def test_review_rework_adds_plan_approval_catalogue_lifecycle_and_preflight_read():
    schema = _load("sentinel-v0-wire.schema.json")
    assert (CONTRACTS / "survey-catalogue-v1.schema.json").exists()
    defs = schema["$defs"]
    for definition in (
        "PlanApproval",
        "SurveyPlanReference",
        "StationCaptureProvenance",
        "ForceRunApproval",
        "CommandForceRunCreatePayload",
        "PlanReadModel",
    ):
        assert definition in defs
    approval = defs["PlanApproval"]
    assert {"plan_id", "plan_sha256", "worker_id", "choreography_id", "expected_state_version", "expires_at"} <= set(
        approval["properties"]
    )
    # rejection is event-only, never a terminal run_status
    run_status = defs["StateSnapshot"]["allOf"][1]["properties"]["run_status"]["enum"]
    assert "REJECTED" not in run_status
    for outcome in ("COMPLETED", "FAILED", "EMERGENCY"):
        assert outcome in run_status
    # plan lifecycle + preflight events exist and are discriminated
    event_types = defs["EventEnvelope"]["allOf"][1]["properties"]["event_type"]["enum"]
    for ev in ("PLAN_PROPOSED", "PLAN_CREATED", "PLAN_APPROVED", "PLAN_REJECTED", "PREFLIGHT_REPORTED"):
        assert ev in event_types
    # run carries a layout mode chosen from the two catalogue modes
    plan_read = defs["PlanReadModel"]["properties"]["mode"]["enum"]
    assert set(plan_read) == {"MARKER_VERIFIED", "ROUTE_ONLY_2_ZONE"}


def test_checked_in_live_perception_fixtures_validate():
    schema = _load("sentinel-v0-wire.schema.json")
    validator = Draft202012Validator(schema)
    fixture_dir = CONTRACTS / "fixtures"
    for name in ("judged-perception-live.valid.json",):
        errors = list(validator.iter_errors(json.loads((fixture_dir / name).read_text(encoding="utf-8"))))
        assert not errors, [error.message for error in errors]


UUID = "66666666-6666-4666-8666-666666666666"
TS = "2026-09-16T00:00:00Z"
ZONES = [
    "LEFT_ALLEY_FRONT",
    "LEFT_ALLEY_MIDDLE",
    "LEFT_ALLEY_BACK",
    "RIGHT_ALLEY_FRONT",
    "RIGHT_ALLEY_MIDDLE",
    "RIGHT_ALLEY_BACK",
]


def _six_zone_snapshot():
    section = lambda z: {
        "section_id": z, "name": z.replace("_", " ").title(),
        "polygon": [[8, 68], [92, 68], [92, 94], [8, 94], [8, 68]],
        "status": "UNSURVEYED", "stale": False,
    }
    return {
        "contract_version": "sentinel.v0", "wire_type": "mountain_snapshot",
        "run_id": UUID, "snapshot_version": 1, "as_of": TS,
        "sections": [section(z) for z in ZONES],
        "source_kind": "SIMULATED", "stale": False, "drone_pose": None,
        "flight_path": [], "hazards": [], "active_mission_id": None, "active_task_id": None,
    }


def _force_run_envelope(payload):
    return {
        "contract_version": "sentinel.v0", "wire_type": "command_envelope",
        "command_id": UUID, "correlation_id": UUID,
        "command_type": "FORCE_RUN_CREATE", "run_id": UUID,
        "actor_type": "OPERATOR", "actor_id": "colin",
        "issued_at": TS, "idempotency_key": "k" * 16,
        "expected_state_version": 3, "payload": payload,
    }


def test_six_zone_mountain_snapshot_is_wire_valid():
    schema = _load("sentinel-v0-wire.schema.json")
    # guard the cardinality gap that regressed the wire contract to the three-section model
    sections = schema["$defs"]["MountainStateSnapshot"]["allOf"][1]["properties"]["sections"]
    assert sections["minItems"] == 6 and sections["maxItems"] == 6
    errors = list(Draft202012Validator(schema).iter_errors(_six_zone_snapshot()))
    assert not errors, [error.message for error in errors]


def test_force_run_create_is_discriminated_to_typed_payload():
    validator = Draft202012Validator(_load("sentinel-v0-wire.schema.json"))
    plan = {
        "plan_id": UUID, "catalogue_id": "CAT_ALLEY", "catalogue_revision": "REV_1",
        "plan_sha256": "a" * 64,
        "station_ids": ["LEFT_ALLEY_MIDDLE", "RIGHT_ALLEY_MIDDLE"],
    }
    good = _force_run_envelope({
        "prior_run_id": UUID, "failed_report_id": UUID, "operator_reason": "retake",
        "new_plan_reference": plan, "real_confirm": True,
    })
    assert not list(validator.iter_errors(good)), "FORCE_RUN_CREATE with its own payload must validate"
    bad = _force_run_envelope({"task_id": UUID, "decision": "CONFIRM", "reason": None})
    assert list(validator.iter_errors(bad)), "FORCE_RUN_CREATE with a foreign payload must be rejected"
