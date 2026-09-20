"""CoordinatorBoundary: the AgentLoop's port adapter over gateway + repository.

Moved here from ``tests/coordinator/harness.py`` (P4 Task 2) so production code
(``scripts/rehearsal_run.py``) does not import from ``tests/``.
"""
from __future__ import annotations

from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.repository import CoordinatorRepository

__all__ = ["CoordinatorBoundary"]


class CoordinatorBoundary:
    """Port adapter used by AgentLoop; mutations still go through gateway."""

    def __init__(self, repository: CoordinatorRepository, gateway: CommandGateway):
        self.repository = repository
        self.gateway = gateway

    def submit(self, command):
        return self.gateway.submit(command)

    def read_state(self, run_id):
        return self.repository.read_state(run_id).to_dict()

    def read_events(self, run_id, after_sequence, limit):
        return self.repository.read_events(run_id, after_sequence, limit)
