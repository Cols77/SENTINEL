#!/usr/bin/env python
"""Retry ONLY the LLM arm of the dual replay (consented; Jev arm already done)."""
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        name, value = line.split("=", 1)
        env[name.strip()] = value.strip()
    return env


def main() -> int:
    from sentinel.evaluation import run_cases
    from sentinel.judgment import LlmJudge

    os.environ.update({k: v for k, v in load_dotenv(Path("C:/coding/sentinel/.env")).items()
                       if not os.environ.get(k)})
    model = os.environ.get("REPLAY_LLM_MODEL", "z-ai/glm-5.3-flash")
    cases = [json.loads(l) for l in (REPO_ROOT / "fixtures/judgment/cases.v1.jsonl")
             .read_text(encoding="utf-8").splitlines() if l.strip()]
    llm = LlmJudge(api_key_env="OPENROUTER_API_KEY",
                   base_url="https://openrouter.ai/api", model=model, timeout_s=120)
    metrics, _ = run_cases(cases, llm, log_path=REPO_ROOT / "artifacts/jev/llm-replay.jsonl")
    print("model:", model)
    print(json.dumps(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
