---
name: idiomatic
stage: branch
---

# Idiomatic

Find code that is not the idiomatic form for its language and library, in this codebase. Every finding must cite proof: a URL to the official documentation or a live source, or a `path:line` precedent in this repository that does it the other way. A finding with neither is taste. Leave it out.

Look for a hand-built version of something the standard library or an existing helper does, a second pattern beside an established one, and a construct whose idiom has moved in the current version of the language or library.

Do not report formatting or what lint already checks. Do not report naming preferences.

The `evidence` field must be a URL or `path:line`. The `fix` names the idiomatic form.

## Output

Output only a JSON list. Each item: {"lens": "idiomatic", "severity": "P1"|"P2"|"P3", "path": "file", "line": 0, "summary": "one sentence", "fix": "one sentence", "evidence": "a URL or a path:line precedent, never prose"}. The path is the repository file the finding is about, or the plan document path when no code file applies. The line is 1-based, 0 when none. P1 breaks an acceptance criterion or corrupts data, P2 is a gap a reviewer would send back, P3 is worth a sentence. A clean result is []. Write no prose around the list.
