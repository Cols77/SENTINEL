"""Deterministic, clearly simulated WP0 fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "sentinel" / "v0"
NORMATIVE_CONTRACT_DIR = Path(__file__).resolve().parents[3] / "docs" / "superpowers" / "specs" / "contracts"


def fixture_path(name: str) -> Path:
    path = FIXTURE_DIR / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def load_fixture(name: str) -> Any:
    return json.loads(fixture_path(name).read_text(encoding="utf-8"))


def load_events(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in fixture_path(name).read_text(encoding="utf-8").splitlines() if line.strip()]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fixture_provenance() -> str:
    """All generated WP0 fixtures are simulated and never judged evidence."""
    return "SIMULATED"


def mountain_fixture() -> dict[str, Any]:
    return json.loads((NORMATIVE_CONTRACT_DIR / "mountain-v0.fixture.json").read_text(encoding="utf-8"))
