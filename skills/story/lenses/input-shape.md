---
name: input-shape
stage: plan
---

# Input shape

Parse, do not validate. A value should enter the system once, as the strongest type the system needs, and never be checked again. Find a plan that lets a value in as something weaker and checks it later or never: a date carried as a string, an id that may not exist, an enum that is free text, an optional that the third call requires, a name that may hold control characters before it reaches an outside service.

Two smells: checks on one value scattered across tasks, and a type that admits values no consumer can handle.

A finding names the value, where it enters, the shape it should have there, and where the missing shape bites. Name the task that should construct the stronger type.

Do not report internal helpers that only receive already parsed values. Do not ask for a type no consumer needs.

Evidence is the interface line or plan step.

Source: Alexis King, "Parse, don't validate" (2019).

## Output

Output only a JSON list. Each item: {"lens": "input-shape", "severity": "P1"|"P2"|"P3", "path": "file", "line": 0, "summary": "one sentence", "fix": "one sentence", "evidence": "the interface line or plan step where the weaker value enters"}. The path is the repository file the finding is about, or the plan document path when no code file applies. The line is 1-based, 0 when none. P1 breaks an acceptance criterion or corrupts data, P2 is a gap a reviewer would send back, P3 is worth a sentence. A clean result is []. Write no prose around the list.
