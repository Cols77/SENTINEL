# src/sentinel/survey/store.py
"""File-backed SQLite store for the survey flow. Owns every write."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = Path(__file__).with_name("sql") / "schema.sql"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


class SurveyStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @classmethod
    def open(cls, path: str | Path) -> "SurveyStore":
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(database_path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_SCHEMA.read_text(encoding="utf-8"))
        return cls(connection)

    def create_run(self, run_id: str, request_text: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO survey_runs(run_id, request_text, created_at) VALUES (?, ?, ?)",
                (run_id, request_text, utc_now()))

    def add_sweep(self, run_id: str, option_id: str, stops: int) -> str:
        sweep_id = new_id()
        with self.connection:
            self.connection.execute(
                "INSERT INTO sweeps(sweep_id, run_id, option_id, stops, created_at) "
                "VALUES (?, ?, ?, ?, ?)", (sweep_id, run_id, option_id, stops, utc_now()))
        return sweep_id

    def set_sweep_status(self, sweep_id: str, status: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE sweeps SET status = ? WHERE sweep_id = ?", (status, sweep_id))

    def add_frame(self, sweep_id: str, *, stop_index: int, look_deg: float,
                  x_cm: float, y_cm: float, z_cm: float, heading_deg: float,
                  position_kind: str, artifact_id: str, sha256: str,
                  image_path: str = "", captured_at: str | None = None,
                  command_completed_at: str | None = None, delta_z_cm: float = 0.0,
                  tolerance: str | None = None,
                  person_regions_blurred: bool = False) -> str:
        frame_id = new_id()
        with self.connection:
            self.connection.execute(
                "INSERT INTO frames(frame_id, sweep_id, stop_index, look_deg, x_cm, y_cm, "
                "z_cm, heading_deg, position_kind, artifact_id, sha256, image_path, "
                "captured_at, command_completed_at, delta_z_cm, tolerance, "
                "person_regions_blurred) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (frame_id, sweep_id, stop_index, look_deg, x_cm, y_cm, z_cm, heading_deg,
                 position_kind, artifact_id, sha256, image_path, captured_at or utc_now(),
                 command_completed_at, delta_z_cm, tolerance, int(person_regions_blurred)))
        return frame_id

    def set_frame_analysis(self, frame_id: str, status: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE frames SET analysis_status = ? WHERE frame_id = ?", (status, frame_id))

    def add_sighting(self, frame_id: str, detection: dict, band: str, *,
                      x_cm: float | None = None, y_cm: float | None = None,
                      depth_cm: float | None = None,
                      raw_response: dict | None = None) -> str:
        sighting_id = new_id()
        bbox = detection.get("bbox")
        with self.connection:
            self.connection.execute(
                "INSERT INTO sightings(sighting_id, frame_id, class, matched_prompt, raw_score, "
                "score_kind, bbox_json, description, provider_item_id, raw_response_json, "
                "band, x_cm, y_cm, depth_cm) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sighting_id, frame_id, detection["class"], detection.get("matched_prompt"),
                 detection.get("raw_score"), detection["score_kind"],
                 json.dumps(bbox) if bbox else None, detection.get("description"),
                 detection.get("provider_item_id"),
                 json.dumps(raw_response) if raw_response else None,
                 band, x_cm, y_cm, depth_cm))
        return sighting_id

    def frames(self, run_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT f.* FROM frames f JOIN sweeps s ON s.sweep_id = f.sweep_id "
            "WHERE s.run_id = ? ORDER BY f.stop_index, f.look_deg", (run_id,))
        return [dict(row) for row in rows]

    def sightings(self, run_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT g.*, f.stop_index, f.sweep_id, f.position_kind, "
            "f.x_cm AS frame_x_cm, f.y_cm AS frame_y_cm, "
            "f.heading_deg, f.look_deg, f.delta_z_cm "
            "FROM sightings g JOIN frames f ON f.frame_id = g.frame_id "
            "JOIN sweeps s ON s.sweep_id = f.sweep_id WHERE s.run_id = ?", (run_id,))
        return [dict(row) for row in rows]

    def flights_used(self, run_id: str) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) FROM sweeps WHERE run_id = ? AND status != 'ABORTED'",
            (run_id,)).fetchone()[0]

    def unanalysed_count(self, run_id: str) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) FROM frames f JOIN sweeps s ON s.sweep_id = f.sweep_id "
            "WHERE s.run_id = ? AND f.analysis_status != 'OK'", (run_id,)).fetchone()[0]

    def set_objects(self, run_id: str, objects: list[dict]) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM objects WHERE run_id = ?", (run_id,))
            self.connection.executemany(
                "INSERT INTO objects(object_id, run_id, class, x_cm, y_cm, distinct_stops, "
                "band, appearance, sighting_ids_json, depth_spread_pct) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(new_id(), run_id, o["class"], o.get("x_cm"), o.get("y_cm"),
                  o["distinct_stops"], o["band"], o.get("appearance"),
                  json.dumps(o["sighting_ids"]), o.get("depth_spread_pct"))
                 for o in objects])

    def counts(self, run_id: str) -> dict[str, dict]:
        rows = self.connection.execute(
            "SELECT class, band, COUNT(*) AS n FROM objects WHERE run_id = ? "
            "GROUP BY class, band", (run_id,))
        out: dict[str, dict] = {}
        for row in rows:
            entry = out.setdefault(row["class"], {"confirmed": 0, "uncertain": 0})
            entry["confirmed" if row["band"] == "CONFIRMED" else "uncertain"] = row["n"]
        return out

    def add_report(self, run_id: str, body: str, class_counts: list[dict]) -> str:
        report_id = new_id()
        with self.connection:
            self.connection.execute(
                "INSERT INTO reports(report_id, run_id, body, class_counts_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (report_id, run_id, body, json.dumps(class_counts), utc_now()))
        return report_id
