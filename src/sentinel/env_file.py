"""Dependency-free .env file discovery and loading.

Secret-safe by contract: values are parsed and returned in-memory, never
logged or persisted anywhere by this module.
"""

from __future__ import annotations

from pathlib import Path
import os
from typing import Mapping


def find_env_file(start_dir: Path | None = None) -> Path | None:
    """Return the nearest ``.env`` path searching upward, or None."""
    current = (start_dir or Path.cwd()).resolve()
    while True:
        candidate = current / ".env"
        if candidate.is_file():
            return candidate
        if current.parent == current:
            return None
        current = current.parent


def parse_env_text(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines; supports blank lines, ``#`` comments, quotes."""
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Strip a trailing comment only for unquoted values.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        result[key] = value
    return result


def load_env_file(
    search_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Populate the process environment from the nearest ``.env`` file.

    Only variables NOT already set in ``environ`` (default ``os.environ``)
    are populated: explicit environment values always win.  Returns the
    mapping of variables that were actually applied; the file is never
    logged and nothing secret is stored beyond the returned dict.
    """
    target = dict(os.environ if environ is None else environ)
    path = find_env_file(search_dir)
    if path is None:
        return {}
    applied: dict[str, str] = {}
    for key, value in parse_env_text(path.read_text(encoding="utf-8")).items():
        if key in target:
            continue
        if environ is None:
            os.environ[key] = value
        applied[key] = value
    return applied


__all__ = ["find_env_file", "load_env_file", "parse_env_text"]
