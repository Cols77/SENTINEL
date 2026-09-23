"""The survey flow's own policy seam.

Structurally identical to ``coordinator.policy.ContextualPolicy``, but with
its own context type: ``PolicyContext.state`` is typed to the alley's
``DecisionState`` and cannot carry survey state. ``Candidate`` and
``LegalSet`` are reused unchanged because both are fully generic.

Rev-4 amendment V8d: the Interfaces line in the plan's Task 5 write-up reads
``SurveyContext(store, run_id, legal, results)``, but the dataclass has no
``store`` field and every call site passes three arguments. There is no
``store`` field here, by design -- the store is a side-effecting dependency
the proposer never touches; it only ever sees ``run_id``, the current
``LegalSet``, and prior ``results``.
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
