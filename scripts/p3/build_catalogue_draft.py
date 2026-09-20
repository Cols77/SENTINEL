"""Build the DRAFT rich survey catalogue (P3a Task 1). Content for operator review only:
it cannot validate under survey-catalogue-v1.schema.json and no production path loads it."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json"
OUT = ROOT / "fixtures/catalogue_draft/survey-catalogue-v2.draft.json"

MARKERS = {"LEFT_ALLEY_FRONT": "TAG_LEFT_FRONT", "LEFT_ALLEY_MIDDLE": "TAG_LEFT_MIDDLE",
           "LEFT_ALLEY_BACK": "TAG_LEFT_BACK", "RIGHT_ALLEY_FRONT": "TAG_RIGHT_FRONT",
           "RIGHT_ALLEY_MIDDLE": "TAG_RIGHT_MIDDLE", "RIGHT_ALLEY_BACK": "TAG_RIGHT_BACK"}
LEFT = ("LEFT_ALLEY_FRONT", "LEFT_ALLEY_MIDDLE", "LEFT_ALLEY_BACK")
RIGHT = ("RIGHT_ALLEY_FRONT", "RIGHT_ALLEY_MIDDLE", "RIGHT_ALLEY_BACK")

PLANS: list[tuple[str, str, tuple[str, ...]]] = [
    *[(f"PLAN_ROUTE_{s}", "ROUTE_ONLY", (s,)) for s in LEFT + RIGHT],
    ("PLAN_ROUTE_LEFT_ALLEY", "ROUTE_ONLY", LEFT),
    ("PLAN_ROUTE_RIGHT_ALLEY", "ROUTE_ONLY", RIGHT),
    ("PLAN_ROUTE_FRONT_PAIR", "ROUTE_ONLY", ("LEFT_ALLEY_FRONT", "RIGHT_ALLEY_FRONT")),
    ("PLAN_ROUTE_MIDDLE_PAIR", "ROUTE_ONLY", ("LEFT_ALLEY_MIDDLE", "RIGHT_ALLEY_MIDDLE")),
    ("PLAN_ROUTE_BACK_PAIR", "ROUTE_ONLY", ("LEFT_ALLEY_BACK", "RIGHT_ALLEY_BACK")),
    ("PLAN_VERIFIED_LEFT_ALLEY", "MARKER_VERIFIED", LEFT),
    ("PLAN_VERIFIED_RIGHT_ALLEY", "MARKER_VERIFIED", RIGHT),
    ("PLAN_VERIFIED_FULL_SITE", "MARKER_VERIFIED", LEFT + RIGHT),
]


def build() -> dict:
    v1 = json.loads(V1.read_text(encoding="utf-8"))
    template = {("MARKER_VERIFIED" if e["mode"] == "MARKER_VERIFIED" else "ROUTE_ONLY"): e for e in v1["entries"]}
    entries = []
    for plan_id, mode, stations in PLANS:
        base = template[mode]
        entries.append({
            "id": plan_id, "revision": "DRAFT_REV_2", "sha256": "0" * 64, "mode": mode,
            "station_steps": [{"index": i, "zone_id": s, "marker_id": MARKERS[s] if mode == "MARKER_VERIFIED" else None}
                              for i, s in enumerate(stations, 1)],
            "capture_headings_deg": base["capture_headings_deg"], "capture_count": base["capture_count"],
            "segment_heights_m": [{"zone_id": s, "height_m": 1.2} for s in stations],
            "route_bounds": base["route_bounds"], "timeout": base["timeout"], "command_budget": base["command_budget"],
        })
    return {"contract_version": "sentinel.survey-catalogue.v1", "catalogue_id": "SURVEY_CATALOGUE_V2_DRAFT",
            "revision": "DRAFT_REV_2", "sha256": "0" * 64, "entries": entries}


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(PLANS)} plans)")
