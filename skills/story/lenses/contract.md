---
name: contract
stage: plan
---

# Contract

Trace every library, vendor or API call the plan relies on to its source or live documentation. Read the vendored code or the official docs, not memory. For each call, check that the function exists, takes those arguments, returns that shape, and behaves that way on conflict, delete, retry and empty input.

An outside behavior a criterion rests on, and that the plan does not probe in a step, is a finding. Do not park it in an unknowns note.

Also check the plan against what already exists: named files, symbols and interfaces must be real, and each docs file or description the change makes false must be listed with the sentence.

Do not report style, naming or taste. Do not list callers the compiler will find.

Evidence is the documentation URL, the source `path:line`, or the plan sentence that assumes the behavior.

## Output

Output only a JSON list. Each item: {"lens": "contract", "severity": "P1"|"P2"|"P3", "path": "file", "line": 0, "summary": "one sentence", "fix": "one sentence", "evidence": "a documentation URL, a source path:line, or the plan sentence that assumes the behavior"}. The path is the repository file the finding is about, or the plan document path when no code file applies. The line is 1-based, 0 when none. P1 breaks an acceptance criterion or corrupts data, P2 is a gap a reviewer would send back, P3 is worth a sentence. A clean result is []. Write no prose around the list.
