"""Probe: one LlmJudge evaluate call against the live API, printing the raw content."""
import json
import os
from pathlib import Path

from sentinel.evaluation import QUESTION_SET
from sentinel.judgment import LlmJudge

for line in Path("C:/coding/sentinel/.env").read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

cases = [json.loads(l) for l in Path("fixtures/judgment/cases.v1.jsonl")
         .read_text(encoding="utf-8").splitlines() if l.strip()]

judge = LlmJudge(api_key_env="OPENROUTER_API_KEY",
                 base_url="https://openrouter.ai/api", model="z-ai/glm-5.3",
                 timeout_s=120)

payload = {
    "model": judge._model,
    "messages": [
        {"role": "system", "content": LlmJudge.SYSTEM_INSTRUCTIONS},
        {"role": "user", "content": json.dumps(
            {"state": "A single hiker standing well off-trail resting.",
             "questions": QUESTION_SET})},
    ],
    "temperature": 0,
    "response_format": {"type": "json_object"},
}
import httpx
resp = httpx.post("https://openrouter.ai/api/v1/chat/completions",
                  headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]},
                  json=payload, timeout=120)
print("status", resp.status_code)
try:
    content = resp.json()["choices"][0]["message"]["content"]
    print("content:", content[:1200])
    parsed = json.loads(content)
    for name, ans in parsed["answers"].items():
        print(name, sorted(ans.keys()))
except Exception as exc:
    print("parse fail:", exc, resp.text[:600])
