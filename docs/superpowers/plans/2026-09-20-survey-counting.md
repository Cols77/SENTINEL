# Survey and Counting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One agentic loop that proposes a rail sweep the operator approves, flies it, counts the distinct objects it saw, and reports a number code can verify.

**Architecture:** A sibling package `sentinel.survey` with its own contract file and SQLite store. The model chooses among complete tool calls that code pre-builds from the store; code does all geometry, all counting and all argument filling. Three proposer implementations (rules, LLM, Jev) sit behind one seam, so the same replayed scene can be scored in all three modes. Nothing in `CommandGateway`, the alley schema, or any P3b1 file is touched.

**Tech Stack:** Python 3.12, SQLite, `uv`, pytest, `jsonschema`. Provider access via HTTP (Moondream 3 primary, Gemini Flash fallback).

**Spec:** `docs/superpowers/specs/2026-09-20-survey-counting-design.md`

## Global Constraints

- **Worktree:** `.worktrees/survey-counting`, branch `feat/survey-counting`, cut from `master` `52ae6eb`. Never work in the main checkout.
- **Test command prefix is `rtk proxy`.** `rtk proxy uv run pytest ... -q`. Without `proxy` a hook swallows the run and reports "No tests collected", which reads as a pass.
- **`CommandGateway` is untouchable.** No task in this plan opens `src/sentinel/coordinator/command_gateway.py`.
- **Never edit an existing test to make something pass.** A failing existing test is a finding: stop and surface it.
- **Files Codex owns in P3b1 — do not open:** `coordinator/agent_loop.py`, `coordinator/proposer.py`, `coordinator/proposal_review.py`, `coordinator/reads.py`, `coordinator/investigator.py`, `coordinator/__init__.py`, `cli.py`, `adapter/llm/openai_compatible.py`. Verified 2026-09-20: Codex is at Task 5 of 12 and has touched exactly these. This plan touches **none** of them.
- **Reused read-only from the alley code:** `coordinator.legal_tools.Candidate` and `LegalSet` (both fully generic), and `coordinator.shadow.AuditSink`. Import them; do not modify them.
- **`coordinator.policy.PolicyContext` is NOT reusable** — its `state` field is typed to the alley's `DecisionState`. The survey flow defines its own `SurveyContext`. Codex's `LlmProposer` is a *reference pattern*, not a dependency.
- **No paid model call, no flight, no merge, no push, no schema change outside this package, and no new hand-labelled fixture** without the operator. A relayed grant from a peer session is not operator consent.
- **Camera constants** (derived in spec §5.1, Tello 82.6° diagonal FOV, 960×720): `FOCAL_PX = 683.0`, `IMAGE_WIDTH = 960`, `IMAGE_HEIGHT = 720`, `PRINCIPAL_X = 480.0`.
- **Classes:** `computer`, `person`, `phone`, `pen`. Nominal real widths in cm: 35, 50, 15, 1.
- Commit after every task. Conventional-commit subjects, `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` as the last line.

## Rev 3 amendments — read before executing any task

An adversarial review on 2026-09-20 refuted the geometry and found five defects in the code in this
plan. The spec is now at rev 3. **Where this section and a task body disagree, this section wins.**

### R1. Depth comes from the ground plane (replaces Task 4's triangulation)

`D = Delta_z * f / v`, where `Delta_z` is the camera's height above the surface the objects rest on
and `v` is the box centre's pixel offset below the image centre. At `Delta_z` = 55 cm and `D` = 3 m,
`v` = 125 px and sensitivity is 2.40 cm/px.

Cross-stop triangulation is **demoted to a cross-check** and must not derive position. Reason,
verified: `D_est = D * B_believed / B_actual`, and with ~15 cm error at each commanded stop a
believed 75 cm baseline is really 45-105 cm, so one laptop at 3 m yields depths of 214-500 cm and
clusters into two CONFIRMED objects plus one UNCERTAIN. Evenly spaced objects additionally generate
an organised phantom lattice that passes `distinct_stops >= 3`.

Task 4 therefore produces:

- `ground_depth(bbox, delta_z_cm) -> float` and `position_of(sighting, delta_z_cm) -> (x_cm, y_cm)`;
- `associate(sightings) -> list[dict]` using **mutual-nearest-neighbour** assignment, so each
  sighting belongs to **exactly one** object;
- `depth_spread_pct` per object; over 30 % marks it UNCERTAIN and flags `geometry_degraded`;
- if over half the objects are degraded, the sweep reports `SINGLE_VIEW_LOWER_BOUND` for every class.

`ray_intersection` and `bearing_of` stay; `_cluster` as written in Task 4 is **deleted**, not fixed.

### R2. Schema additions (Task 1) — all under consent C-20

- `frames`: `image_path TEXT NOT NULL`, `captured_at TEXT NOT NULL`,
  `command_completed_at TEXT`, `delta_z_cm REAL NOT NULL`
- `sightings`: `band TEXT NOT NULL`, `x_cm REAL`, `y_cm REAL`, `depth_cm REAL`
- `objects`: `depth_spread_pct REAL`
- `sweeps.status` gains `ABORTED`; `flights_used` counts only non-`ABORTED` sweeps

### R3. The band gate was dead code (Tasks 1, 7)

`loop.py:_fly_and_ingest` set `row["band"] = "high"` on every row, so the non-low-band rule was
enforced only in unit tests. Store `band` at ingest and read it back. Add a test that a `low` band
sighting cannot produce a CONFIRMED object **through the real store**, not through hand-built dicts.

### R4. `check_report` scoping (Task 7)

As written it rejects **any** integer not in the permitted set, so the spec's own prescribed wording
"6 of 8 frames analysed" is rejected and the run ends with no report at all. Match only numbers
adjacent to a class noun:

```python
_CLASS_NUMBER = re.compile(r"(\d+)\s+(computers?|people|persons?|phones?|pens?)", re.IGNORECASE)
```

Allow the writer **one** rewrite attempt before `REPORT_REJECTED`. Add a test using §8's exact
sentence. Also fix `test_claiming_certainty_about_a_not_observable_class_is_rejected`, whose "3 pens"
already fails the number check and so never exercises the pen rule it names.

### R5. Consent is now refuse-or-allow, never blur (Task 8)

`blur_person_regions` is **deleted**. It nulled two text fields *after* the frame had been written
and sent, so rev 2's guarantee was false. Replace with:

| Consent state | Behaviour |
|---|---|
| recorded for this location and date | frames sent and retained |
| absent | **no frame leaves the process, none is retained**; the run ends `CONSENT_REQUIRED` before any capture is written |

`consent_policy` returns `RETAIN` or raises `ConsentMissing`. There is no third value.

### R6. Class map misses must be loud (Task 3)

An unmapped label is currently `continue`d with no trace, so a model that finds every laptop under
the label `macbook` reports an empty scene. `normalise` returns dropped labels alongside
detections; `DetectionBatch` gains `dropped: tuple[str, ...]`. If dropped exceeds half the returned
items, the frame's status is `CLASS_MAP_MISS`. **Remove `"monitor"` from `CLASS_MAP`** — in a
lecture room it turns every external display and the projector screen into a computer.

### R7. `on_approval` receives the rendered candidate (Task 7)

Pass the full `Candidate.description` — stops, extent, range, resolvable classes, duration — not
just `{option_id, reason}`. Approving a bare token is not approval, and this is the design's central
safety claim.

### R8. Task 9 scoring gains `depth_spread_pct` and `dropped_labels`

Both are attribution signals: they separate "the provider missed it" from "the geometry failed".

## Dependency and gate map

| Tasks | Blocked on |
|---|---|
| 1–10 | **nothing.** No WP5, no Codex, no paid call, no flight. Start immediately. |
| 11 | **WP5 merged to `master`** (operator gate). The only task importing `sentinel.cube`. |
| 12 | **G-provider-spike** — ~50 paid calls (operator gate). |
| 13 | Tasks 11 and 12. |

The dependency on WP5 is confined to one module by design (Task 2 defines `SweepEnvelope` as a plain dataclass; Task 11 adds the only `sentinel.cube` importer). If WP5 slips, Tasks 1–10 and 12 still deliver a complete replay-driven system.

### Codex dependency, verified 2026-09-20 at `ae2a617`

**File overlap is empty.** Codex's branch touches ten files: `coordinator/{proposer,proposal_review,__init__}.py`, `cli.py`, `adapter/llm/openai_compatible.py` and their five test files. This plan creates `src/sentinel/survey/**`, `tests/survey/**`, `scripts/survey_run.py`, `fixtures/survey/**` and docs. Nothing intersects, so the two branches merge without conflict in either order.

**One runtime coupling exists and is not avoidable.** Codex added `from .proposer import LlmProposer` to `coordinator/__init__.py`. Python executes that package `__init__` before any submodule, so every survey import of `coordinator.legal_tools` or `coordinator.shadow` also imports Codex's `proposer.py` and `agent_loop.py`. Consequence: **an import-time error in Codex's code fails the survey suite too.** Mitigation, not avoidance:

- run `rtk proxy uv run pytest tests/survey -q` as the survey signal, so a failure there is attributable to this package;
- if the survey suite fails with an error raised from a `coordinator/` module, that is a **Codex-side finding**: report it, do not patch their file.

## File structure

```text
src/sentinel/survey/
  __init__.py          exports the public surface
  envelope.py          SweepEnvelope, SweepOption  (Task 2)
  options.py           generate_options()          (Task 2)
  observability.py     pixels_at(), resolvability() (Task 2)
  detector.py          DetectorPort, ScriptedDetector, ReplayDetector (Task 3)
  geometry.py          bearing_of(), ray_intersection()             (Task 4)
  merge.py             merge_sightings()                            (Task 4)
  candidates.py        propose_candidates(), review_candidates()     (Task 5)
  context.py           SurveyContext, SurveyPolicy                   (Task 5)
  proposer.py          RuleProposer, JevProposer, LlmProposer        (Task 6)
  report.py            check_report()                                (Task 7)
  loop.py              run_survey()                                  (Task 7)
  consent.py           filming consent + blur_person_regions()       (Task 8)
  flight.py            FlightPort, ReplayFlight                      (Task 9)
  eval.py              score_episode(), compare_modes()              (Task 9)
  store.py             SurveyStore                                   (Task 1)
  sql/schema.sql       tables                                        (Task 1)
  cube_bridge.py       envelope_from_site(), CubeFlight   (Task 11, WP5 gate)
  providers/moondream.py, providers/gemini.py               (Task 12, paid gate)
docs/superpowers/specs/contracts/sentinel-survey-v1.schema.json     (Task 1)
scripts/survey_run.py                                                (Task 10)
tests/survey/…
fixtures/survey/scenes/, fixtures/survey/frames/, fixtures/survey/responses/
```

---

### Task 1: Contract and store

**Files:**
- Create: `docs/superpowers/specs/contracts/sentinel-survey-v1.schema.json`
- Create: `src/sentinel/survey/__init__.py`
- Create: `src/sentinel/survey/sql/schema.sql`
- Create: `src/sentinel/survey/store.py`
- Test: `tests/survey/test_store.py`, `tests/survey/test_survey_contract.py`

**Interfaces — Consumes:** nothing.

**Interfaces — Produces:** `SurveyStore` with `open(path)`, `create_run(run_id, request_text)`, `add_sweep(run_id, option_id, stops)`, `add_frame(...) -> str`, `add_sighting(frame_id, detection) -> str`, `set_objects(run_id, objects)`, `counts(run_id) -> dict[str, dict]`, `add_report(run_id, body, class_counts)`, `flights_used(run_id) -> int`, `frames(run_id) -> list[dict]`, `sightings(run_id) -> list[dict]`.

- [ ] **Step 1: Write the failing store test**

```python
# tests/survey/test_store.py
from sentinel.survey.store import SurveyStore


def test_a_frame_records_whether_its_position_was_commanded_or_measured(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count the computers")
    sweep_id = store.add_sweep("RUN1", "RAIL_MID_HEIGHT", stops=4)
    frame_id = store.add_frame(sweep_id, stop_index=0, look_deg=0, x_cm=0, y_cm=0,
                               z_cm=120, heading_deg=0.0, position_kind="COMMANDED",
                               artifact_id="A1", sha256="0" * 64)
    rows = store.frames("RUN1")
    assert [r["position_kind"] for r in rows] == ["COMMANDED"]
    assert rows[0]["frame_id"] == frame_id
    assert rows[0]["analysis_status"] == "PENDING"


def test_flights_used_counts_sweeps_not_frames(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count the computers")
    first = store.add_sweep("RUN1", "RAIL_MID_HEIGHT", stops=4)
    for index in range(4):
        store.add_frame(first, stop_index=index, look_deg=0, x_cm=index * 75, y_cm=0,
                        z_cm=120, heading_deg=0.0, position_kind="COMMANDED",
                        artifact_id=f"A{index}", sha256="0" * 64)
    assert store.flights_used("RUN1") == 1
    store.add_sweep("RUN1", "CLOSE_LOOK_LEFT", stops=2)
    assert store.flights_used("RUN1") == 2


def test_an_unanalysed_frame_is_visible_as_such(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "r")
    sweep_id = store.add_sweep("RUN1", "RAIL_MID_HEIGHT", stops=1)
    frame_id = store.add_frame(sweep_id, stop_index=0, look_deg=0, x_cm=0, y_cm=0,
                               z_cm=120, heading_deg=0.0, position_kind="COMMANDED",
                               artifact_id="A1", sha256="0" * 64)
    store.set_frame_analysis(frame_id, "PROVIDER_ERROR")
    assert store.unanalysed_count("RUN1") == 1
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_store.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'sentinel.survey'`.

- [ ] **Step 3: Write the schema SQL**

```sql
-- src/sentinel/survey/sql/schema.sql
CREATE TABLE IF NOT EXISTS survey_runs (
    run_id TEXT PRIMARY KEY,
    request_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sweeps (
    sweep_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES survey_runs(run_id),
    option_id TEXT NOT NULL,
    stops INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS frames (
    frame_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES sweeps(sweep_id),
    stop_index INTEGER NOT NULL,
    look_deg REAL NOT NULL,
    x_cm REAL NOT NULL, y_cm REAL NOT NULL, z_cm REAL NOT NULL,
    heading_deg REAL NOT NULL,
    position_kind TEXT NOT NULL CHECK (position_kind IN ('COMMANDED', 'MEASURED')),
    artifact_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    analysis_status TEXT NOT NULL DEFAULT 'PENDING',
    person_regions_blurred INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sightings (
    sighting_id TEXT PRIMARY KEY,
    frame_id TEXT NOT NULL REFERENCES frames(frame_id),
    class TEXT NOT NULL,
    matched_prompt TEXT,
    raw_score REAL,
    score_kind TEXT NOT NULL,
    bbox_json TEXT,
    description TEXT,
    provider_item_id TEXT,
    raw_response_json TEXT
);
CREATE TABLE IF NOT EXISTS objects (
    object_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES survey_runs(run_id),
    class TEXT NOT NULL,
    x_cm REAL, y_cm REAL,
    distinct_stops INTEGER NOT NULL,
    band TEXT NOT NULL CHECK (band IN ('CONFIRMED', 'UNCERTAIN')),
    appearance TEXT,
    sighting_ids_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES survey_runs(run_id),
    body TEXT NOT NULL,
    class_counts_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS filming_consent (
    location TEXT NOT NULL,
    consent_date TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    PRIMARY KEY (location, consent_date)
);
CREATE TABLE IF NOT EXISTS survey_events (
    sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

- [ ] **Step 4: Implement `SurveyStore`**

Follow `src/sentinel/coordinator/repository.py`'s pattern exactly: own one connection, apply the schema on open, `row_factory = sqlite3.Row`, WAL, `foreign_keys = ON`, `busy_timeout = 5000`.

```python
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

    def add_frame(self, sweep_id: str, *, stop_index: int, look_deg: float,
                  x_cm: float, y_cm: float, z_cm: float, heading_deg: float,
                  position_kind: str, artifact_id: str, sha256: str,
                  person_regions_blurred: bool = False) -> str:
        frame_id = new_id()
        with self.connection:
            self.connection.execute(
                "INSERT INTO frames(frame_id, sweep_id, stop_index, look_deg, x_cm, y_cm, "
                "z_cm, heading_deg, position_kind, artifact_id, sha256, person_regions_blurred) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (frame_id, sweep_id, stop_index, look_deg, x_cm, y_cm, z_cm, heading_deg,
                 position_kind, artifact_id, sha256, int(person_regions_blurred)))
        return frame_id

    def set_frame_analysis(self, frame_id: str, status: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE frames SET analysis_status = ? WHERE frame_id = ?", (status, frame_id))

    def add_sighting(self, frame_id: str, detection: dict, raw_response: dict | None = None) -> str:
        sighting_id = new_id()
        bbox = detection.get("bbox")
        with self.connection:
            self.connection.execute(
                "INSERT INTO sightings(sighting_id, frame_id, class, matched_prompt, raw_score, "
                "score_kind, bbox_json, description, provider_item_id, raw_response_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sighting_id, frame_id, detection["class"], detection.get("matched_prompt"),
                 detection.get("raw_score"), detection["score_kind"],
                 json.dumps(bbox) if bbox else None, detection.get("description"),
                 detection.get("provider_item_id"),
                 json.dumps(raw_response) if raw_response else None))
        return sighting_id

    def frames(self, run_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT f.* FROM frames f JOIN sweeps s ON s.sweep_id = f.sweep_id "
            "WHERE s.run_id = ? ORDER BY f.stop_index, f.look_deg", (run_id,))
        return [dict(row) for row in rows]

    def sightings(self, run_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT g.*, f.stop_index, f.x_cm, f.y_cm, f.heading_deg, f.look_deg "
            "FROM sightings g JOIN frames f ON f.frame_id = g.frame_id "
            "JOIN sweeps s ON s.sweep_id = f.sweep_id WHERE s.run_id = ?", (run_id,))
        return [dict(row) for row in rows]

    def flights_used(self, run_id: str) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) FROM sweeps WHERE run_id = ?", (run_id,)).fetchone()[0]

    def unanalysed_count(self, run_id: str) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) FROM frames f JOIN sweeps s ON s.sweep_id = f.sweep_id "
            "WHERE s.run_id = ? AND f.analysis_status NOT IN ('OK',)", (run_id,)).fetchone()[0]

    def set_objects(self, run_id: str, objects: list[dict]) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM objects WHERE run_id = ?", (run_id,))
            self.connection.executemany(
                "INSERT INTO objects(object_id, run_id, class, x_cm, y_cm, distinct_stops, "
                "band, appearance, sighting_ids_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(new_id(), run_id, o["class"], o.get("x_cm"), o.get("y_cm"),
                  o["distinct_stops"], o["band"], o.get("appearance"),
                  json.dumps(o["sighting_ids"])) for o in objects])

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
```

Add `src/sentinel/survey/__init__.py` containing only a docstring for now.

- [ ] **Step 5: Run the store tests**

Run: `rtk proxy uv run pytest tests/survey/test_store.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 6: Write the contract and its round-trip test**

Create `sentinel-survey-v1.schema.json` with `$defs` for `Detection`, `Sweep`, `Frame`, `Sighting`, `Object`, `ClassCount` and `Report`. `Detection` is copied **verbatim** from `docs/SENTINEL_Perception_Pipeline_Spec_v0.1.md` §4.1 — `class`, `matched_prompt`, `raw_score` (nullable), `score_kind` (enum `DETECTOR_RAW`, `VLM_CONFIDENCE_BAND`, `NONE`), `bbox` (nullable object of `x`/`y`/`width`/`height`), `bbox_coordinate_space` (const `PIXELS_TOP_LEFT`), `description`, `provider_item_id`. `ClassCount.method` is an enum of `MERGED_MULTI_VIEW`, `SINGLE_VIEW_LOWER_BOUND`, `NOT_OBSERVABLE`. Every object sets `additionalProperties: false`.

```python
# tests/survey/test_survey_contract.py
import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "docs" / "superpowers" / "specs"
                     / "contracts" / "sentinel-survey-v1.schema.json").read_text(encoding="utf-8"))


def _validate(name, doc):
    jsonschema.validate(doc, {**SCHEMA, "$ref": f"#/$defs/{name}"})


def test_a_detection_without_a_box_is_valid_because_boxes_are_never_manufactured():
    _validate("Detection", {"class": "computer", "matched_prompt": "laptop",
                            "raw_score": None, "score_kind": "NONE", "bbox": None,
                            "bbox_coordinate_space": "PIXELS_TOP_LEFT",
                            "description": "a laptop on the near table", "provider_item_id": None})


def test_a_vlm_band_is_a_legal_score_kind_and_a_detector_raw_score_is_too():
    for kind in ("VLM_CONFIDENCE_BAND", "DETECTOR_RAW", "NONE"):
        _validate("Detection", {"class": "computer", "matched_prompt": "laptop",
                                "raw_score": 0.9, "score_kind": kind,
                                "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                                "bbox_coordinate_space": "PIXELS_TOP_LEFT",
                                "description": "d", "provider_item_id": "p"})


def test_an_unknown_class_is_rejected():
    with pytest.raises(jsonschema.ValidationError):
        _validate("Detection", {"class": "rock", "matched_prompt": "rock", "raw_score": None,
                                "score_kind": "NONE", "bbox": None,
                                "bbox_coordinate_space": "PIXELS_TOP_LEFT",
                                "description": "d", "provider_item_id": None})


def test_a_class_count_must_declare_how_it_was_obtained():
    with pytest.raises(jsonschema.ValidationError):
        _validate("ClassCount", {"class": "computer", "confirmed": 3, "uncertain": 1})
```

- [ ] **Step 7: Run the contract tests**

Run: `rtk proxy uv run pytest tests/survey/test_survey_contract.py -q`
Expected: PASS, 4 tests.

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/specs/contracts/sentinel-survey-v1.schema.json src/sentinel/survey tests/survey
git commit -m "feat(survey): contract and store"
```

---

### Task 2: Envelope, sweep options, and observability

**Files:**
- Create: `src/sentinel/survey/envelope.py`, `src/sentinel/survey/observability.py`, `src/sentinel/survey/options.py`
- Test: `tests/survey/test_options.py`, `tests/survey/test_observability.py`

**Interfaces — Consumes:** nothing from Task 1.

**Interfaces — Produces:**
- `SweepEnvelope(min_x_cm, max_x_cm, min_y_cm, max_y_cm, min_z_cm, max_z_cm, standoff_cm)` — a plain frozen dataclass with **no `sentinel.cube` import**, so Tasks 2–10 never depend on WP5.
- `SweepOption(option_id, description, stops, z_cm, looks_deg, viewed_width_cm, estimated_seconds, resolvable_classes, range_cm)` where `stops` is `tuple[tuple[int, int], ...]` of `(x_cm, y_cm)`.
- `generate_options(envelope: SweepEnvelope) -> tuple[SweepOption, ...]`
- `pixels_at(class_name: str, range_cm: float) -> float`
- `resolvable_classes(range_cm: float) -> tuple[str, ...]`

- [ ] **Step 1: Write the failing observability test**

The numbers come from spec §5.1: `pixels = width_cm * 683.0 / range_cm`.

```python
# tests/survey/test_observability.py
import pytest

from sentinel.survey.observability import MIN_DETECTABLE_PX, pixels_at, resolvable_classes


@pytest.mark.parametrize("class_name, expected_px", [
    ("person", 113.8), ("computer", 79.7), ("phone", 34.2), ("pen", 2.3),
])
def test_apparent_size_at_three_metres_matches_the_spec(class_name, expected_px):
    assert pixels_at(class_name, 300.0) == pytest.approx(expected_px, abs=0.2)


def test_a_pen_is_never_resolvable_at_demo_range():
    assert "pen" not in resolvable_classes(300.0)
    assert pixels_at("pen", 300.0) < MIN_DETECTABLE_PX


def test_moving_closer_can_make_a_phone_resolvable():
    """This is what gives a follow-up sweep a real reason to exist."""
    assert "phone" not in resolvable_classes(600.0)
    assert "phone" in resolvable_classes(150.0)


def test_an_unknown_class_raises_rather_than_guessing():
    with pytest.raises(KeyError):
        pixels_at("bicycle", 300.0)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_observability.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement observability**

```python
# src/sentinel/survey/observability.py
"""Whether a class can be resolved at all at a given range.

Derived in the design spec section 5.1 from the Tello's 82.6 degree
*diagonal* field of view over a 960x720 frame: half-diagonal 41.3 degrees
against a 600 px image half-diagonal gives f = 600 / tan(41.3) = 683 px.
An object of real width S at range D spans S * f / D pixels.
"""
from __future__ import annotations

FOCAL_PX = 683.0
IMAGE_WIDTH = 960
IMAGE_HEIGHT = 720
PRINCIPAL_X = IMAGE_WIDTH / 2.0

#: Nominal real width in centimetres, per class.
NOMINAL_WIDTH_CM = {"person": 50.0, "computer": 35.0, "phone": 15.0, "pen": 1.0}

#: Below this many pixels across, no detector we have measured returns a
#: usable box. Revisit only with measured evidence from Task 12.
MIN_DETECTABLE_PX = 12.0


def pixels_at(class_name: str, range_cm: float) -> float:
    if range_cm <= 0:
        raise ValueError("range must be positive")
    return NOMINAL_WIDTH_CM[class_name] * FOCAL_PX / range_cm


def resolvable_classes(range_cm: float) -> tuple[str, ...]:
    return tuple(name for name in NOMINAL_WIDTH_CM
                 if pixels_at(name, range_cm) >= MIN_DETECTABLE_PX)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `rtk proxy uv run pytest tests/survey/test_observability.py -q`
Expected: PASS, 6 tests.

- [ ] **Step 5: Write the failing options test**

```python
# tests/survey/test_options.py
from sentinel.survey.envelope import SweepEnvelope
from sentinel.survey.options import generate_options

ENVELOPE = SweepEnvelope(min_x_cm=-150, max_x_cm=150, min_y_cm=0, max_y_cm=100,
                         min_z_cm=80, max_z_cm=180, standoff_cm=300)


def test_every_generated_stop_lies_inside_the_envelope():
    for option in generate_options(ENVELOPE):
        for x_cm, y_cm in option.stops:
            assert ENVELOPE.min_x_cm <= x_cm <= ENVELOPE.max_x_cm
            assert ENVELOPE.min_y_cm <= y_cm <= ENVELOPE.max_y_cm
        assert ENVELOPE.min_z_cm <= option.z_cm <= ENVELOPE.max_z_cm


def test_the_main_rail_options_never_rotate():
    """Spec 5.3: differential yaw between stops is the largest depth error
    term, and rotate-to-look is what introduces it."""
    rails = [o for o in generate_options(ENVELOPE) if o.option_id.startswith("RAIL_")]
    assert rails
    for option in rails:
        assert option.looks_deg == (0,)


def test_no_option_exceeds_the_mission_runner_waypoint_cap():
    for option in generate_options(ENVELOPE):
        assert len(option.stops) <= 8


def test_options_are_deterministic_and_uniquely_identified():
    first = generate_options(ENVELOPE)
    assert first == generate_options(ENVELOPE)
    assert len({o.option_id for o in first}) == len(first)


def test_each_option_states_which_classes_it_can_resolve():
    for option in generate_options(ENVELOPE):
        assert "computer" in option.resolvable_classes
        assert "pen" not in option.resolvable_classes


def test_a_close_look_option_exists_and_resolves_more_than_the_main_rail():
    options = {o.option_id: o for o in generate_options(ENVELOPE)}
    close = options["CLOSE_LOOK_CENTRE"]
    rail = options["RAIL_MID_HEIGHT"]
    assert close.range_cm < rail.range_cm
    assert set(rail.resolvable_classes) <= set(close.resolvable_classes)
```

- [ ] **Step 6: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_options.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 7: Implement the envelope and option generator**

```python
# src/sentinel/survey/envelope.py
"""The flyable box, as plain data.

Deliberately free of any ``sentinel.cube`` import so every module below it
is testable before WP5 lands. ``survey.cube_bridge`` (Task 11) is the one
place that converts a WP5 ``Site`` into this.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SweepEnvelope:
    min_x_cm: int
    max_x_cm: int
    min_y_cm: int
    max_y_cm: int
    min_z_cm: int
    max_z_cm: int
    standoff_cm: int      # distance from the rail to what it looks at

    def contains(self, x_cm: float, y_cm: float, z_cm: float) -> bool:
        return (self.min_x_cm <= x_cm <= self.max_x_cm
                and self.min_y_cm <= y_cm <= self.max_y_cm
                and self.min_z_cm <= z_cm <= self.max_z_cm)


@dataclass(frozen=True)
class SweepOption:
    option_id: str
    description: str
    stops: tuple[tuple[int, int], ...]
    z_cm: int
    looks_deg: tuple[int, ...]
    viewed_width_cm: int
    estimated_seconds: int
    resolvable_classes: tuple[str, ...]
    range_cm: int
```

```python
# src/sentinel/survey/options.py
"""Generate the legal sweeps. Deterministic, envelope-valid, and the only
source of a sweep the agent may ask for."""
from __future__ import annotations

import math

from .envelope import SweepEnvelope, SweepOption
from .observability import FOCAL_PX, IMAGE_WIDTH, resolvable_classes

MAX_WAYPOINTS = 8          # the WP5 mission runner's own cap
SECONDS_PER_STOP = 22      # move, settle, capture -- measured on 09-21, revise then


def _rail(envelope: SweepEnvelope, option_id: str, description: str,
          count: int, z_cm: int, y_cm: int, range_cm: int) -> SweepOption:
    span = envelope.max_x_cm - envelope.min_x_cm
    step = span / (count - 1) if count > 1 else 0
    stops = tuple((int(round(envelope.min_x_cm + step * index)), y_cm)
                  for index in range(count))
    half_angle = math.atan(IMAGE_WIDTH / 2.0 / FOCAL_PX)
    viewed = int(span + 2 * range_cm * math.tan(half_angle))
    return SweepOption(option_id, description, stops, z_cm, (0,), viewed,
                       count * SECONDS_PER_STOP, resolvable_classes(range_cm), range_cm)


def generate_options(envelope: SweepEnvelope) -> tuple[SweepOption, ...]:
    mid_z = (envelope.min_z_cm + envelope.max_z_cm) // 2
    near_y = envelope.min_y_cm
    far_y = envelope.max_y_cm
    near_range = envelope.standoff_cm + (far_y - near_y)
    far_range = envelope.standoff_cm
    centre_x = (envelope.min_x_cm + envelope.max_x_cm) // 2
    close_range = max(100, envelope.standoff_cm // 2)

    options = [
        _rail(envelope, "RAIL_MID_HEIGHT", "four stops across the middle of the cube",
              4, mid_z, near_y, near_range),
        _rail(envelope, "RAIL_HIGH_WIDE", "six stops at the top of the cube, widest view",
              6, envelope.max_z_cm, far_y, far_range),
        _rail(envelope, "RAIL_LOW_NEAR", "four stops low and close",
              4, envelope.min_z_cm, near_y, near_range),
        SweepOption("CLOSE_LOOK_CENTRE", "two close stops in the middle, for a second look",
                    ((centre_x - 40, far_y), (centre_x + 40, far_y)), mid_z, (0,),
                    int(2 * close_range * math.tan(math.atan(IMAGE_WIDTH / 2.0 / FOCAL_PX))),
                    2 * SECONDS_PER_STOP, resolvable_classes(close_range), close_range),
    ]
    for option in options:
        assert len(option.stops) <= MAX_WAYPOINTS, option.option_id
        for x_cm, y_cm in option.stops:
            assert envelope.contains(x_cm, y_cm, option.z_cm), option.option_id
    return tuple(options)
```

- [ ] **Step 8: Run the options tests**

Run: `rtk proxy uv run pytest tests/survey/test_options.py tests/survey/test_observability.py -q`
Expected: PASS, 12 tests.

- [ ] **Step 9: Commit**

```bash
git add src/sentinel/survey/envelope.py src/sentinel/survey/options.py src/sentinel/survey/observability.py tests/survey/test_options.py tests/survey/test_observability.py
git commit -m "feat(survey): envelope, sweep options and observability"
```

---

### Task 3: Detector port and its test doubles

**Files:**
- Create: `src/sentinel/survey/detector.py`
- Test: `tests/survey/test_detector.py`

**Interfaces — Consumes:** `sentinel.coordinator.ports.PerceptionPort` (read-only import).

**Interfaces — Produces:**
- `DetectorPort` protocol: `detect(image_path: str, classes: tuple[str, ...]) -> DetectionBatch`
- `DetectionBatch(detections: tuple[dict, ...], status: str, raw: dict | None)` where `status` is `OK` or `PROVIDER_ERROR`
- `ScriptedDetector(script: dict[str, list[dict]])` — keyed by image basename
- `ReplayDetector(responses_dir: Path)` — reads stored provider JSON, never calls out
- `normalise(raw_items, width, height) -> tuple[dict, ...]`

- [ ] **Step 1: Write the failing test**

```python
# tests/survey/test_detector.py
import json

from sentinel.survey.detector import ReplayDetector, ScriptedDetector, normalise


def test_normalise_converts_fractional_boxes_to_top_left_pixels():
    items = [{"label": "laptop", "x": 0.25, "y": 0.5, "width": 0.1, "height": 0.2,
              "confidence": "high", "description": "silver laptop"}]
    out = normalise(items, 960, 720)
    assert out[0]["bbox"] == {"x": 240, "y": 360, "width": 96, "height": 144}
    assert out[0]["bbox_coordinate_space"] == "PIXELS_TOP_LEFT"
    assert out[0]["class"] == "computer"
    assert out[0]["matched_prompt"] == "laptop"


def test_a_vlm_never_claims_a_detector_raw_score():
    """WP3's rule: a VLM band is supplemental and must not be compared to a
    detector threshold."""
    out = normalise([{"label": "laptop", "x": 0.1, "y": 0.1, "width": 0.1,
                      "height": 0.1, "confidence": "high"}], 960, 720)
    assert out[0]["score_kind"] == "VLM_CONFIDENCE_BAND"
    assert out[0]["raw_score"] is None


def test_an_item_with_no_location_survives_with_a_null_box_rather_than_a_guess():
    """Pipeline spec 4.1: a box is never manufactured from prose."""
    out = normalise([{"label": "laptop", "description": "a laptop somewhere"}], 960, 720)
    assert out[0]["bbox"] is None
    assert out[0]["class"] == "computer"


def test_an_unmappable_label_is_dropped_not_guessed():
    assert normalise([{"label": "sandwich", "x": 0.1, "y": 0.1,
                       "width": 0.1, "height": 0.1}], 960, 720) == ()


def test_a_box_outside_the_frame_is_clamped_to_the_frame():
    out = normalise([{"label": "laptop", "x": 0.95, "y": 0.1, "width": 0.2,
                      "height": 0.1}], 960, 720)
    assert out[0]["bbox"]["x"] + out[0]["bbox"]["width"] <= 960


def test_the_replay_detector_never_calls_out_and_returns_the_stored_response(tmp_path):
    (tmp_path / "f0.json").write_text(json.dumps(
        {"objects": [{"label": "laptop", "x": 0.5, "y": 0.5, "width": 0.1, "height": 0.1}]}),
        encoding="utf-8")
    batch = ReplayDetector(tmp_path).detect("frames/f0.png", ("computer",))
    assert batch.status == "OK"
    assert batch.detections[0]["class"] == "computer"
    assert batch.raw == {"objects": [{"label": "laptop", "x": 0.5, "y": 0.5,
                                      "width": 0.1, "height": 0.1}]}


def test_a_missing_replay_response_is_a_provider_error_not_an_empty_scene(tmp_path):
    batch = ReplayDetector(tmp_path).detect("frames/absent.png", ("computer",))
    assert batch.status == "PROVIDER_ERROR"
    assert batch.detections == ()


def test_the_scripted_detector_returns_exactly_what_it_was_given():
    detection = {"class": "computer", "matched_prompt": "laptop", "raw_score": None,
                 "score_kind": "VLM_CONFIDENCE_BAND",
                 "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                 "bbox_coordinate_space": "PIXELS_TOP_LEFT",
                 "description": "d", "provider_item_id": None}
    batch = ScriptedDetector({"f0.png": [detection]}).detect("frames/f0.png", ("computer",))
    assert batch.detections == (detection,)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_detector.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement the detector module**

```python
# src/sentinel/survey/detector.py
"""The perception boundary for the survey flow.

Implements the shape of ``sentinel.coordinator.ports.PerceptionPort``: one
image in, provider-neutral detections out. Nothing here opens the store,
and nothing here decides anything.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .observability import IMAGE_HEIGHT, IMAGE_WIDTH

#: Provider vocabulary -> our controlled classes. Explicit and versioned:
#: an unmapped label is dropped, never guessed (pipeline spec 4.1).
CLASS_MAP = {
    "laptop": "computer", "computer": "computer", "monitor": "computer",
    "notebook computer": "computer",
    "person": "person", "people": "person", "man": "person", "woman": "person",
    "cell phone": "phone", "phone": "phone", "mobile phone": "phone", "smartphone": "phone",
    "pen": "pen", "pencil": "pen",
}

_BANDS = {"high", "medium", "low"}


@dataclass(frozen=True)
class DetectionBatch:
    detections: tuple[dict, ...]
    status: str                    # "OK" | "PROVIDER_ERROR"
    raw: dict | None = None


@runtime_checkable
class DetectorPort(Protocol):
    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch: ...


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _box(item: dict, width: int, height: int) -> dict | None:
    if not all(key in item for key in ("x", "y", "width", "height")):
        return None
    x = _clamp(int(round(item["x"] * width)), 0, width - 1)
    y = _clamp(int(round(item["y"] * height)), 0, height - 1)
    w = _clamp(int(round(item["width"] * width)), 1, width - x)
    h = _clamp(int(round(item["height"] * height)), 1, height - y)
    return {"x": x, "y": y, "width": w, "height": h}


def normalise(items, width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> tuple[dict, ...]:
    out = []
    for item in items:
        label = str(item.get("label", "")).strip().lower()
        mapped = CLASS_MAP.get(label)
        if mapped is None:
            continue
        band = str(item.get("confidence", "")).strip().lower()
        out.append({
            "class": mapped,
            "matched_prompt": label,
            # A VLM band is never a detector score (WP3 section 2).
            "raw_score": None,
            "score_kind": "VLM_CONFIDENCE_BAND" if band in _BANDS else "NONE",
            "bbox": _box(item, width, height),
            "bbox_coordinate_space": "PIXELS_TOP_LEFT",
            "description": item.get("description"),
            "provider_item_id": item.get("id"),
            "band": band if band in _BANDS else "low",
        })
    return tuple(out)


class ScriptedDetector:
    """Returns exactly what the test gave it, keyed by image basename."""

    def __init__(self, script: dict[str, list[dict]]) -> None:
        self.script = script

    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch:
        key = Path(image_path).name
        if key not in self.script:
            return DetectionBatch((), "PROVIDER_ERROR")
        return DetectionBatch(tuple(self.script[key]), "OK")


class ReplayDetector:
    """Serves a previously recorded provider response. Never calls out, so
    every replay and every evaluation run is free and repeatable."""

    def __init__(self, responses_dir) -> None:
        self.responses_dir = Path(responses_dir)

    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch:
        path = self.responses_dir / (Path(image_path).stem + ".json")
        if not path.exists():
            return DetectionBatch((), "PROVIDER_ERROR")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return DetectionBatch(normalise(raw.get("objects", [])), "OK", raw)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `rtk proxy uv run pytest tests/survey/test_detector.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/survey/detector.py tests/survey/test_detector.py
git commit -m "feat(survey): detector port with scripted and replay doubles"
```

---

### Task 4: Geometry and the merge

**Files:**
- Create: `src/sentinel/survey/geometry.py`, `src/sentinel/survey/merge.py`
- Test: `tests/survey/test_geometry.py`, `tests/survey/test_merge.py`

**Interfaces — Consumes:** `survey.observability.FOCAL_PX`, `PRINCIPAL_X`.

**Interfaces — Produces:**
- `bearing_of(bbox: dict) -> float` — radians, positive to the right
- `ray_intersection(p1, d1, p2, d2) -> tuple[float, float] | None`
- `merge_sightings(sightings: list[dict]) -> list[dict]` producing `{"class", "x_cm", "y_cm", "distinct_stops", "band", "appearance", "sighting_ids"}`
- `MERGE_MIN_RANGE_CM = 50`, `MERGE_MAX_RANGE_CM = 1200`, `CONFIRM_MIN_STOPS = 3`

- [ ] **Step 1: Write the failing geometry test**

```python
# tests/survey/test_geometry.py
import math

import pytest

from sentinel.survey.geometry import bearing_of, ray_intersection
from sentinel.survey.observability import FOCAL_PX


def test_an_object_in_the_centre_of_the_frame_has_zero_bearing():
    assert bearing_of({"x": 460, "y": 300, "width": 40, "height": 40}) == pytest.approx(0.0)


def test_an_object_to_the_right_has_a_positive_bearing():
    bearing = bearing_of({"x": 680, "y": 300, "width": 40, "height": 40})
    assert bearing == pytest.approx(math.atan(220 / FOCAL_PX))
    assert bearing > 0


def test_two_rays_crossing_ahead_return_their_intersection():
    point = ray_intersection((0.0, 0.0), (0.0, 1.0), (100.0, 0.0), (-1.0, 1.0))
    assert point == pytest.approx((0.0, 100.0))


def test_parallel_rays_do_not_intersect():
    assert ray_intersection((0.0, 0.0), (0.0, 1.0), (100.0, 0.0), (0.0, 1.0)) is None


def test_an_intersection_behind_either_camera_is_rejected():
    assert ray_intersection((0.0, 0.0), (0.0, 1.0), (100.0, 0.0), (1.0, 1.0)) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_geometry.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement geometry**

```python
# src/sentinel/survey/geometry.py
"""Image-space to room-space arithmetic. Pure functions, no I/O."""
from __future__ import annotations

import math

from .observability import FOCAL_PX, PRINCIPAL_X


def bearing_of(bbox: dict) -> float:
    """Horizontal angle from the optical axis to the box's centre, in
    radians, positive to the right. Only the horizontal centre is used: the
    merge needs a bearing, not a tight box, which is the least demanding
    thing a grounding model can produce."""
    centre_u = bbox["x"] + bbox["width"] / 2.0
    return math.atan((centre_u - PRINCIPAL_X) / FOCAL_PX)


def ray_direction(heading_deg: float, bearing_rad: float) -> tuple[float, float]:
    """Room-frame unit direction. Heading 0 looks along +y; +x is right."""
    angle = math.radians(heading_deg) + bearing_rad
    return (math.sin(angle), math.cos(angle))


def ray_intersection(p1, d1, p2, d2) -> tuple[float, float] | None:
    """Where two rays cross, or None if they are parallel or cross behind
    either origin."""
    denominator = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denominator) < 1e-9:
        return None
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    t1 = (dx * d2[1] - dy * d2[0]) / denominator
    t2 = (dx * d1[1] - dy * d1[0]) / denominator
    if t1 <= 0 or t2 <= 0:
        return None
    return (p1[0] + t1 * d1[0], p1[1] + t1 * d1[1])
```

- [ ] **Step 4: Run geometry tests**

Run: `rtk proxy uv run pytest tests/survey/test_geometry.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Write the failing merge test**

```python
# tests/survey/test_merge.py
from sentinel.survey.merge import CONFIRM_MIN_STOPS, merge_sightings


def _sighting(sighting_id, stop, x_cm, class_name="computer", u=480, band="high",
              description=None):
    return {"sighting_id": sighting_id, "stop_index": stop, "x_cm": x_cm, "y_cm": 0.0,
            "heading_deg": 0.0, "look_deg": 0.0, "class": class_name, "band": band,
            "description": description,
            "bbox": {"x": u - 20, "y": 300, "width": 40, "height": 40}}


def _looking_at(stop, x_cm, target_x, target_y, **kwargs):
    """A sighting whose pixel column is whatever a real camera at x_cm would
    have produced for an object at (target_x, target_y)."""
    import math

    from sentinel.survey.observability import FOCAL_PX, PRINCIPAL_X
    bearing = math.atan2(target_x - x_cm, target_y)
    u = PRINCIPAL_X + FOCAL_PX * math.tan(bearing)
    return _sighting(f"s{stop}", stop, x_cm, u=u, **kwargs)


def test_one_object_seen_from_three_stops_is_counted_once_and_confirmed():
    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0) for i in range(3)]
    objects = merge_sightings(sightings)
    assert len(objects) == 1
    assert objects[0]["band"] == "CONFIRMED"
    assert objects[0]["distinct_stops"] == CONFIRM_MIN_STOPS
    assert objects[0]["x_cm"] == pytest.approx(100.0, abs=25)
    assert objects[0]["y_cm"] == pytest.approx(300.0, abs=60)


def test_two_well_separated_objects_stay_two():
    sightings = ([_looking_at(i, i * 75.0, -100.0, 300.0) for i in range(3)]
                 + [dict(_looking_at(i, i * 75.0, 200.0, 300.0), sighting_id=f"t{i}")
                    for i in range(3)])
    assert len(merge_sightings(sightings)) == 2


def test_an_object_seen_from_only_one_stop_is_uncertain_not_dropped():
    objects = merge_sightings([_looking_at(0, 0.0, 100.0, 300.0)])
    assert len(objects) == 1
    assert objects[0]["band"] == "UNCERTAIN"
    assert objects[0]["distinct_stops"] == 1


def test_different_classes_never_merge_however_close():
    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0) for i in range(3)]
    sightings += [dict(_looking_at(i, i * 75.0, 100.0, 300.0),
                       sighting_id=f"p{i}", **{"class": "phone"}) for i in range(3)]
    objects = merge_sightings(sightings)
    assert sorted(o["class"] for o in objects) == ["computer", "phone"]


def test_a_sighting_without_a_box_cannot_be_merged_and_stays_uncertain():
    blind = dict(_sighting("b0", 0, 0.0), bbox=None)
    objects = merge_sightings([blind])
    assert objects[0]["band"] == "UNCERTAIN"
    assert objects[0]["x_cm"] is None


def test_a_low_band_sighting_cannot_confirm_an_object():
    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0, band="low") for i in range(3)]
    assert merge_sightings(sightings)[0]["band"] == "UNCERTAIN"


def test_people_are_never_position_merged_because_they_move():
    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0, class_name="person")
                 for i in range(3)]
    objects = merge_sightings(sightings)
    assert all(o["class"] == "person" for o in objects)
    assert all(o["x_cm"] is None for o in objects)
```

Add `import pytest` at the top of the file.

- [ ] **Step 6: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_merge.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 7: Implement the merge**

```python
# src/sentinel/survey/merge.py
"""Sightings to distinct objects.

Code counts; the model never does. Vision-language models are documented to
count poorly (hallucination, poor individuation, double counting, and a
consistent undercount bias), so the provider is asked only *where* each
object is and the arithmetic happens here.
"""
from __future__ import annotations

import math

from .geometry import bearing_of, ray_direction, ray_intersection

MERGE_MIN_RANGE_CM = 50.0
MERGE_MAX_RANGE_CM = 1200.0
CONFIRM_MIN_STOPS = 3

#: Classes that move between stops, so their positions cannot be merged.
#: Counted instead as a single-view lower bound (design spec 4.3).
MOBILE_CLASSES = frozenset({"person"})


def _tolerance_cm(range_cm: float) -> float:
    """Grows with distance: a 15 cm position error on a 75 cm baseline is a
    20% depth error, so a fixed tolerance would be wrong at both ends."""
    return max(30.0, 0.25 * range_cm)


def _positioned(sighting: dict) -> bool:
    return (sighting.get("bbox") is not None
            and sighting["class"] not in MOBILE_CLASSES)


def _ray(sighting: dict):
    origin = (float(sighting["x_cm"]), float(sighting["y_cm"]))
    bearing = bearing_of(sighting["bbox"]) + math.radians(sighting.get("look_deg", 0.0))
    return origin, ray_direction(float(sighting["heading_deg"]), bearing)


def _unmerged(sighting: dict) -> dict:
    return {"class": sighting["class"], "x_cm": None, "y_cm": None,
            "distinct_stops": 1, "band": "UNCERTAIN",
            "appearance": sighting.get("description"),
            "sighting_ids": [sighting["sighting_id"]]}


def merge_sightings(sightings: list[dict]) -> list[dict]:
    objects: list[dict] = []
    for class_name in sorted({s["class"] for s in sightings}):
        rows = [s for s in sightings if s["class"] == class_name]
        mergeable = [s for s in rows if _positioned(s)]
        objects.extend(_unmerged(s) for s in rows if not _positioned(s))
        objects.extend(_cluster(mergeable))
    return objects


def _cluster(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    if len(rows) == 1:
        return [_unmerged(rows[0])]

    crossings = []
    for i, first in enumerate(rows):
        for second in rows[i + 1:]:
            if first["stop_index"] == second["stop_index"]:
                continue
            point = ray_intersection(*_ray(first), *_ray(second))
            if point is None:
                continue
            distance = math.hypot(point[0] - first["x_cm"], point[1] - first["y_cm"])
            if not (MERGE_MIN_RANGE_CM <= distance <= MERGE_MAX_RANGE_CM):
                continue
            crossings.append((point, distance, first, second))

    clusters: list[dict] = []
    for point, distance, first, second in sorted(crossings, key=lambda c: c[1]):
        tolerance = _tolerance_cm(distance)
        for cluster in clusters:
            if math.hypot(point[0] - cluster["x_cm"], point[1] - cluster["y_cm"]) <= tolerance:
                cluster["points"].append(point)
                cluster["x_cm"] = sum(p[0] for p in cluster["points"]) / len(cluster["points"])
                cluster["y_cm"] = sum(p[1] for p in cluster["points"]) / len(cluster["points"])
                cluster["rows"].update({first["sighting_id"]: first,
                                        second["sighting_id"]: second})
                break
        else:
            clusters.append({"x_cm": point[0], "y_cm": point[1], "points": [point],
                             "rows": {first["sighting_id"]: first,
                                      second["sighting_id"]: second}})

    claimed = {sighting_id for cluster in clusters for sighting_id in cluster["rows"]}
    out = [_object_from(cluster) for cluster in clusters]
    out.extend(_unmerged(row) for row in rows if row["sighting_id"] not in claimed)
    return out


def _object_from(cluster: dict) -> dict:
    rows = list(cluster["rows"].values())
    stops = {row["stop_index"] for row in rows}
    confident = all(row.get("band") != "low" for row in rows)
    appearance = next((row.get("description") for row in rows if row.get("description")), None)
    return {
        "class": rows[0]["class"],
        "x_cm": cluster["x_cm"], "y_cm": cluster["y_cm"],
        "distinct_stops": len(stops),
        "band": "CONFIRMED" if len(stops) >= CONFIRM_MIN_STOPS and confident else "UNCERTAIN",
        "appearance": appearance,
        "sighting_ids": sorted(cluster["rows"]),
    }
```

- [ ] **Step 8: Run the merge tests**

Run: `rtk proxy uv run pytest tests/survey/test_merge.py tests/survey/test_geometry.py -q`
Expected: PASS, 12 tests.

- [ ] **Step 9: Commit**

```bash
git add src/sentinel/survey/geometry.py src/sentinel/survey/merge.py tests/survey/test_geometry.py tests/survey/test_merge.py
git commit -m "feat(survey): triangulating merge from sightings to distinct objects"
```

---

### Task 5: Candidates and the survey policy seam

**Files:**
- Create: `src/sentinel/survey/context.py`, `src/sentinel/survey/candidates.py`
- Test: `tests/survey/test_candidates.py`

**Interfaces — Consumes:** `sentinel.coordinator.legal_tools.Candidate` and `LegalSet` (imported, never modified — both are fully generic), `survey.options.SweepOption`, `survey.store.SurveyStore`.

**Interfaces — Produces:**
- `PROPOSE_SWEEP = "PROPOSE_SWEEP"`, `REVIEW_RESULTS = "REVIEW_RESULTS"`, `ESCALATE_LABEL = "escalate"`
- `SurveyContext(store, run_id, legal, results)` frozen dataclass
- `SurveyPolicy` protocol: `decide(request: dict, context: SurveyContext) -> dict`
- `propose_candidates(options) -> LegalSet`
- `review_candidates(results, options) -> LegalSet`

Every `Candidate.arguments` is **complete** — the tool call is ready to submit as-is. This is what lets a model that cannot generate arguments (Jev) still drive the loop.

- [ ] **Step 1: Write the failing test**

```python
# tests/survey/test_candidates.py
from sentinel.survey.candidates import (ESCALATE_LABEL, PROPOSE_SWEEP, REVIEW_RESULTS,
                                        propose_candidates, review_candidates)
from sentinel.survey.envelope import SweepEnvelope
from sentinel.survey.options import generate_options

ENVELOPE = SweepEnvelope(-150, 150, 0, 100, 80, 180, 300)
OPTIONS = generate_options(ENVELOPE)


def test_every_propose_candidate_carries_a_complete_ready_to_submit_call():
    legal = propose_candidates(OPTIONS)
    assert legal.decision_point == PROPOSE_SWEEP
    for candidate in legal.candidates:
        if candidate.label == ESCALATE_LABEL:
            continue
        assert candidate.tool_name == "survey.propose_sweep"
        assert set(candidate.arguments) == {"option_id", "reason"}
        assert candidate.arguments["option_id"] == candidate.label
        assert candidate.description


def test_escalate_is_always_available_at_propose():
    assert ESCALATE_LABEL in propose_candidates(OPTIONS).labels


def test_review_offers_reporting_plus_one_follow_up_per_option():
    results = {"uncertain_objects": [], "counts": {}, "unanalysed": 0, "flights_used": 1}
    legal = review_candidates(results, OPTIONS)
    assert legal.decision_point == REVIEW_RESULTS
    assert "report" in legal.labels
    assert any(c.tool_name == "survey.propose_sweep" for c in legal.candidates)


def test_a_run_that_has_used_both_flights_can_only_report():
    results = {"uncertain_objects": [], "counts": {}, "unanalysed": 0, "flights_used": 2}
    legal = review_candidates(results, OPTIONS)
    assert legal.labels == ("report",)


def test_each_follow_up_names_the_uncertain_objects_it_would_view():
    results = {"counts": {}, "unanalysed": 0, "flights_used": 1,
               "uncertain_objects": [{"object_id": "O1", "class": "phone",
                                      "x_cm": 0.0, "y_cm": 300.0}]}
    legal = review_candidates(results, OPTIONS)
    follow_ups = [c for c in legal.candidates if c.tool_name == "survey.propose_sweep"]
    assert follow_ups
    assert any("O1" in c.arguments["addresses"] for c in follow_ups)


def test_candidate_labels_are_unique():
    labels = propose_candidates(OPTIONS).labels
    assert len(labels) == len(set(labels))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_candidates.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement the context and candidates**

```python
# src/sentinel/survey/context.py
"""The survey flow's own policy seam.

Structurally identical to ``coordinator.policy.ContextualPolicy``, but with
its own context type: ``PolicyContext.state`` is typed to the alley's
``DecisionState`` and cannot carry survey state. ``Candidate`` and
``LegalSet`` are reused unchanged because both are fully generic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sentinel.coordinator.legal_tools import LegalSet


@dataclass(frozen=True)
class SurveyContext:
    run_id: str
    legal: LegalSet
    results: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SurveyPolicy(Protocol):
    def decide(self, request: dict[str, Any], context: SurveyContext) -> dict[str, Any]: ...
```

```python
# src/sentinel/survey/candidates.py
"""Build the complete legal tool calls at each decision point.

Every candidate's ``arguments`` are filled from the store and the site, so a
proposer's whole job is to pick one. That is what lets Jev -- which can
choose among enumerated options but cannot generate a UUID or a sentence --
drive this loop at all.
"""
from __future__ import annotations

import math

from sentinel.coordinator.legal_tools import Candidate, LegalSet

PROPOSE_SWEEP = "PROPOSE_SWEEP"
REVIEW_RESULTS = "REVIEW_RESULTS"
ESCALATE_LABEL = "escalate"
MAX_FLIGHTS = 2


def _describe(option) -> str:
    return (f"{option.description}; {len(option.stops)} stops at {option.z_cm} cm, "
            f"views {option.viewed_width_cm} cm at {option.range_cm} cm range, "
            f"~{option.estimated_seconds}s, resolves "
            f"{', '.join(sorted(option.resolvable_classes)) or 'nothing'}")


def propose_candidates(options) -> LegalSet:
    candidates = [
        Candidate("survey.propose_sweep",
                  {"option_id": option.option_id, "reason": ""},
                  option.option_id, _describe(option))
        for option in options
    ]
    candidates.append(Candidate(
        "survey.escalate", {"reason": ""}, ESCALATE_LABEL,
        "the request does not name a coverage any option provides, or is self-contradictory"))
    return LegalSet(PROPOSE_SWEEP, tuple(candidates))


def _addresses(option, uncertain) -> list[str]:
    """Which uncertain objects this option would actually view: inside its
    horizontal extent, and at a range where the class resolves."""
    if not option.stops:
        return []
    left = min(x for x, _ in option.stops) - option.viewed_width_cm / 2
    right = max(x for x, _ in option.stops) + option.viewed_width_cm / 2
    out = []
    for item in uncertain:
        if item.get("x_cm") is None:
            continue
        if not (left <= item["x_cm"] <= right):
            continue
        if item["class"] not in option.resolvable_classes:
            continue
        out.append(item["object_id"])
    return out


def review_candidates(results: dict, options) -> LegalSet:
    report = Candidate("survey.submit_report", {}, "report",
                       "report the counts now, with caveats")
    if results.get("flights_used", 0) >= MAX_FLIGHTS:
        return LegalSet(REVIEW_RESULTS, (report,))

    uncertain = results.get("uncertain_objects", [])
    candidates = [report]
    for option in options:
        addressed = _addresses(option, uncertain)
        candidates.append(Candidate(
            "survey.propose_sweep",
            {"option_id": option.option_id, "reason": "", "addresses": addressed},
            f"follow_up:{option.option_id}",
            f"{_describe(option)}; would view {len(addressed)} of "
            f"{len(uncertain)} uncertain objects"))
    return LegalSet(REVIEW_RESULTS, tuple(candidates))
```

- [ ] **Step 4: Run the tests**

Run: `rtk proxy uv run pytest tests/survey/test_candidates.py -q`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/survey/context.py src/sentinel/survey/candidates.py tests/survey/test_candidates.py
git commit -m "feat(survey): pre-built candidate calls and the survey policy seam"
```

---

### Task 6: Three proposers behind one seam

**Files:**
- Create: `src/sentinel/survey/proposer.py`
- Test: `tests/survey/test_proposer.py`

**Interfaces — Consumes:** `survey.context.SurveyContext`, `survey.candidates.ESCALATE_LABEL`, `sentinel.coordinator.shadow.AuditSink`.

**Interfaces — Produces:**
- `RuleProposer()`, `JevProposer(judge, sink, act_at=0.60, min_margin=0.15)`, `LlmProposer(adapter, sink)` — each with `decide(request, context) -> dict`
- `justify(label: str, context: SurveyContext) -> str` — the derived approval text for mode `J`
- `chosen_label(result: dict) -> str | None`

**Design note carried from the spec (§7.3):** a proposal is not an act. Every result here still stops at operator approval, which is why mode `J` needs no promotion evidence — the identical argument P3b1 makes for its own `LlmProposer`.

- [ ] **Step 1: Write the failing test**

```python
# tests/survey/test_proposer.py
from sentinel.coordinator.legal_tools import Candidate, LegalSet
from sentinel.survey.candidates import ESCALATE_LABEL, PROPOSE_SWEEP
from sentinel.survey.context import SurveyContext
from sentinel.survey.proposer import JevProposer, LlmProposer, RuleProposer, chosen_label

LEGAL = LegalSet(PROPOSE_SWEEP, (
    Candidate("survey.propose_sweep", {"option_id": "RAIL_MID_HEIGHT", "reason": ""},
              "RAIL_MID_HEIGHT", "four stops across the middle"),
    Candidate("survey.propose_sweep", {"option_id": "RAIL_HIGH_WIDE", "reason": ""},
              "RAIL_HIGH_WIDE", "six stops at the top"),
    Candidate("survey.escalate", {"reason": ""}, ESCALATE_LABEL, "unclear"),
))
CONTEXT = SurveyContext("RUN1", LEGAL, {})
REQUEST = {"run_id": "RUN1", "request_text": "do a rail sweep and count the computers"}


class _Sink:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class _Judge:
    def __init__(self, value, confidence, probabilities=None):
        self.value, self.confidence = value, confidence
        self.probabilities = probabilities or {}
        self.calls = []

    def choice(self, **kwargs):
        self.calls.append(kwargs)
        return {"value": self.value, "confidence": self.confidence,
                "probabilities": self.probabilities}


def test_the_rule_proposer_takes_the_first_non_escalate_option():
    assert chosen_label(RuleProposer().decide(REQUEST, CONTEXT)) == "RAIL_MID_HEIGHT"


def test_jev_acting_above_the_threshold_and_margin_picks_its_option():
    judge = _Judge("RAIL_HIGH_WIDE", 0.82,
                   {"RAIL_HIGH_WIDE": 0.82, "RAIL_MID_HEIGHT": 0.10, ESCALATE_LABEL: 0.08})
    result = JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)
    assert chosen_label(result) == "RAIL_HIGH_WIDE"


def test_jev_below_the_confidence_floor_escalates():
    judge = _Judge("RAIL_HIGH_WIDE", 0.40,
                   {"RAIL_HIGH_WIDE": 0.40, "RAIL_MID_HEIGHT": 0.35, ESCALATE_LABEL: 0.25})
    assert chosen_label(JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_jev_with_too_thin_a_margin_escalates_even_when_confident():
    """A near-tie is exactly the case the operator should see."""
    judge = _Judge("RAIL_HIGH_WIDE", 0.70,
                   {"RAIL_HIGH_WIDE": 0.36, "RAIL_MID_HEIGHT": 0.34, ESCALATE_LABEL: 0.30})
    assert chosen_label(JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_jev_is_never_offered_escalate_as_a_candidate():
    """Escalation is derived from the gates, not handed to the model as a
    free abstention -- see the Jev handoff, section 2.2."""
    judge = _Judge("RAIL_MID_HEIGHT", 0.9, {"RAIL_MID_HEIGHT": 0.9, "RAIL_HIGH_WIDE": 0.1})
    JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)
    assert ESCALATE_LABEL not in judge.calls[0]["criteria"]


def test_jev_records_a_derived_justification_because_it_returns_no_rationale():
    judge = _Judge("RAIL_HIGH_WIDE", 0.82,
                   {"RAIL_HIGH_WIDE": 0.82, "RAIL_MID_HEIGHT": 0.10})
    sink = _Sink()
    result = JevProposer(judge, sink).decide(REQUEST, CONTEXT)
    reason = result["tool_calls"][0]["arguments"]["reason"]
    assert "six stops at the top" in reason
    assert any(r["record"] == "survey_route" for r in sink.records)


def test_an_unknown_label_from_any_advisor_escalates():
    judge = _Judge("RAIL_INVENTED", 0.99, {"RAIL_INVENTED": 0.99})
    assert chosen_label(JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_a_judge_that_raises_fails_closed_to_escalate():
    class _Boom:
        def choice(self, **kwargs):
            raise RuntimeError("provider down")

    assert chosen_label(JevProposer(_Boom(), _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_the_llm_proposer_accepts_a_legal_label_with_a_reason():
    class _Adapter:
        def propose(self, request, context):
            return {"status": "TOOL_CALLS", "tool_calls": [
                {"tool_name": "survey.propose_sweep",
                 "arguments": {"option_id": "RAIL_HIGH_WIDE", "reason": "you asked for wide"}}]}

    result = LlmProposer(_Adapter(), _Sink()).decide(REQUEST, CONTEXT)
    assert chosen_label(result) == "RAIL_HIGH_WIDE"
    assert result["tool_calls"][0]["arguments"]["reason"] == "you asked for wide"


def test_the_llm_proposer_escalates_on_a_blank_reason():
    class _Adapter:
        def propose(self, request, context):
            return {"status": "TOOL_CALLS", "tool_calls": [
                {"tool_name": "survey.propose_sweep",
                 "arguments": {"option_id": "RAIL_HIGH_WIDE", "reason": "  "}}]}

    assert chosen_label(LlmProposer(_Adapter(), _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_the_llm_proposer_escalates_when_the_model_tries_to_finish():
    class _Adapter:
        def propose(self, request, context):
            return {"status": "TOOL_CALLS",
                    "tool_calls": [{"tool_name": "finish", "arguments": {}}]}

    assert chosen_label(LlmProposer(_Adapter(), _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_proposer.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement the proposers**

```python
# src/sentinel/survey/proposer.py
"""Three ways to pick a candidate, behind one seam.

``F`` rules, ``E`` the LLM, ``J`` Jev. All three return a result whose tool
call is one of the *pre-built* candidates, so none of them ever generates an
argument. Every result still stops at operator approval: a proposal is not
an act, which is why mode ``J`` claims no promotion evidence.
"""
from __future__ import annotations

from typing import Any

from .candidates import ESCALATE_LABEL
from .context import SurveyContext

#: Confidence floor for Jev. 0.60 rather than 0.80: every published
#: integration uses a floor in the 0.55-0.70 band with a margin gate beside
#: it, and 0.80 was never validated for this task shape.
DEFAULT_ACT_AT = 0.60
#: Top option must beat the runner-up by this much, or it is a near-tie and
#: the operator should see it.
DEFAULT_MIN_MARGIN = 0.15


def _call(context: SurveyContext, label: str, reason: str) -> dict[str, Any]:
    candidate = context.legal.by_label(label)
    arguments = dict(candidate.arguments)
    if "reason" in arguments:
        arguments["reason"] = reason[:512]
    return {"status": "TOOL_CALLS",
            "tool_calls": [{"tool_name": candidate.tool_name, "arguments": arguments}]}


def _escalate(context: SurveyContext, reason: str) -> dict[str, Any]:
    return _call(context, ESCALATE_LABEL, reason)


def chosen_label(result: dict) -> str | None:
    calls = result.get("tool_calls") or []
    if not calls:
        return None
    arguments = calls[0].get("arguments", {})
    if calls[0]["tool_name"] == "survey.escalate":
        return ESCALATE_LABEL
    return arguments.get("option_id")


def justify(label: str, context: SurveyContext) -> str:
    """Jev returns no rationale at all, so the approval text is derived from
    the candidate's own metrics. Being derived, it cannot be wrong."""
    return context.legal.by_label(label).description


def _actionable(context: SurveyContext):
    return [c for c in context.legal.candidates if c.label != ESCALATE_LABEL]


class RuleProposer:
    """Deterministic: the first actionable candidate."""

    def decide(self, request: dict, context: SurveyContext) -> dict:
        options = _actionable(context)
        if not options:
            return _escalate(context, "no actionable candidate")
        return _call(context, options[0].label, f"rule default: {options[0].description}")


class JevProposer:
    """Jev picks among the pre-built calls; code gates on confidence and
    margin and writes the justification."""

    def __init__(self, judge, sink, act_at: float = DEFAULT_ACT_AT,
                 min_margin: float = DEFAULT_MIN_MARGIN) -> None:
        self.judge, self.sink = judge, sink
        self.act_at, self.min_margin = act_at, min_margin

    def decide(self, request: dict, context: SurveyContext) -> dict:
        options = _actionable(context)
        if not options:
            return _escalate(context, "no actionable candidate")
        criteria = {c.label: c.description for c in options}
        try:
            answer = self.judge.choice(
                instruction="Choose the sweep that best answers the operator's request.",
                state=request.get("request_text", ""), criteria=criteria)
        except Exception as exc:  # noqa: BLE001 -- the advisor boundary fails closed
            return self._record(context, ESCALATE_LABEL, f"judge_error:{type(exc).__name__}", None)

        label = answer.get("value")
        confidence = answer.get("confidence")
        probabilities = answer.get("probabilities") or {}
        if label not in criteria:
            return self._record(context, ESCALATE_LABEL, "unknown_label", answer)
        if confidence is None or confidence < self.act_at:
            return self._record(context, ESCALATE_LABEL, "below_act_at", answer)
        ranked = sorted(probabilities.values(), reverse=True)
        margin = ranked[0] - ranked[1] if len(ranked) > 1 else 1.0
        if margin < self.min_margin:
            return self._record(context, ESCALATE_LABEL, "thin_margin", answer)
        return self._record(context, label, "accepted", answer)

    def _record(self, context: SurveyContext, label: str, outcome: str, answer) -> dict:
        self.sink.write({"record": "survey_route", "mode": "J", "outcome": outcome,
                         "label": label, "run_id": context.run_id,
                         "decision_point": context.legal.decision_point,
                         "judge_answer": answer})
        if label == ESCALATE_LABEL:
            return _escalate(context, f"jev: {outcome}")
        return _call(context, label, justify(label, context))


class LlmProposer:
    """Mirrors the validation P3b1's own proposer performs: exactly one call,
    a legal tool, a legal label, and a non-blank reason. Anything else
    escalates."""

    def __init__(self, adapter, sink) -> None:
        self.adapter, self.sink = adapter, sink

    def decide(self, request: dict, context: SurveyContext) -> dict:
        try:
            document = self.adapter.propose(request, context)
        except Exception as exc:  # noqa: BLE001 -- the adapter boundary fails closed
            return self._record(context, ESCALATE_LABEL, f"adapter_error:{type(exc).__name__}")
        if not isinstance(document, dict) or document.get("status") != "TOOL_CALLS":
            return self._record(context, ESCALATE_LABEL, "malformed_result")
        calls = document.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            return self._record(context, ESCALATE_LABEL, "wrong_call_count")
        call = calls[0]
        if call.get("tool_name") != "survey.propose_sweep":
            return self._record(context, ESCALATE_LABEL, f"illegal_tool:{call.get('tool_name')}")
        arguments = call.get("arguments") or {}
        label = arguments.get("option_id")
        reason = arguments.get("reason")
        if label not in {c.label for c in _actionable(context)}:
            return self._record(context, ESCALATE_LABEL, "unknown_option")
        if not isinstance(reason, str) or not reason.strip():
            return self._record(context, ESCALATE_LABEL, "blank_reason")
        self.sink.write({"record": "survey_route", "mode": "E", "outcome": "accepted",
                         "label": label, "run_id": context.run_id})
        return _call(context, label, reason.strip())

    def _record(self, context: SurveyContext, label: str, outcome: str) -> dict:
        self.sink.write({"record": "survey_route", "mode": "E", "outcome": outcome,
                         "label": label, "run_id": context.run_id})
        return _escalate(context, f"llm: {outcome}")
```

- [ ] **Step 4: Run the tests**

Run: `rtk proxy uv run pytest tests/survey/test_proposer.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/survey/proposer.py tests/survey/test_proposer.py
git commit -m "feat(survey): rule, Jev and LLM proposers behind one seam"
```

---

### Task 7: The loop, the flight cap, and the report check

**Files:**
- Create: `src/sentinel/survey/report.py`, `src/sentinel/survey/flight.py`, `src/sentinel/survey/loop.py`
- Test: `tests/survey/test_report.py`, `tests/survey/test_loop.py`

**Interfaces — Consumes:** everything from Tasks 1–6.

**Interfaces — Produces:**
- `FlightPort` protocol: `fly(option: SweepOption) -> list[FlightFrame]`; `FlightFrame(stop_index, look_deg, x_cm, y_cm, z_cm, heading_deg, position_kind, image_path, artifact_id, sha256)`
- `ReplayFlight(frames_dir)` — serves frames from a folder, never flies
- `class_counts(store, run_id, options_range_cm) -> list[dict]`
- `check_report(body: str, class_counts: list[dict]) -> None` raising `ReportMismatch`
- `run_survey(*, store, run_id, request_text, options, policy, flight, detector, on_approval, writer) -> dict` returning `{"status", "flights", "counts", "report", "escalations"}`

- [ ] **Step 1: Write the failing report test**

```python
# tests/survey/test_report.py
import pytest

from sentinel.survey.report import ReportMismatch, check_report

COUNTS = [
    {"class": "computer", "confirmed": 6, "uncertain": 1, "method": "MERGED_MULTI_VIEW"},
    {"class": "person", "confirmed": 2, "uncertain": 0, "method": "SINGLE_VIEW_LOWER_BOUND"},
    {"class": "pen", "confirmed": 0, "uncertain": 0, "method": "NOT_OBSERVABLE"},
]


def test_a_report_matching_the_store_is_accepted():
    check_report("At least 6 computers, possibly 7. At least 2 people. "
                 "Pens are not observable at this range.", COUNTS)


def test_a_number_the_store_does_not_hold_is_rejected():
    with pytest.raises(ReportMismatch) as excinfo:
        check_report("I counted 9 computers.", COUNTS)
    assert "9" in str(excinfo.value)


def test_claiming_certainty_about_a_not_observable_class_is_rejected():
    with pytest.raises(ReportMismatch):
        check_report("6 computers, 2 people and 3 pens.", COUNTS)


def test_a_lower_bound_class_may_not_be_stated_as_exact():
    with pytest.raises(ReportMismatch):
        check_report("There are exactly 2 people.", COUNTS)


def test_an_empty_report_is_rejected():
    with pytest.raises(ReportMismatch):
        check_report("   ", COUNTS)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_report.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement the report check**

```python
# src/sentinel/survey/report.py
"""Code checks the report against the store, so the model can explain a
count but never invent one."""
from __future__ import annotations

import re

from .observability import resolvable_classes


class ReportMismatch(ValueError):
    """The wording claims something the stored counts do not support."""


_EXACTNESS = re.compile(r"\bexactly\b", re.IGNORECASE)


def class_counts(store, run_id: str, range_cm: float) -> list[dict]:
    resolvable = set(resolvable_classes(range_cm))
    stored = store.counts(run_id)
    out = []
    for name in ("computer", "person", "phone", "pen"):
        entry = stored.get(name, {"confirmed": 0, "uncertain": 0})
        if name not in resolvable:
            method = "NOT_OBSERVABLE"
        elif name == "person":
            method = "SINGLE_VIEW_LOWER_BOUND"
        else:
            method = "MERGED_MULTI_VIEW"
        out.append({"class": name, "confirmed": entry["confirmed"],
                    "uncertain": entry["uncertain"], "method": method})
    return out


def check_report(body: str, counts: list[dict]) -> None:
    if not body or not body.strip():
        raise ReportMismatch("the report is empty")

    permitted = {0}
    for entry in counts:
        if entry["method"] == "NOT_OBSERVABLE":
            continue
        permitted.add(entry["confirmed"])
        permitted.add(entry["confirmed"] + entry["uncertain"])

    for token in re.findall(r"\b\d+\b", body):
        if int(token) not in permitted:
            raise ReportMismatch(
                f"the report states {token}, which the store does not support "
                f"(permitted: {sorted(permitted)})")

    for entry in counts:
        if entry["method"] != "NOT_OBSERVABLE":
            continue
        pattern = re.compile(rf"\b\d+\s+{entry['class']}s?\b", re.IGNORECASE)
        if pattern.search(body):
            raise ReportMismatch(
                f"{entry['class']} is not observable at this range; "
                "the report may not give it a count")

    if _EXACTNESS.search(body):
        for entry in counts:
            if entry["method"] == "SINGLE_VIEW_LOWER_BOUND" and entry["class"] in body.lower():
                raise ReportMismatch(
                    f"{entry['class']} is a single-view lower bound and may not be "
                    "reported as exact")
```

- [ ] **Step 4: Run the report tests**

Run: `rtk proxy uv run pytest tests/survey/test_report.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Write the failing loop test**

```python
# tests/survey/test_loop.py
import math

import pytest

from sentinel.survey.candidates import ESCALATE_LABEL
from sentinel.survey.detector import ScriptedDetector
from sentinel.survey.envelope import SweepEnvelope
from sentinel.survey.flight import FlightFrame, ReplayFlight
from sentinel.survey.loop import run_survey
from sentinel.survey.observability import FOCAL_PX, PRINCIPAL_X
from sentinel.survey.options import generate_options
from sentinel.survey.proposer import RuleProposer
from sentinel.survey.store import SurveyStore

ENVELOPE = SweepEnvelope(-150, 150, 0, 100, 80, 180, 300)
OPTIONS = generate_options(ENVELOPE)


class _StaticFlight:
    """Four stops along a rail, all looking straight ahead."""

    def __init__(self, stops=4):
        self.stops = stops
        self.flights = 0

    def fly(self, option):
        self.flights += 1
        return [FlightFrame(stop_index=i, look_deg=0.0, x_cm=i * 75.0 - 112.5, y_cm=0.0,
                            z_cm=option.z_cm, heading_deg=0.0, position_kind="COMMANDED",
                            image_path=f"frames/f{i}.png", artifact_id=f"A{i}",
                            sha256="0" * 64)
                for i in range(self.stops)]


def _seeing(target_x, target_y, x_cm, class_name="computer", band="high"):
    bearing = math.atan2(target_x - x_cm, target_y)
    u = PRINCIPAL_X + FOCAL_PX * math.tan(bearing)
    return {"class": class_name, "matched_prompt": class_name, "raw_score": None,
            "score_kind": "VLM_CONFIDENCE_BAND",
            "bbox": {"x": u - 20, "y": 300, "width": 40, "height": 40},
            "bbox_coordinate_space": "PIXELS_TOP_LEFT", "description": "a laptop",
            "provider_item_id": None, "band": band}


def _one_computer_script():
    return {f"f{i}.png": [_seeing(0.0, 300.0, i * 75.0 - 112.5)] for i in range(4)}


def _writer(body_calls):
    def write(results):
        body_calls.append(results)
        confirmed = next(c["confirmed"] for c in results["class_counts"]
                         if c["class"] == "computer")
        return f"I found {confirmed} computers."
    return write


def test_a_clean_run_flies_once_counts_and_reports(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    flight = _StaticFlight()
    calls = []
    outcome = run_survey(store=store, run_id="RUN1", request_text="count the computers",
                         options=OPTIONS, policy=RuleProposer(), flight=flight,
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True, writer=_writer(calls))
    assert outcome["status"] == "REPORTED"
    assert flight.flights == 1
    assert outcome["counts"]["computer"]["confirmed"] == 1
    assert "1 computers" in outcome["report"]


def test_a_refused_proposal_ends_the_run_without_flying(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    flight = _StaticFlight()
    outcome = run_survey(store=store, run_id="RUN1", request_text="count the computers",
                         options=OPTIONS, policy=RuleProposer(), flight=flight,
                         detector=ScriptedDetector({}),
                         on_approval=lambda call: False, writer=_writer([]))
    assert outcome["status"] == "REFUSED"
    assert flight.flights == 0


def test_the_run_never_flies_a_third_time(tmp_path):
    """The cap is enforced by code, not by the policy's good manners."""
    class _AlwaysFollowUp:
        def decide(self, request, context):
            from sentinel.survey.proposer import _call
            actionable = [c for c in context.legal.candidates
                          if c.tool_name == "survey.propose_sweep"]
            return _call(context, actionable[0].label, "again")

    store = SurveyStore.open(tmp_path / "s.sqlite3")
    flight = _StaticFlight()
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=_AlwaysFollowUp(), flight=flight,
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True, writer=_writer([]))
    assert flight.flights == 2
    assert outcome["status"] == "REPORTED"


def test_an_escalation_at_propose_ends_the_run_for_the_operator(tmp_path):
    class _Escalating:
        def decide(self, request, context):
            from sentinel.survey.proposer import _escalate
            return _escalate(context, "the request names no coverage I have")

    store = SurveyStore.open(tmp_path / "s.sqlite3")
    flight = _StaticFlight()
    outcome = run_survey(store=store, run_id="RUN1", request_text="have a look round",
                         options=OPTIONS, policy=_Escalating(), flight=flight,
                         detector=ScriptedDetector({}),
                         on_approval=lambda call: True, writer=_writer([]))
    assert outcome["status"] == "ESCALATED"
    assert flight.flights == 0
    assert outcome["escalations"][0]["label"] == ESCALATE_LABEL


def test_a_provider_error_is_reported_not_silently_dropped(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    script = _one_computer_script()
    del script["f2.png"]
    calls = []
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(script),
                         on_approval=lambda call: True, writer=_writer(calls))
    assert calls[0]["unanalysed"] == 1
    assert outcome["status"] == "REPORTED"


def test_a_writer_that_invents_a_number_is_refused(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True,
                         writer=lambda results: "I found 47 computers.")
    assert outcome["status"] == "REPORT_REJECTED"
    assert outcome["report"] is None
```

- [ ] **Step 6: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_loop.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 7: Implement the flight port and the loop**

```python
# src/sentinel/survey/flight.py
"""The flight boundary: one interface, so the loop never knows whether a
drone moved."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FlightFrame:
    stop_index: int
    look_deg: float
    x_cm: float
    y_cm: float
    z_cm: float
    heading_deg: float
    position_kind: str
    image_path: str
    artifact_id: str
    sha256: str


@runtime_checkable
class FlightPort(Protocol):
    def fly(self, option) -> list[FlightFrame]: ...


class ReplayFlight:
    """Serves the frames a real sweep already produced. ``frames.json`` is a
    list of FlightFrame field dicts."""

    def __init__(self, frames_dir) -> None:
        self.frames_dir = Path(frames_dir)

    def fly(self, option) -> list[FlightFrame]:
        manifest = json.loads((self.frames_dir / "frames.json").read_text(encoding="utf-8"))
        return [FlightFrame(**row) for row in manifest
                if row.pop("option_id", option.option_id) == option.option_id]
```

```python
# src/sentinel/survey/loop.py
"""The survey run.

Eight steps, two decision points, at most two flights. The policy chooses
among pre-built candidate calls; this module submits nothing the operator
has not approved and writes no number the store does not hold.
"""
from __future__ import annotations

from typing import Any

from .candidates import (ESCALATE_LABEL, MAX_FLIGHTS, propose_candidates,
                         review_candidates)
from .context import SurveyContext
from .merge import merge_sightings
from .proposer import chosen_label
from .report import ReportMismatch, check_report, class_counts


def _results(store, run_id: str, options) -> dict[str, Any]:
    objects = [dict(row) for row in store.connection.execute(
        "SELECT * FROM objects WHERE run_id = ?", (run_id,))]
    range_cm = min(option.range_cm for option in options)
    return {
        "counts": store.counts(run_id),
        "class_counts": class_counts(store, run_id, range_cm),
        "uncertain_objects": [o for o in objects if o["band"] == "UNCERTAIN"],
        "unanalysed": store.unanalysed_count(run_id),
        "flights_used": store.flights_used(run_id),
    }


def _fly_and_ingest(store, run_id, option, flight, detector) -> None:
    sweep_id = store.add_sweep(run_id, option.option_id, len(option.stops))
    for frame in flight.fly(option):
        frame_id = store.add_frame(
            sweep_id, stop_index=frame.stop_index, look_deg=frame.look_deg,
            x_cm=frame.x_cm, y_cm=frame.y_cm, z_cm=frame.z_cm,
            heading_deg=frame.heading_deg, position_kind=frame.position_kind,
            artifact_id=frame.artifact_id, sha256=frame.sha256)
        batch = detector.detect(frame.image_path, option.resolvable_classes)
        store.set_frame_analysis(frame_id, "OK" if batch.status == "OK" else batch.status)
        for detection in batch.detections:
            store.add_sighting(frame_id, detection, batch.raw)
    rows = store.sightings(run_id)
    for row in rows:
        import json as _json
        row["bbox"] = _json.loads(row["bbox_json"]) if row["bbox_json"] else None
        row["band"] = "high"
    store.set_objects(run_id, merge_sightings(rows))


def run_survey(*, store, run_id: str, request_text: str, options, policy,
               flight, detector, on_approval, writer,
               max_flights: int = MAX_FLIGHTS) -> dict[str, Any]:
    store.create_run(run_id, request_text)
    request = {"run_id": run_id, "request_text": request_text}
    escalations: list[dict] = []

    legal = propose_candidates(options)
    result = policy.decide(request, SurveyContext(run_id, legal, {}))
    label = chosen_label(result)
    if label == ESCALATE_LABEL or label is None:
        escalations.append({"point": legal.decision_point, "label": ESCALATE_LABEL,
                            "result": result})
        return {"status": "ESCALATED", "flights": 0, "counts": {}, "report": None,
                "escalations": escalations}

    while True:
        call = result["tool_calls"][0]
        if not on_approval(call):
            return {"status": "REFUSED", "flights": store.flights_used(run_id),
                    "counts": store.counts(run_id), "report": None,
                    "escalations": escalations}

        option = next(o for o in options if o.option_id == call["arguments"]["option_id"])
        _fly_and_ingest(store, run_id, option, flight, detector)

        results = _results(store, run_id, options)
        if results["flights_used"] >= max_flights:
            break
        review = review_candidates(results, options)
        result = policy.decide(request, SurveyContext(run_id, review, results))
        label = chosen_label(result)
        if label is None or result["tool_calls"][0]["tool_name"] == "survey.submit_report":
            break
        if label == ESCALATE_LABEL:
            escalations.append({"point": review.decision_point, "label": ESCALATE_LABEL,
                                "result": result})
            break

    results = _results(store, run_id, options)
    body = writer(results)
    try:
        check_report(body, results["class_counts"])
    except ReportMismatch as mismatch:
        return {"status": "REPORT_REJECTED", "flights": results["flights_used"],
                "counts": results["counts"], "report": None,
                "escalations": escalations + [{"point": "REPORT", "error": str(mismatch)}]}
    store.add_report(run_id, body, results["class_counts"])
    return {"status": "REPORTED", "flights": results["flights_used"],
            "counts": results["counts"], "report": body, "escalations": escalations}
```

- [ ] **Step 8: Run the loop tests**

Run: `rtk proxy uv run pytest tests/survey/test_loop.py tests/survey/test_report.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 9: Commit**

```bash
git add src/sentinel/survey/report.py src/sentinel/survey/flight.py src/sentinel/survey/loop.py tests/survey/test_report.py tests/survey/test_loop.py
git commit -m "feat(survey): run loop, two-flight cap and the report check"
```

---

### Task 8: Filming consent, enforced

**Files:**
- Create: `src/sentinel/survey/consent.py`
- Modify: `src/sentinel/survey/loop.py` (add the `consent` keyword to `run_survey` and the `blur` path in `_fly_and_ingest`)
- Test: `tests/survey/test_consent.py`

**Interfaces — Consumes:** `survey.store.SurveyStore`.

**Interfaces — Produces:**
- `ConsentMissing(RuntimeError)`
- `record_consent(store, location, consent_date, recorded_by)` — **never called by Claude on the operator's behalf**
- `has_consent(store, location, consent_date) -> bool`
- `consent_policy(store, location, consent_date, retain_person_frames) -> str` returning `RETAIN` or `BLUR`, or raising `ConsentMissing`
- `blur_person_regions(detections) -> tuple[dict, ...]` — strips person boxes' pixel data reference while keeping class and band

- [ ] **Step 1: Write the failing test**

```python
# tests/survey/test_consent.py
import pytest

from sentinel.survey.consent import (ConsentMissing, consent_policy, has_consent,
                                     record_consent)
from sentinel.survey.store import SurveyStore


def _store(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count")
    return store


def test_without_recorded_consent_the_default_is_to_blur_not_to_refuse(tmp_path):
    assert consent_policy(_store(tmp_path), "garden", "2026-09-21",
                          retain_person_frames=False) == "BLUR"


def test_without_recorded_consent_asking_to_retain_refuses_to_start(tmp_path):
    with pytest.raises(ConsentMissing):
        consent_policy(_store(tmp_path), "garden", "2026-09-21", retain_person_frames=True)


def test_recorded_consent_permits_retaining(tmp_path):
    store = _store(tmp_path)
    record_consent(store, "garden", "2026-09-21", recorded_by="operator")
    assert consent_policy(store, "garden", "2026-09-21", retain_person_frames=True) == "RETAIN"


def test_consent_does_not_carry_to_another_location(tmp_path):
    store = _store(tmp_path)
    record_consent(store, "garden", "2026-09-21", recorded_by="operator")
    assert not has_consent(store, "lecture_room", "2026-09-21")
    with pytest.raises(ConsentMissing):
        consent_policy(store, "lecture_room", "2026-09-21", retain_person_frames=True)


def test_consent_does_not_carry_to_another_date(tmp_path):
    store = _store(tmp_path)
    record_consent(store, "garden", "2026-09-21", recorded_by="operator")
    with pytest.raises(ConsentMissing):
        consent_policy(store, "garden", "2026-09-25", retain_person_frames=True)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_consent.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement consent**

```python
# src/sentinel/survey/consent.py
"""Filming consent, enforced rather than documented.

Frames containing people are written to disk and sent to a third-party
provider, so consent to be filmed is a gate. The operator records it per
location and date; Claude never records it on the operator's behalf and
never infers it from another location or another day.

Counts and person boxes survive blurring, so the gate costs evidence
quality and never the count.
"""
from __future__ import annotations


class ConsentMissing(RuntimeError):
    """A run asked to retain frames of people without recorded consent."""


def record_consent(store, location: str, consent_date: str, recorded_by: str) -> None:
    with store.connection:
        store.connection.execute(
            "INSERT OR REPLACE INTO filming_consent(location, consent_date, recorded_by) "
            "VALUES (?, ?, ?)", (location, consent_date, recorded_by))


def has_consent(store, location: str, consent_date: str) -> bool:
    row = store.connection.execute(
        "SELECT 1 FROM filming_consent WHERE location = ? AND consent_date = ?",
        (location, consent_date)).fetchone()
    return row is not None


def consent_policy(store, location: str, consent_date: str,
                   retain_person_frames: bool) -> str:
    if has_consent(store, location, consent_date):
        return "RETAIN"
    if retain_person_frames:
        raise ConsentMissing(
            f"no filming consent recorded for {location} on {consent_date}; "
            "either record it or run without --retain-person-frames")
    return "BLUR"


def blur_person_regions(detections) -> tuple[dict, ...]:
    """Keep the class, band and box geometry -- which is all the count needs
    -- and drop the description, which can carry identifying detail."""
    out = []
    for detection in detections:
        if detection["class"] == "person":
            out.append({**detection, "description": None, "provider_item_id": None})
        else:
            out.append(detection)
    return tuple(out)
```

- [ ] **Step 4: Wire it into the loop**

In `loop.py`, add `consent: str = "BLUR"` to `run_survey`'s keyword arguments, thread it into `_fly_and_ingest`, and inside the detection loop apply it:

```python
from .consent import blur_person_regions
# ...
        detections = batch.detections
        if consent == "BLUR":
            detections = blur_person_regions(detections)
        for detection in detections:
            store.add_sighting(frame_id, detection, batch.raw if consent == "RETAIN" else None)
```

Also set `person_regions_blurred=consent == "BLUR"` on `store.add_frame`.

- [ ] **Step 5: Run consent and loop tests together**

Run: `rtk proxy uv run pytest tests/survey/test_consent.py tests/survey/test_loop.py -q`
Expected: PASS, 11 tests. The loop tests must still pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/survey/consent.py src/sentinel/survey/loop.py tests/survey/test_consent.py
git commit -m "feat(survey): filming consent gate enforced in code"
```

---

### Task 9: The episode harness and three-mode scoring

**Files:**
- Create: `src/sentinel/survey/eval.py`
- Create: `fixtures/survey/scenes/garden-one-computer.json`
- Test: `tests/survey/test_eval.py`

**Interfaces — Consumes:** `survey.loop.run_survey`, `survey.merge`.

**Interfaces — Produces:**
- `Scene(scene_id, objects, frames_dir, responses_dir)` loaded by `load_scene(path)`
- `score_episode(scene, outcome, store, run_id) -> dict` with keys `count_error`, `localisation`, `decision_quality`, `flights`, `illegal_calls`, `report_consistent`
- `compare_modes(scene, policies: dict[str, Any], **kwargs) -> dict[str, dict]`

**Scoring rules, fixed here so no task invents its own:**
- `count_error[class]` = reported confirmed − true count. Negative means undercount.
- `localisation` = fraction of true objects matched to a reported object of the same class within `max(50, 0.3 × true range)` cm.
- `decision_quality` = 1.0 if every uncertain object at review is addressed by the follow-up chosen, 0.0 if a follow-up was available that addressed more, `None` if nothing was uncertain.
- `illegal_calls` counts tool calls whose name is not in the candidate set. **Must be 0.**
- `report_consistent` is `True` unless the run ended `REPORT_REJECTED`. A `False` here is a bug in `report.py`, not a model failure.

- [ ] **Step 1: Write the failing test**

```python
# tests/survey/test_eval.py
import json
import math

from sentinel.survey.detector import ScriptedDetector
from sentinel.survey.envelope import SweepEnvelope
from sentinel.survey.eval import compare_modes, load_scene, score_episode
from sentinel.survey.observability import FOCAL_PX, PRINCIPAL_X
from sentinel.survey.options import generate_options
from sentinel.survey.proposer import RuleProposer
from sentinel.survey.store import SurveyStore
from tests.survey.test_loop import _StaticFlight, _writer

ENVELOPE = SweepEnvelope(-150, 150, 0, 100, 80, 180, 300)
OPTIONS = generate_options(ENVELOPE)


def _scene_file(tmp_path):
    path = tmp_path / "scene.json"
    path.write_text(json.dumps({
        "scene_id": "one-computer",
        "objects": [{"class": "computer", "x_cm": 0.0, "y_cm": 300.0}],
        "frames_dir": "frames", "responses_dir": "responses"}), encoding="utf-8")
    return path


def _script():
    def seeing(x_cm):
        bearing = math.atan2(0.0 - x_cm, 300.0)
        u = PRINCIPAL_X + FOCAL_PX * math.tan(bearing)
        return {"class": "computer", "matched_prompt": "laptop", "raw_score": None,
                "score_kind": "VLM_CONFIDENCE_BAND",
                "bbox": {"x": u - 20, "y": 300, "width": 40, "height": 40},
                "bbox_coordinate_space": "PIXELS_TOP_LEFT",
                "description": "a laptop", "provider_item_id": None, "band": "high"}
    return {f"f{i}.png": [seeing(i * 75.0 - 112.5)] for i in range(4)}


def test_a_perfect_run_scores_zero_count_error_and_full_localisation(tmp_path):
    scene = load_scene(_scene_file(tmp_path))
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    from sentinel.survey.loop import run_survey
    outcome = run_survey(store=store, run_id="RUN1", request_text="count the computers",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_script()),
                         on_approval=lambda call: True, writer=_writer([]))
    score = score_episode(scene, outcome, store, "RUN1")
    assert score["count_error"]["computer"] == 0
    assert score["localisation"] == 1.0
    assert score["illegal_calls"] == 0
    assert score["report_consistent"] is True


def test_a_missed_object_scores_as_an_undercount(tmp_path):
    path = tmp_path / "scene.json"
    path.write_text(json.dumps({
        "scene_id": "two-computers",
        "objects": [{"class": "computer", "x_cm": 0.0, "y_cm": 300.0},
                    {"class": "computer", "x_cm": 900.0, "y_cm": 300.0}],
        "frames_dir": "frames", "responses_dir": "responses"}), encoding="utf-8")
    scene = load_scene(path)
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    from sentinel.survey.loop import run_survey
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_script()),
                         on_approval=lambda call: True, writer=_writer([]))
    score = score_episode(scene, outcome, store, "RUN1")
    assert score["count_error"]["computer"] == -1
    assert score["localisation"] == 0.5


def test_all_three_modes_are_scored_over_the_same_scene(tmp_path):
    scene = load_scene(_scene_file(tmp_path))

    class _Judge:
        def choice(self, **kwargs):
            label = sorted(kwargs["criteria"])[0]
            return {"value": label, "confidence": 0.9,
                    "probabilities": {label: 0.9, **{k: 0.02 for k in kwargs["criteria"]
                                                     if k != label}}}

    class _Adapter:
        def propose(self, request, context):
            actionable = [c for c in context.legal.candidates
                          if c.tool_name == "survey.propose_sweep"]
            return {"status": "TOOL_CALLS", "tool_calls": [
                {"tool_name": "survey.propose_sweep",
                 "arguments": {"option_id": actionable[0].arguments["option_id"],
                               "reason": "the widest coverage of the request"}}]}

    from sentinel.survey.proposer import JevProposer, LlmProposer

    class _Sink:
        def write(self, record):
            pass

    scores = compare_modes(
        scene,
        {"F": RuleProposer(), "J": JevProposer(_Judge(), _Sink()),
         "E": LlmProposer(_Adapter(), _Sink())},
        options=OPTIONS, flight_factory=_StaticFlight,
        detector=ScriptedDetector(_script()), tmp_path=tmp_path)
    assert set(scores) == {"F", "E", "J"}
    for mode in ("F", "E", "J"):
        assert scores[mode]["illegal_calls"] == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_eval.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement the harness**

```python
# src/sentinel/survey/eval.py
"""Episode-level scoring.

Each stage is scored separately so a wrong count can be attributed: an
aggregate would hide whether the provider, the merge or the planning was at
fault. Every arm replays stored frames, so a full three-mode comparison
costs nothing and can be re-run after any tolerance change.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .loop import run_survey


@dataclass(frozen=True)
class Scene:
    scene_id: str
    objects: tuple[dict, ...]
    frames_dir: str
    responses_dir: str


def load_scene(path) -> Scene:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return Scene(doc["scene_id"], tuple(doc["objects"]),
                 doc.get("frames_dir", "frames"), doc.get("responses_dir", "responses"))


def _match_radius_cm(true_object: dict) -> float:
    return max(50.0, 0.3 * math.hypot(true_object["x_cm"], true_object["y_cm"]))


def score_episode(scene: Scene, outcome: dict, store, run_id: str) -> dict[str, Any]:
    reported = [dict(row) for row in store.connection.execute(
        "SELECT * FROM objects WHERE run_id = ?", (run_id,))]

    count_error: dict[str, int] = {}
    for class_name in sorted({o["class"] for o in scene.objects}
                             | {o["class"] for o in reported}):
        truth = sum(1 for o in scene.objects if o["class"] == class_name)
        said = sum(1 for o in reported
                   if o["class"] == class_name and o["band"] == "CONFIRMED")
        count_error[class_name] = said - truth

    matched, available = 0, list(reported)
    for true_object in scene.objects:
        radius = _match_radius_cm(true_object)
        for candidate in available:
            if candidate["class"] != true_object["class"] or candidate["x_cm"] is None:
                continue
            if math.hypot(candidate["x_cm"] - true_object["x_cm"],
                          candidate["y_cm"] - true_object["y_cm"]) <= radius:
                matched += 1
                available.remove(candidate)
                break

    return {
        "scene_id": scene.scene_id,
        "count_error": count_error,
        "localisation": matched / len(scene.objects) if scene.objects else None,
        "decision_quality": outcome.get("decision_quality"),
        "flights": outcome["flights"],
        "illegal_calls": sum(1 for e in outcome.get("escalations", [])
                             if str(e.get("result", {})).count("illegal_tool")),
        "report_consistent": outcome["status"] != "REPORT_REJECTED",
        "status": outcome["status"],
    }


def compare_modes(scene: Scene, policies: dict[str, Any], *, options, flight_factory,
                  detector, tmp_path, request_text: str = "count the computers") -> dict:
    from .store import SurveyStore

    def writer(results):
        confirmed = next((c["confirmed"] for c in results["class_counts"]
                          if c["class"] == "computer"), 0)
        return f"I found {confirmed} computers."

    scores = {}
    for mode, policy in policies.items():
        store = SurveyStore.open(Path(tmp_path) / f"{mode}.sqlite3")
        outcome = run_survey(store=store, run_id=f"RUN-{mode}", request_text=request_text,
                             options=options, policy=policy, flight=flight_factory(),
                             detector=detector, on_approval=lambda call: True,
                             writer=writer)
        scores[mode] = score_episode(scene, outcome, store, f"RUN-{mode}")
    return scores
```

Create `fixtures/survey/scenes/garden-one-computer.json`:

```json
{
  "scene_id": "garden-one-computer",
  "objects": [{"class": "computer", "x_cm": 0.0, "y_cm": 300.0}],
  "frames_dir": "fixtures/survey/frames/garden-one-computer",
  "responses_dir": "fixtures/survey/responses/garden-one-computer"
}
```

- [ ] **Step 4: Run the eval tests**

Run: `rtk proxy uv run pytest tests/survey/test_eval.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/survey/eval.py fixtures/survey/scenes tests/survey/test_eval.py
git commit -m "feat(survey): episode harness scoring F, E and J over one scene"
```

---

### Task 10: The CLI and a full replay run

**Files:**
- Create: `scripts/survey_run.py`
- Test: `tests/survey/test_survey_cli.py`

**Interfaces — Consumes:** everything above.

**Interfaces — Produces:** `main(argv) -> int` and `build_policy(mode, *, judge=None, adapter=None, sink) -> SurveyPolicy`.

CLI flags: `--mode {F,E,J}` (default `F`), `--scene PATH`, `--frames DIR`, `--responses DIR`, `--db PATH`, `--request TEXT`, `--location TEXT`, `--date YYYY-MM-DD`, `--retain-person-frames`, `--auto-approve` (tests and replay only; a real flight always prompts).

- [ ] **Step 1: Write the failing CLI test**

```python
# tests/survey/test_survey_cli.py
import json

import pytest

from scripts.survey_run import build_policy, main
from sentinel.survey.consent import ConsentMissing
from sentinel.survey.proposer import JevProposer, LlmProposer, RuleProposer


class _Sink:
    def write(self, record):
        pass


def test_each_mode_builds_its_own_proposer():
    assert isinstance(build_policy("F", sink=_Sink()), RuleProposer)
    assert isinstance(build_policy("J", judge=object(), sink=_Sink()), JevProposer)
    assert isinstance(build_policy("E", adapter=object(), sink=_Sink()), LlmProposer)


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        build_policy("X", sink=_Sink())


def test_retaining_person_frames_without_consent_refuses_to_start(tmp_path, capsys):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frames.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ConsentMissing):
        main(["--mode", "F", "--frames", str(frames), "--responses", str(frames),
              "--db", str(tmp_path / "s.sqlite3"), "--request", "count",
              "--location", "garden", "--date", "2026-09-21",
              "--retain-person-frames", "--auto-approve"])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_survey_cli.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'scripts.survey_run'`.

- [ ] **Step 3: Implement the CLI**

Follow `scripts/rehearsal_run.py`'s structure. `build_policy` raises `ValueError` on an unknown mode. `main` resolves the consent policy **before** constructing the flight, so a refusal happens before anything moves. Print a JSON summary of the outcome to stdout.

- [ ] **Step 4: Run the CLI tests**

Run: `rtk proxy uv run pytest tests/survey/test_survey_cli.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the whole survey suite plus the full repo suite**

Run: `rtk proxy uv run pytest tests/survey -q`
Expected: PASS, ~70 tests.

Run: `rtk proxy uv run pytest -q`
Expected: 662 previously-passing tests still pass, 3 skipped, 0 failed. **If any pre-existing test fails, stop and surface it — do not edit it.**

- [ ] **Step 6: Commit**

```bash
git add scripts/survey_run.py tests/survey/test_survey_cli.py
git commit -m "feat(survey): CLI with F/E/J modes and consent enforcement"
```

---

### Task 11: Cube bridge and the real flight — **BLOCKED on the WP5 merge**

**Status:** Do not start until `sentinel.cube` is on `master` (operator gate). Tasks 1–10 and 12 do not depend on it.

**Files:**
- Create: `src/sentinel/survey/cube_bridge.py`
- Test: `tests/survey/test_cube_bridge.py`

**Interfaces — Consumes:** `sentinel.cube.site.Site`, `sentinel.cube.mission.Mission`, `Waypoint`, `CubeMissionRunner`.

**Interfaces — Produces:**
- `envelope_from_site(site: Site, standoff_cm: int) -> SweepEnvelope`
- `mission_from_option(option: SweepOption, start_x_cm: int, start_y_cm: int) -> Mission`
- `CubeFlight(runner, capture_dir)` implementing `FlightPort`

**This is the only module in the package that imports `sentinel.cube`.** Everything else stays testable without WP5.

- [ ] **Step 1: Write the failing test**

```python
# tests/survey/test_cube_bridge.py
from sentinel.cube.site import Site, Stand
from sentinel.survey.cube_bridge import envelope_from_site, mission_from_option
from sentinel.survey.options import generate_options

SITE = Site(line_y_m=0.0, stands=(Stand(0.0, 0, (0.9, 1.45), 0.16),),
            cube_min_x_m=-1.5, cube_max_x_m=1.5, cube_min_y_m=0.0, cube_max_y_m=1.0,
            cube_max_z_m=1.8, inset_m=0.1, min_z_cm=80, max_z_cm=180)


def test_the_envelope_is_inset_from_the_cube_bounds():
    envelope = envelope_from_site(SITE, standoff_cm=300)
    assert envelope.min_x_cm == -140
    assert envelope.max_x_cm == 140
    assert envelope.min_z_cm == 80 and envelope.max_z_cm == 180


def test_every_generated_mission_stays_within_the_waypoint_cap():
    envelope = envelope_from_site(SITE, standoff_cm=300)
    for option in generate_options(envelope):
        mission = mission_from_option(option, start_x_cm=0, start_y_cm=0)
        assert len(mission.waypoints) <= 8


def test_a_rail_mission_never_asks_the_runner_to_rotate():
    envelope = envelope_from_site(SITE, standoff_cm=300)
    rail = next(o for o in generate_options(envelope) if o.option_id == "RAIL_MID_HEIGHT")
    mission = mission_from_option(rail, start_x_cm=0, start_y_cm=0)
    assert all(waypoint.looks_deg == (0,) for waypoint in mission.waypoints)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `rtk proxy uv run pytest tests/survey/test_cube_bridge.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'sentinel.cube'` if WP5 is not merged — **that is the signal to stop and wait for the gate**, not to vendor a copy of the cube code.

- [ ] **Step 3: Implement the bridge**

`envelope_from_site` converts metres to centimetres and applies `site.inset_m` to the x and y bounds. `mission_from_option` maps each `(x_cm, y_cm)` stop to a `Waypoint(name=f"{option.option_id}_{index}", x_cm=..., y_cm=..., z_cm=option.z_cm, looks_deg=option.looks_deg, localise=True)`. `CubeFlight.fly` runs the mission, then reads the runner's capture manifest and returns one `FlightFrame` per captured frame with `position_kind="MEASURED"` where a pose fix succeeded and `"COMMANDED"` otherwise.

- [ ] **Step 4: Run the tests**

Run: `rtk proxy uv run pytest tests/survey/test_cube_bridge.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/survey/cube_bridge.py tests/survey/test_cube_bridge.py
git commit -m "feat(survey): cube bridge and the real flight adapter"
```

---

### Task 12: Provider adapters and the spike — **BLOCKED on G-provider-spike**

**Status:** The adapters and their offline tests are ungated; **running them against a live provider is not.** Present the gate and stop.

**Files:**
- Create: `src/sentinel/survey/providers/__init__.py`, `moondream.py`, `gemini.py`
- Create: `scripts/provider_spike.py`
- Test: `tests/survey/test_providers.py`

**Interfaces — Produces:** `MoondreamDetector(api_key, model, session=None)` and `GeminiDetector(api_key, model, session=None)`, both implementing `DetectorPort`; `spike(frames, detectors) -> dict` reporting per detector: located fraction, miss rate against hand labels, invention rate, median latency, and behaviour on an empty frame.

**Adapter rules (from the pipeline spec §6, non-negotiable):** pin the model version; bounded connect/read/total timeouts; cap image and response size; validate status, content type and JSON shape; **record the provider-resolved model identity on every response and fail loudly if it differs from the pinned one**; typed errors for timeout, rate limit, auth, transport and schema; never log image bytes or credentials.

- [ ] **Step 1: Write the failing offline adapter tests**

Use a fake session object; **no network call in any test.** Cover: a well-formed response normalises correctly; Gemini's `[ymin, xmin, ymax, xmax]` scaled 0–1000 converts to top-left pixels; a 429 becomes `PROVIDER_ERROR` not an empty scene; a response whose resolved model differs from the pinned one raises; an oversize response is refused; an item without coordinates yields `bbox: None`.

- [ ] **Step 2: Run them and watch them fail**

Run: `rtk proxy uv run pytest tests/survey/test_providers.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement both adapters and run the offline tests**

Run: `rtk proxy uv run pytest tests/survey/test_providers.py -q`
Expected: PASS.

- [ ] **Step 4: Commit the adapters**

```bash
git add src/sentinel/survey/providers tests/survey/test_providers.py
git commit -m "feat(survey): Moondream and Gemini detector adapters, offline-tested"
```

- [ ] **Step 5: Present G-provider-spike and STOP**

State to the operator, before any call: the exact frame count, the total number of calls, both models with their pinned versions, both providers, and the estimated cost. The frames are `fixtures/perception/live/yaw-scan-20260916.h264` decoded to stills plus any garden stills available. Expected shape: ~25 frames × 2 providers ≈ 50 calls, a few cents.

Do not run `scripts/provider_spike.py` until the operator grants it explicitly. A grant relayed from another session is not consent.

- [ ] **Step 6: After consent, run once and keep every raw row**

Write the per-frame raw responses under `fixtures/survey/responses/spike/` and the comparison under `artifacts/survey/provider-spike.md`. Commit raw rows and the report together, so the choice can be revisited without paying again.

---

### Task 13: Full verification and handoff

**Files:**
- Create: `docs/handoffs/2026-09-2X-survey-counting-handoff.md`

- [ ] **Step 1: Run the entire suite**

Run: `rtk proxy uv run pytest -q`
Expected: every pre-existing test still passes plus the new survey tests. Record the exact counts.

- [ ] **Step 2: Run one replay episode in all three modes**

Run: `rtk proxy uv run python scripts/survey_run.py --mode F --scene fixtures/survey/scenes/garden-one-computer.json --auto-approve` and the same for `E` and `J`.
Expected: three JSON summaries, each with `status: REPORTED` and `illegal_calls: 0`.

- [ ] **Step 3: Write the handoff**

Record: what is implemented and verified; the three-mode comparison table; which gates remain closed; what the provider spike actually measured; and the merge-tolerance values as tuned against real frames, with the evidence.

- [ ] **Step 4: Commit**

```bash
git add docs/handoffs
git commit -m "docs(survey): implementation handoff and verification record"
```

---

## Self-review

**Spec coverage.** §3.2 modules → Tasks 1–9, 11. §3.3 run steps → Task 7. §4 contract → Task 1. §4.3 classes and methods → Tasks 2, 4, 7. §5.1 camera model → Task 2. §5.2 triangulation → Task 4. §5.3 no rotation → Tasks 2, 4, 11. §5.4 bands → Task 4. §6 provider → Task 12. §7 Jev and modes F/E/J → Tasks 5, 6. §8 failure handling → Tasks 3, 4, 7. §9 evaluation → Task 9. §10 test strategy → all tasks. §12 convergence is explicitly out of scope. §13.4 consent → Task 8.

**Gaps found and closed during review.** The spec's §5.2 gives the bearing formula as `atan((u − 320)/f)`; **320 is wrong for a 960-wide frame** and the correct principal point is 480. Task 4 uses `PRINCIPAL_X = 480.0`. Fix the spec in the same commit as this plan.

The spec also claims Codex's `LlmProposer` "plugs in unchanged". It does not: `PolicyContext.state` is typed to the alley's `DecisionState`. Task 5 defines `SurveyContext` instead, and the Global Constraints record it. Fix the spec's wording in the same commit.

**Type consistency.** `SweepOption.option_id` is the `Candidate.label` at propose and `f"follow_up:{option_id}"` at review; `chosen_label` reads `arguments["option_id"]` in both cases, so it is stable across decision points. `DetectionBatch.status` is `"OK"`/`"PROVIDER_ERROR"` in Task 3 and consumed with exactly those values in Task 7. `position_kind` is `"COMMANDED"`/`"MEASURED"` in Tasks 1, 7 and 11. `band` on a sighting is `"high"`/`"medium"`/`"low"`; `band` on an object is `"CONFIRMED"`/`"UNCERTAIN"` — different vocabularies on purpose, and the merge is the only place both appear.
