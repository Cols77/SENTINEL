# Twelve generated inspection requests

These cases supplement the existing harness scenarios with agent-authored request
phrasing. They use only the current approved two-plan catalogue. They do not fill
or relabel the separate 24-slot v2 request set for a different draft catalogue.
Each case retains `synthetic_declared` label status.

The first three cases require the two middle stations without markers; the next
three require all six stations with marker verification. The remaining six
require escalation: three specify unavailable coverage or verification
granularity, one lacks a target, one contradicts itself, and one requests computer
counting outside the inspection catalogue. Unsupported wording does not become a
supported capability merely because a model selects a legal plan.

The existing runner supports these cases without code changes:

```powershell
rtk proxy C:/coding/sentinel/.venv/Scripts/python.exe scripts/proposal_sweep.py --fixtures fixtures/proposal_eval/generated-requests-v1.json --dry-run --repeats 1
```

Scripted responses are generated from these declared expected labels, so scripted
success establishes harness behavior only. A separately authorized live run can
measure fresh model behavior on this synthetic set; it cannot establish accuracy
on independently authored operator requests. Reviewer agreement on the labels
does not change their synthetic provenance.
