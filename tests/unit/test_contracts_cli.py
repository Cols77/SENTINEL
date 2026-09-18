"""CLI tests for ``python -m sentinel.contracts validate`` (KB-BOOT-12)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "docs" / "superpowers" / "specs" / "contracts" / "fixtures"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "sentinel.contracts", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )


def test_valid_fixture_exits_zero_with_ok() -> None:
    result = run_cli("validate", str(FIXTURES / "judged-perception-live.valid.json"))
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_invalid_fixture_exits_one_with_machine_readable_errors() -> None:
    result = run_cli(
        "validate", str(FIXTURES / "judged-perception-simulated.invalid.json")
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert isinstance(payload, list) and payload
    assert all({"code", "message"} <= entry.keys() for entry in payload)


def test_missing_file_exits_two() -> None:
    result = run_cli("validate", str(FIXTURES / "does-not-exist.json"))
    assert result.returncode == 2


def test_usage_error_exits_two() -> None:
    result = run_cli("validate")
    assert result.returncode == 2


def test_wire_schema_option_is_supported() -> None:
    result = run_cli(
        "validate",
        str(FIXTURES / "judged-perception-live.valid.json"),
        "--schema",
        "wire",
    )
    assert result.returncode == 0
    assert "OK" in result.stdout
