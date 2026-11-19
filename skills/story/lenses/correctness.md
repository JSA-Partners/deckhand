---
name: correctness
stage: plan, branch
---

# Correctness

Plan: read each task as code that will run. Find a step whose result contradicts an acceptance criterion, an ordering that cannot hold, a state a later task needs that no earlier task makes, and an edge the criteria name that no task handles.

Branch: read the diff as the code that ships. Find logic that gives a wrong result, a missed error path, a changed behavior no test or criterion mentions, and a criterion the diff does not meet. Open the files around the diff when a hunk depends on them.

Do not report style, naming, missing comments or taste. Do not report what lint or the compiler catches. Do not report a risk with no concrete input that triggers it.

Evidence is the plan sentence or the `path:line` that shows the fault, with the input that exposes it.

## Output

Output only a JSON list. Each item: {"lens": "correctness", "severity": "P1"|"P2"|"P3", "path": "file", "line": 0, "summary": "one sentence", "fix": "one sentence", "evidence": "the plan sentence or a path:line that shows the fault, with the triggering input"}. The path is the repository file the finding is about, or the plan document path when no code file applies. The line is 1-based, 0 when none. P1 breaks an acceptance criterion or corrupts data, P2 is a gap a reviewer would send back, P3 is worth a sentence. A clean result is []. Write no prose around the list.
