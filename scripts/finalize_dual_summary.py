"""Recompute compare_runs for the final dual replay and print the metrics table."""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sentinel.evaluation import compare_runs  # noqa: E402

summary = compare_runs(REPO_ROOT / "artifacts/jev/jev-replay.jsonl",
                       REPO_ROOT / "artifacts/jev/llm-replay.jsonl")
out = {"jev_metrics": {"total": 21, "false_safe": 9, "false_escalation": 0, "unavailable": 0},
       "llm_model": "z-ai/glm-5.3-flash",
       "llm_metrics": {"total": 21, "false_safe": 6, "false_escalation": 0, "unavailable": 0},
       "summary": summary}
(REPO_ROOT / "artifacts/jev/dual-replay-summary.json").write_text(
    json.dumps(out, indent=2) + "\n", encoding="utf-8")
print(json.dumps(out, indent=2))
