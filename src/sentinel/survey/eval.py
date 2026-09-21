# src/sentinel/survey/eval.py
"""Episode-level scoring.

Each stage is scored separately so a wrong count can be attributed: an
aggregate would hide whether the provider, the merge or the planning was at
fault. Every arm replays stored frames, so a full three-mode comparison
costs nothing and can be re-run after any tolerance change.

Rev-4 corrections against the plan body's Task 9 (see the plan's "Rev 3
amendments" R8 and "Rev 4 amendments" V8c -- this module implements those,
not the task body's original code):

R8/V8c: ``decision_quality`` is computed by ``loop.run_survey`` itself (see
its own R8/V8c docstring), which records the uncertain set and the
``addresses`` list of the chosen and the best-available follow-up at every
``REVIEW_RESULTS`` turn -- the only place that context exists.
``score_episode`` must not re-derive it; it only reads
``outcome["decision_quality"]`` off the outcome ``run_survey`` already
returns.

R8: ``depth_spread_pct`` and ``dropped_labels`` are added here as
attribution signals, so an undercount can be told apart as "the provider
never saw it" (``dropped_labels`` -- an unmapped label, accumulated across
every frame this run ingested) from "the geometry disagreed with itself"
(``depth_spread_pct`` -- the merge's per-object ground-plane depth spread,
``merge.py``'s ``DEGRADED_SPREAD_PCT`` gate, stored on ``objects`` by
``store.set_objects``). ``dropped_labels`` is read straight off the
outcome (``run_survey`` already accumulates it); ``depth_spread_pct`` is
the mean spread over every merged object that has one (an unpositioned,
single-sighting object has none -- ``merge._unmerged`` leaves it ``None``,
and ``None`` here means either no object had a computable spread, or none
of the objects this run reported carried a box at all).
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

    # R8: mean depth-spread over every merged object that has one -- the
    # geometry-disagreement signal, kept separate from dropped_labels'
    # provider-miss signal.
    spreads = [o["depth_spread_pct"] for o in reported if o.get("depth_spread_pct") is not None]
    depth_spread_pct = sum(spreads) / len(spreads) if spreads else None

    return {
        "scene_id": scene.scene_id,
        "count_error": count_error,
        "localisation": matched / len(scene.objects) if scene.objects else None,
        "decision_quality": outcome.get("decision_quality"),
        "depth_spread_pct": depth_spread_pct,
        "dropped_labels": outcome.get("dropped_labels", ()),
        "flights": outcome["flights"],
        "illegal_calls": sum(str(e.get("result", {})).count("illegal_tool")
                             for e in outcome.get("escalations", [])),
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
