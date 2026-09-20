"""The draft rich catalogue is content for operator review. No production path may read it."""
from __future__ import annotations

import json
import re
from pathlib import Path

from sentinel.coordinator.legal_tools import load_catalogue_plans

ROOT = Path(__file__).resolve().parents[2]
DRAFT_CATALOGUE_PATH = ROOT / "fixtures/catalogue_draft/survey-catalogue-v2.draft.json"
STATIONS = ("LEFT_ALLEY_FRONT", "LEFT_ALLEY_MIDDLE", "LEFT_ALLEY_BACK",
            "RIGHT_ALLEY_FRONT", "RIGHT_ALLEY_MIDDLE", "RIGHT_ALLEY_BACK")
EXPECTED_IDS = {
    *(f"PLAN_ROUTE_{s}" for s in STATIONS),
    "PLAN_ROUTE_LEFT_ALLEY", "PLAN_ROUTE_RIGHT_ALLEY",
    "PLAN_ROUTE_FRONT_PAIR", "PLAN_ROUTE_MIDDLE_PAIR", "PLAN_ROUTE_BACK_PAIR",
    "PLAN_VERIFIED_LEFT_ALLEY", "PLAN_VERIFIED_RIGHT_ALLEY", "PLAN_VERIFIED_FULL_SITE",
}


def test_draft_loads_through_the_production_loader_and_has_the_14_plans():
    plans = load_catalogue_plans(DRAFT_CATALOGUE_PATH)
    assert {p.plan_id for p in plans} == EXPECTED_IDS


def test_no_plan_id_contains_a_bare_count():
    for plan in load_catalogue_plans(DRAFT_CATALOGUE_PATH):
        assert not re.search(r"_\d+(_|$)", plan.plan_id), plan.plan_id


def test_every_description_names_its_stations():
    doc = json.loads(DRAFT_CATALOGUE_PATH.read_text(encoding="utf-8"))
    by_id = {p.plan_id: p for p in load_catalogue_plans(DRAFT_CATALOGUE_PATH)}
    for entry in doc["entries"]:
        for step in entry["station_steps"]:
            assert step["zone_id"] in by_id[entry["id"]].description


def test_verified_plans_carry_markers_and_route_plans_do_not():
    doc = json.loads(DRAFT_CATALOGUE_PATH.read_text(encoding="utf-8"))
    for entry in doc["entries"]:
        markers = [step.get("marker_id") for step in entry["station_steps"]]
        if entry["mode"] == "MARKER_VERIFIED":
            assert all(markers)
        else:
            assert entry["mode"] == "ROUTE_ONLY" and not any(markers)


def test_draft_is_marked_draft_and_production_default_is_still_v1():
    doc = json.loads(DRAFT_CATALOGUE_PATH.read_text(encoding="utf-8"))
    assert doc["revision"] == "DRAFT_REV_2"
    assert {p.plan_id for p in load_catalogue_plans()} == {"PLAN_MARKER_VERIFIED_6", "PLAN_ROUTE_ONLY_2_ZONE"}


def test_no_source_file_references_the_draft():
    offenders = [str(p) for p in (ROOT / "src").rglob("*.py") if "catalogue_draft" in p.read_text(encoding="utf-8")]
    assert offenders == []
