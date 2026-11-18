---
name: tests
stage: plan, branch
---

# Tests

Plan: every acceptance criterion needs a test that fails for the behavior reason before the code exists. Find a criterion with no test, a test step that shows no code or no red failure, a comment stub in place of a test, a verification tool that cannot run in this repository, and a test that restates a fixture instead of behavior.

Branch: find a change whose tests still pass when the change is deleted. Name the line to delete. Find a test file with no source file of the same name, an assertion on mock behavior, and a missing case the diff makes reachable.

Do not report coverage percentages, test naming taste, or cases the type system rules out.

Evidence is the criterion with the missing test, or the `path:line` whose removal leaves the suite green.

## Output

Output only a JSON list. Each item: {"lens": "tests", "severity": "P1"|"P2"|"P3", "path": "file", "line": 0, "summary": "one sentence", "fix": "one sentence", "evidence": "quote or path:line"}. P1 breaks an acceptance criterion or corrupts data, P2 is a gap a reviewer would send back, P3 is worth a sentence. A clean result is []. Write no prose around the list.
