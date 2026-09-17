"""Persistence for immutable resolved catalogue plans."""

from __future__ import annotations

import json
from typing import Mapping

from .catalogue import CatalogueResolver
from .events import utc_now


class PlanResolver:
    def __init__(self, repository, catalogue: CatalogueResolver):
        self.repository = repository
        self.catalogue = catalogue

    def resolve(
        self,
        run_id: str,
        plan_id: str,
        catalogue_id: str,
        entry_id: str,
    ) -> Mapping[str, object]:
        plan = self.catalogue.resolve(catalogue_id, entry_id, plan_id)
        entry = self.catalogue.entry(entry_id)
        station_ids = list(plan["station_ids"])
        with self.repository.connection:
            self.repository.connection.execute(
                """INSERT INTO plans(
                   plan_id, run_id, catalogue_id, catalogue_revision, plan_sha256,
                   mode, station_ids_json, canonical_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan["plan_id"],
                    run_id,
                    plan["catalogue_id"],
                    plan["catalogue_revision"],
                    plan["plan_sha256"],
                    entry["mode"],
                    json.dumps(station_ids, separators=(",", ":")),
                    json.dumps(
                        {
                            "plan_id": plan["plan_id"],
                            "catalogue_id": plan["catalogue_id"],
                            "catalogue_revision": plan["catalogue_revision"],
                            "station_ids": station_ids,
                        },
                        separators=(",", ":"),
                    ),
                    utc_now(),
                ),
            )
        return plan


def resolve_catalogue_plan(repository, run_id: str, fixture, plan_id: str, catalogue_id: str, entry_id: str):
    """Convenience entry point that persists only after successful resolution."""
    return PlanResolver(repository, CatalogueResolver.from_fixture(fixture)).resolve(
        run_id, plan_id, catalogue_id, entry_id
    )
