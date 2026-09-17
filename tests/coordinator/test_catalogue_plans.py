from pathlib import Path

import pytest

from sentinel.coordinator.catalogue import CatalogueResolver
from sentinel.coordinator.errors import CoordinatorError
from sentinel.coordinator.plans import PlanResolver
from sentinel.coordinator.repository import CoordinatorRepository

RUN_ID = "11111111-1111-4111-8111-111111111111"
PLAN_ID = "55555555-5555-4555-8555-555555555555"
FIXTURE = Path("docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json")


def test_known_catalogue_resolves_immutable_ordered_plan_with_stable_hash(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "plans.sqlite3")
    repository.create_run(RUN_ID)
    catalogue = CatalogueResolver.from_fixture(FIXTURE)
    first = catalogue.resolve("SURVEY_CATALOGUE_V1", "PLAN_MARKER_VERIFIED_6", PLAN_ID)
    second = catalogue.resolve("SURVEY_CATALOGUE_V1", "PLAN_MARKER_VERIFIED_6", PLAN_ID)
    PlanResolver(repository, catalogue).resolve(RUN_ID, PLAN_ID, "SURVEY_CATALOGUE_V1", "PLAN_MARKER_VERIFIED_6")

    assert list(first["station_ids"]) == [
        "LEFT_ALLEY_FRONT", "LEFT_ALLEY_MIDDLE", "LEFT_ALLEY_BACK",
        "RIGHT_ALLEY_FRONT", "RIGHT_ALLEY_MIDDLE", "RIGHT_ALLEY_BACK",
    ]
    assert first["plan_sha256"] == second["plan_sha256"]
    assert repository.connection.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1
    repository.close()


def test_unknown_catalogue_id_is_rejected_without_persistence(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "plans.sqlite3")
    repository.create_run(RUN_ID)
    resolver = PlanResolver(repository, CatalogueResolver.from_fixture(FIXTURE))

    with pytest.raises(CoordinatorError) as caught:
        resolver.resolve(RUN_ID, PLAN_ID, "UNKNOWN_CATALOGUE", "PLAN_MARKER_VERIFIED_6")

    assert caught.value.code == "INVALID_CONTRACT"
    assert repository.connection.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0
    repository.close()
