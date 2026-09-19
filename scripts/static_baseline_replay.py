"""Static-baseline shadow replay (offline verification)."""
import json
from pathlib import Path

from sentinel.evaluation import run_cases
from sentinel.judgment import StaticJudge

cases = [json.loads(l) for l in Path("fixtures/judgment/cases.v1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
metrics, log_path = run_cases(
    cases,
    StaticJudge(booleans={"hazard_relevant": (True, 0.9)}),
    log_path=Path("artifacts/jev/static-baseline.jsonl"),
)
print(metrics, log_path)
