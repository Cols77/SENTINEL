from sentinel.adapter.llm import AgentModelPort


def test_provider_neutral_port_imports_without_provider_or_credentials():
    assert AgentModelPort.__name__ == "AgentModelPort"
    assert not hasattr(AgentModelPort, "__module__") or AgentModelPort.__module__ == "sentinel.adapter.llm"
