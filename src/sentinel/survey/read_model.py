"""Backend-owned, allowlisted survey read model.

The UI consumes this projection instead of querying ``SurveyStore``'s write
tables or using its private/raw helpers. Every query is scoped by ``run_id``.
"""
from __future__ import annotations

import json
from typing import Any


class SurveyReadModel:
    def __init__(self, store) -> None:
        self.store = store

    def read(self, run_id: str) -> dict[str, Any] | None:
        run = self.store.connection.execute(
            "SELECT run_id, request_text, status, created_at FROM survey_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        return {
            "run": dict(run),
            "sweeps": self._sweeps(run_id),
            "frames": self._frames(run_id),
            "sightings": self._sightings(run_id),
            "objects": self._objects(run_id),
            "counts": self.store.counts(run_id),
            "report": self._report(run_id),
        }

    def _sweeps(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT sweep_id, option_id, stops, status, created_at FROM sweeps "
            "WHERE run_id = ? ORDER BY created_at, sweep_id", (run_id,))
        return [dict(row) for row in rows]

    def _frames(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT f.frame_id, f.sweep_id, f.stop_index, f.look_deg, f.x_cm, f.y_cm, "
            "f.z_cm, f.heading_deg, f.position_kind, f.artifact_id, f.sha256, "
            "f.captured_at, f.command_completed_at, f.delta_z_cm, f.tolerance, "
            "f.analysis_status, f.person_regions_blurred "
            "FROM frames f JOIN sweeps s ON s.sweep_id = f.sweep_id "
            "WHERE s.run_id = ? ORDER BY f.stop_index, f.look_deg, f.frame_id", (run_id,))
        return [dict(row) for row in rows]

    def _sightings(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT g.sighting_id, g.frame_id, g.class, g.raw_score, g.score_kind, "
            "g.bbox_json, g.band, g.x_cm, g.y_cm, g.depth_cm "
            "FROM sightings g JOIN frames f ON f.frame_id = g.frame_id "
            "JOIN sweeps s ON s.sweep_id = f.sweep_id WHERE s.run_id = ? "
            "ORDER BY g.sighting_id", (run_id,))
        out = []
        for row in rows:
            item = dict(row)
            item["bbox"] = json.loads(item.pop("bbox_json")) if item["bbox_json"] else None
            out.append(item)
        return out

    def _objects(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT object_id, class, x_cm, y_cm, distinct_stops, band, "
            "sighting_ids_json, depth_spread_pct FROM objects WHERE run_id = ? "
            "ORDER BY object_id", (run_id,))
        out = []
        for row in rows:
            item = dict(row)
            item["sighting_ids"] = json.loads(item.pop("sighting_ids_json"))
            out.append(item)
        return out

    def _report(self, run_id: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT report_id, body, class_counts_json, created_at FROM reports "
            "WHERE run_id = ? ORDER BY created_at DESC, report_id DESC LIMIT 1", (run_id,)
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["class_counts"] = json.loads(item.pop("class_counts_json"))
        return item

