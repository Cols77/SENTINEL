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
    assert proposal["allOf"][1]["properties"]["catalogue_plan_id"]["type"] == "string"


def test_preflight_has_named_structured_checks():
    defs = _load("sentinel-v0-wire.schema.json")["$defs"]
    checks = defs["PreflightReport"]["properties"]["checks"]
    assert checks["minItems"] == 8
    assert set(checks["items"]["properties"]) >= {"name", "passed", "checked_at"}


def test_checked_in_live_perception_fixtures_validate():
    schema = _load("sentinel-v0-wire.schema.json")
    validator = Draft202012Validator(schema)
    fixture_dir = CONTRACTS / "fixtures"
    for name in ("judged-perception-live.valid.json",):
        errors = list(validator.iter_errors(json.loads((fixture_dir / name).read_text(encoding="utf-8"))))
        assert not errors, [error.message for error in errors]
