"""Shared construction must gate providers before initialization or artifacts."""
import pytest

from scripts import rehearsal_run
from sentinel.coordinator.proposer import LlmProposer
from sentinel.coordinator.shadow import MemoryAuditSink


def test_llm_selector_requires_consent_before_factory(monkeypatch):
    monkeypatch.delenv("SENTINEL_LLM_PROPOSER_CONSENT", raising=False)
    initialized = []
    with pytest.raises(PermissionError):
        rehearsal_run.build_proposer("llm", sink=MemoryAuditSink(),
                                     adapter_factory=lambda **kw: initialized.append(kw))
    assert initialized == []


def test_llm_selector_uses_existing_configuration_and_shared_sink(monkeypatch):
    monkeypatch.setenv("SENTINEL_LLM_PROPOSER_CONSENT", "test-batch")
    values = {"PROVIDER": "openrouter", "MODEL": "test-model", "TIMEOUT_MS": "1000",
              "PROMPT_REVISION": "v1", "TOOL_POLICY_REVISION": "v1"}
    for key, value in values.items():
        monkeypatch.setenv("SENTINEL_LLM_" + key, value)
    sink = MemoryAuditSink()
    initialized = []
    adapter = object()
    def factory(**kwargs):
        initialized.append(kwargs)
        return adapter
    proposer = rehearsal_run.build_proposer("llm", sink=sink, adapter_factory=factory)
    assert isinstance(proposer, LlmProposer)
    assert proposer.adapter is adapter and proposer.sink is sink
    assert initialized[0]["config"].model == "test-model"


def test_llm_cli_refuses_before_creating_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("SENTINEL_LLM_PROPOSER_CONSENT", raising=False)
    root = tmp_path / "absent"
    assert rehearsal_run.main(["--advisors", "none", "--architecture", "E",
                               "--proposer", "llm", "--db", str(root / "db"),
                               "--audit", str(root / "audit")]) == 2
    assert not root.exists()


@pytest.mark.parametrize("mode", ["scripted", "recorded"])
@pytest.mark.parametrize("arm", ["jev", "llm"])
def test_offline_construction_requires_injected_transport(mode, arm):
    with pytest.raises(ValueError, match="injected transport"):
        rehearsal_run.build_proposer(arm, mode=mode, sink=MemoryAuditSink())


def test_manifest_authorization_precedes_provider_factory(monkeypatch):
    monkeypatch.setenv("SENTINEL_JEV_PROPOSER_CONSENT", "existing-consent")
    initialized = []
    def refuse():
        raise PermissionError("manifest mismatch")
    with pytest.raises(PermissionError, match="manifest mismatch"):
        rehearsal_run.build_proposer("jev", sink=MemoryAuditSink(),
                                     authorization=refuse,
                                     judge_factory=lambda **kw: initialized.append(kw))
    assert initialized == []
