# v2 dry run summary (offline, no network)

Fixture: fixtures/judgment/cases.v2.jsonl; repeats=3 (baselines deterministic, repeats=1).
Fake Jev transport: scripted answers injected via JevJudge's transport hook; no socket, no key, no provider call.
Label source: approved v2 case-level hazard/needs_review labels; hazard-null (amb-*) cases are skipped for hazard and scored for review.

| arm | hazard FS/FE/scored | review FS/FE/scored | pos-* false-safe | review recall | stability flips | ranks vs always-escalate |
|---|---|---|---|---|---|---|
| static_v2 | 0/7/15 | 0/0/0 | 0/4 | n/a | 0/15 (count 0) | no |
| jev_fake_transport | 4/0/15 | 2/5/21 | 0/4 | 0.818 | 0/36 (count 0) | no |
| baseline-always_safe | 8/0/15 | 11/0/21 | 4/4 | 0.0 | 0/36 (count 0) | no |
| baseline-always_escalate | 0/7/15 | 0/10/21 | 0/4 | 1.0 | 0/36 (count 0) | -- |
| baseline-static_rules | 5/0/15 | 1/8/21 | 1/4 | 0.909 | 0/36 (count 0) | no |

Notes:
- static_v2 answers True@0.9 to every hazard question: its 0 false-safe is by construction (constant), and every reference-negative hazard case is a false-escalation. The v1 run's 'static 0 false-safe' claim cannot support go/no-go.
- always_escalate: 0 false-safe by construction, so any judge only ranks if it beats it on false-escalation at equal-or-better false-safe (decision_rule).
- Jev noul wire answers carry no self-confidence field, so noul routing bands report 'unknown'; the plain 0.5 threshold carries the routing signal. choice/score answers carry confidence and band normally.
- Known flake: tests/test_env_file.py::test_parses_comments_and_quoted_values (tracked by WP1 card t_45e8629b, out of scope here).
