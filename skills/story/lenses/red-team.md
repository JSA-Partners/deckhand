---
name: red-team
stage: plan
---

# Red team

Read the plan as a motivated adversary who wants what the story protects. Assume every input is hostile, every caller is impersonating someone, and every boundary the text does not name does not exist.

A finding is a concrete sequence of steps that produces an outcome the acceptance criteria forbid or fail to forbid. Name the step, the outcome, and the criterion it defeats. Point at the task whose code would let it happen.

Cover ownership and permission checks, injection, untrusted ids, secrets in logs, and replayed or forged requests.

Do not report a hunch. Do not report a threat the story scopes out. Do not report generic hardening advice.

Evidence is a quote from the plan or a `path:line` in the repository.

## Output

Output only a JSON list. Each item: {"lens": "red-team", "severity": "P1"|"P2"|"P3", "path": "file", "line": 0, "summary": "one sentence", "fix": "one sentence", "evidence": "quote or path:line"}. P1 breaks an acceptance criterion or corrupts data, P2 is a gap a reviewer would send back, P3 is worth a sentence. A clean result is []. Write no prose around the list.
