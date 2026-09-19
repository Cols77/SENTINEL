"""Probe: send case[0] state with criteria:None question to the decisions API."""
import json
import os
import sys
from pathlib import Path

import httpx

for line in Path("C:/coding/sentinel/.env").read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

cases = [json.loads(l) for l in Path("fixtures/judgment/cases.v1.jsonl")
         .read_text(encoding="utf-8").splitlines() if l.strip()]
state = cases[0]["state"]
wire = {"hazard_relevant": {"type": "noul", "instructions": "q?", "criteria": None}}
r = httpx.post("https://openrouter.ai/api/alpha/decisions",
               headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]},
               json={"model": "~typesafe/jev-latest", "state": state, "questions": wire},
               timeout=10)
print(r.status_code)
print(r.text[:500])
