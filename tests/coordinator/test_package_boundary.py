from pathlib import Path


def test_package_boundary_exports_ports_and_wp0_types_without_provider_imports():
    from sentinel.contracts import (
        AgentModelResult,
        AgentRequest,
        AgentToolCall,
        AgentToolResult,
    )
    from sentinel.coordinator import CoordinatorPort, PerceptionPort, WorkerPort

    assert all((AgentModelResult, AgentRequest, AgentToolCall, AgentToolResult))
    assert all((CoordinatorPort, WorkerPort, PerceptionPort))
    source = Path(__import__("sentinel.coordinator", fromlist=["__name__"]).__file__).read_text()
    lowered = source.lower()
    assert not any(name in lowered for name in ("nicegui", "openai", "djitellopy"))
