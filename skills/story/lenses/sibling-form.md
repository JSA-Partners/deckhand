---
name: sibling-form
stage: branch
---

# Sibling form

Compare new text with the sibling text beside it and report the mismatch: a comment or doc comment that departs from the form its neighbors use, a name that is plural where its siblings are singular, a file or test with no counterpart its siblings have, a fake whose comment contradicts its behavior, and a doc that no longer matches the shipped code.

Name both sides. The finding cites the new line and the sibling line it should match.

Do not report a departure that the diff applies to every sibling. Do not report a form the siblings do not share.

Evidence is the new `path:line` and the sibling `path:line`, written as one string.

## Output

Output only a JSON list. Each item: {"lens": "sibling-form", "severity": "P1"|"P2"|"P3", "path": "file", "line": 0, "summary": "one sentence", "fix": "one sentence", "evidence": "the new path:line and the sibling path:line, in one string"}. The path is the repository file the finding is about, or the plan document path when no code file applies. The line is 1-based, 0 when none. P1 breaks an acceptance criterion or corrupts data, P2 is a gap a reviewer would send back, P3 is worth a sentence. A clean result is []. Write no prose around the list.
