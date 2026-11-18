---
name: chaos
stage: plan
---

# Chaos

State the steady state the plan assumes, then vary the real-world events around it, step by step. For each dependency, ask what the plan does when it is slow, absent, duplicated, reordered or half-finished: a job that runs twice, a request during a migration, a cache that outlives its row, a timeout that fires after the write, a client gone before the response, a commit that errors after it lands.

Look past the most visible failure to the second-order one: a hash collision, a lost connection, a retry that repeats a side effect.

A finding is a failure mode the criteria neither prevent nor acknowledge, with the state it leaves behind and what else breaks. Prefer data corruption and stranded users over a plain error.

Do not report failures the plan already handles or scopes out.

Evidence is the sentence or `path:line` that makes the failure possible.

Source: principlesofchaos.org.

## Output

Output only a JSON list. Each item: {"lens": "chaos", "severity": "P1"|"P2"|"P3", "path": "file", "line": 0, "summary": "one sentence", "fix": "one sentence", "evidence": "quote or path:line"}. P1 breaks an acceptance criterion or corrupts data, P2 is a gap a reviewer would send back, P3 is worth a sentence. A clean result is []. Write no prose around the list.
