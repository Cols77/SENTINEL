# J5 live v2 shadow replay summary

Fixture: fixtures/judgment/cases.v2.jsonl; repeats=3; cases=21.
Arms: Jev via /api/alpha/decisions; LLM baseline z-ai/glm-5.3-flash via JSON mode.
Jev remains SHADOW_EVALUATION_ONLY regardless of outcome.

| arm | hazard FS/FE/scored | review FS/FE/scored | clean-pos FS | review recall | stability | ranks vs always-escalate |
|---|---|---|---|---|---|---|
| jev | 1/2/15 | 0/10/21 | 1/4 | 1.0 | 1/36 (count 1) | no |
| llm | 0/3/15 | 0/5/21 | 0/4 | 1.0 | 4/36 (count 6) | YES |

Decision rule: an arm ranks only if it has equal-or-better false-safe outcomes and beats always-escalate on false escalation; the task's worse criterion additionally requires losing on clean-positive false-safe and needs_review recall.

Provenance: committed J5 logs predate the fix; their `judge.model` field reads `static` for both arms and is wrong; the Jev endpoint resolved `~typesafe/jev-latest`, not the consented pinned revision.
