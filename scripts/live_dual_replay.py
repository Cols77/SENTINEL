#!/usr/bin/env python
"""LIVE dual-arm replay: Jev vs LLM over the reference-labeled cases.

T12 consented run. Parses the repo .env for env-var NAMES (values are read
at runtime by the judges via os.environ and never printed). Writes
artifacts/jev/jev-replay.jsonl and artifacts/jev/llm-replay.jsonl via
evaluation.run_cases, then compare_runs.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def load_dotenv_names(path: Path) -> dict[str, str]:
    """Parse .env into a dict (names only are returned to the caller's logic;
    values are only ever placed into os.environ)."""
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        name, value = line.split("=", 1)
        env[name.strip()] = value.strip()
    return env


def main() -> int:
    import os

    from sentinel.evaluation import QUESTION_SET, compare_runs, run_cases
    from sentinel.judgment import JevJudge, LlmJudge

    env = load_dotenv_names(Path("C:/coding/sentinel/.env"))
    os.environ.update({k: v for k, v in env.items() if not os.environ.get(k)})

    cases = [json.loads(l) for l in (REPO_ROOT / "fixtures/judgment/cases.v1.jsonl")
             .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(cases) == 21, f"expected 21 cases, got {len(cases)}"

    # Arm A: Jev (batched decisions API, via OpenRouter — live-verified path)
    jev = JevJudge(api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai")
    jev_metrics, _ = run_cases(cases, jev, log_path=REPO_ROOT / "artifacts/jev/jev-replay.jsonl")

    # Arm B: LLM baseline (OpenAI-compatible JSON mode via OpenRouter)
    model = os.environ.get("SENTINEL_LLM_MODEL", "").strip()
    key_env = os.environ.get("SENTINEL_LLM_API_KEY_ENV", "OPENROUTER_API_KEY").strip()
    llm = LlmJudge(api_key_env=key_env, base_url="https://openrouter.ai/api", model=model)
    llm_metrics, _ = run_cases(cases, llm, log_path=REPO_ROOT / "artifacts/jev/llm-replay.jsonl")

    summary = compare_runs(REPO_ROOT / "artifacts/jev/jev-replay.jsonl",
                           REPO_ROOT / "artifacts/jev/llm-replay.jsonl")
    out = {"llm_model": model, "jev_metrics": jev_metrics, "llm_metrics": llm_metrics,
           "summary": summary}
    (REPO_ROOT / "artifacts/jev/dual-replay-summary.json").write_text(
        json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
