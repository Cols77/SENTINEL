# J5 offline band re-score

No new calls. Input: committed `v2-live-*.jsonl`. n is tiny (15 scored cases x 3 repeats); directional only.

| arm | uncertain band | scored | confident correct | escalated | false-safe | false-escalation |
|---|---|---|---|---|---|---|
| jev | 0.45-0.55 | 45 | 34 | 8 | 0 | 3 |
| jev | 0.40-0.60 | 45 | 25 | 17 | 0 | 3 |
| jev | 0.35-0.65 | 45 | 19 | 23 | 0 | 3 |
| llm | 0.45-0.55 | 45 | 37 | 1 | 0 | 7 |
| llm | 0.40-0.60 | 45 | 36 | 3 | 0 | 6 |
| llm | 0.35-0.65 | 45 | 34 | 5 | 0 | 6 |
