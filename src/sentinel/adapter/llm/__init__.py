"""Provider-neutral LLM adapter contracts.

The optional provider implementations live in sibling modules and are imported
only when the caller selects one explicitly.
"""

from __future__ import annotations

from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from sentinel.contracts import AgentRequest, AgentModelResult


@runtime_checkable
class AgentModelPort(Protocol):
    """The provider-neutral model boundary used by the agent loop.

    ``read_tools`` is supplied by the coordinator-owned capability view. Model
    implementations may receive it for composition, but must not expose or
    execute coordinator state outside that injected boundary.
    """

    def propose(
        self,
        request: "AgentRequest",
        read_tools: Any = None,
    ) -> "AgentModelResult":
        """Return a typed result for one bounded model turn."""
        ...


__all__ = ["AgentModelPort"]
