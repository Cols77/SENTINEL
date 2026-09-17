"""Catalogue loading and immutable catalogue-bound plan resolution."""

from __future__ import annotations

import hashlib
import json
import re
from json import JSONDecodeError
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .errors import CoordinatorError

_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


class CatalogueResolver:
    def __init__(self, document: Mapping[str, Any]):
        self._document = document
        self._entries = {entry["id"]: entry for entry in document["entries"]}

    @classmethod
    def from_fixture(cls, path: str | Path) -> "CatalogueResolver":
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError) as exc:
            raise CoordinatorError("INVALID_CONTRACT", f"cannot load catalogue fixture: {exc}") from exc
        schema_path = Path(path).resolve().parents[1] / "survey-catalogue-v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(document))
        if errors:
            raise CoordinatorError("INVALID_CONTRACT", errors[0].message)
        if len({entry["id"] for entry in document["entries"]}) != len(document["entries"]):
            raise CoordinatorError("INVALID_CONTRACT", "catalogue contains duplicate plan IDs")
        return cls(document)

    @property
    def document(self) -> Mapping[str, Any]:
        return self._document

    def resolve(
        self,
        catalogue_id: str,
        entry_id: str,
        plan_id: str,
    ) -> Mapping[str, Any]:
        if catalogue_id != self._document["catalogue_id"] or not _ID_RE.fullmatch(catalogue_id):
            raise CoordinatorError("INVALID_CONTRACT", f"unknown catalogue ID: {catalogue_id}")
        if entry_id not in self._entries:
            raise CoordinatorError("INVALID_CONTRACT", f"unknown catalogue plan ID: {entry_id}")
        if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", plan_id):
            raise CoordinatorError("INVALID_CONTRACT", "plan_id is not a canonical UUID")
        entry = self._entries[entry_id]
        station_ids = tuple(step["zone_id"] for step in entry["station_steps"])
        canonical = {
            "plan_id": plan_id,
            "catalogue_id": catalogue_id,
            "catalogue_revision": self._document["revision"],
            "station_ids": station_ids,
        }
        canonical_json = json.dumps(canonical, separators=(",", ":"))
        plan = {
            "plan_id": plan_id,
            "catalogue_id": catalogue_id,
            "catalogue_revision": self._document["revision"],
            "plan_sha256": hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
            "station_ids": station_ids,
        }
        return MappingProxyType(plan)

    def entry(self, entry_id: str) -> Mapping[str, Any]:
        try:
            return MappingProxyType(self._entries[entry_id])
        except KeyError as exc:
            raise CoordinatorError("INVALID_CONTRACT", f"unknown catalogue plan ID: {entry_id}") from exc
