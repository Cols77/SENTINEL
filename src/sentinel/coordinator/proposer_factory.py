"""Shared rehearsal/evaluation proposer construction with explicit live gates."""
from __future__ import annotations

import os
from typing import Any, Callable

from .policy import PolicyContext, one_call
from .proposer import JevProposer, LlmProposer

JEV_PROPOSER_CONSENT_ENV = "SENTINEL_JEV_PROPOSER_CONSENT"
LLM_PROPOSER_CONSENT_ENV = "SENTINEL_LLM_PROPOSER_CONSENT"
JEV_PROPOSER_MODEL = "typesafe/jev-1.13-20260917"


class ScriptedProposer:
    """A fixed proposal for offline wiring checks; no model-quality claim."""

    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id

    def decide(self, request: dict, context: PolicyContext) -> dict:
        return one_call(request, "inspection.propose_plan", {"catalogue_plan_id": self.plan_id})


def require_proposer_consent(spec: str) -> str:
    variable = {"jev": JEV_PROPOSER_CONSENT_ENV, "llm": LLM_PROPOSER_CONSENT_ENV}[spec]
    consent = os.environ.get(variable, "")
    if not consent.strip():
        raise PermissionError(
            f"{variable} is not set: '--proposer {spec}' can make a paid remote call "
            f"and requires fresh explicit operator consent (plan gate G-live-{spec}-proposer)"
        )
    return consent


def require_jev_proposer_consent() -> str:
    return require_proposer_consent("jev")


def _build_jev_judge(**kwargs: Any) -> Any:
    from sentinel.judgment import JevJudge
    return JevJudge(**kwargs)


def _build_llm_adapter(**kwargs: Any) -> Any:
    from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
    return OpenAICompatibleAgentModel(**kwargs)


def build_proposer(spec: str | None, *, sink: Any = None,
                   judge_factory: Callable[..., Any] | None = None,
                   adapter_factory: Callable[..., Any] | None = None,
                   mode: str = "live", authorization: Callable[[], Any] | None = None,
                   config: Any = None) -> Any:
    """Offline model arms require injected transports; live gates run first.

    Evaluation supplies its manifest authorization callback *in addition* to
    the existing provider-specific consent. Rehearsal retains its own gates.
    No sink or artifact is constructed here.
    """
    if mode not in {"live", "scripted", "recorded"}:
        raise ValueError("unknown execution mode")
    if spec is None:
        return None
    if spec.startswith("scripted:") and spec[len("scripted:"):]:
        return ScriptedProposer(spec[len("scripted:"):])
    if spec not in {"jev", "llm"}:
        raise ValueError("expected 'llm', 'jev' or 'scripted:<PLAN_ID>'")
    if mode == "live":
        if authorization is not None:
            authorization()
        require_proposer_consent(spec)
    elif (judge_factory if spec == "jev" else adapter_factory) is None:
        raise ValueError("offline model arms require an injected transport")
    if sink is None:
        raise ValueError("the proposer requires a shared audit sink")
    if spec == "jev":
        judge = (judge_factory or _build_jev_judge)(
            api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai",
            model=JEV_PROPOSER_MODEL)
        return JevProposer(judge, sink)
    if config is None:
        from sentinel.adapter.llm.config import load_config
        config = load_config()
    return LlmProposer((adapter_factory or _build_llm_adapter)(config=config), sink)
