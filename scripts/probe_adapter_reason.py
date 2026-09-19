"""Print the exact fail-closed reason for one LlmJudge call through the adapter."""
import json
import os
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sentinel.evaluation import QUESTION_SET
from sentinel.judgment import LlmJudge

for line in Path("C:/coding/sentinel/.env").read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

cases = [json.loads(l) for l in (REPO_ROOT / "fixtures/judgment/cases.v1.jsonl")
         .read_text(encoding="utf-8").splitlines() if l.strip()]
model = os.environ.get("REPLAY_LLM_MODEL", "z-ai/glm-5.3-flash")
judge = LlmJudge(api_key_env="OPENROUTER_API_KEY",
                 base_url="https://openrouter.ai/api", model=model, timeout_s=120)
try:
    judge.evaluate(cases[0]["state"], QUESTION_SET)
except Exception:
    traceback.print_exc()
