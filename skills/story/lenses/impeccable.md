---
name: impeccable
stage: branch
---

# Impeccable

Review the front end diff from its source and from the screenshots the change summary lists. Open the changed components and judge them against the repository design rules: spacing, type, color, states, and accessibility. Check each interactive state (hover, focus, highlighted, chosen, disabled, empty, loading) and the keyboard path.

Verify an accessibility claim against the component library source or the official guidance before reporting it. Do not ask for an ARIA attribute that changes what a screen reader announces without proof.

Do not report taste, a redesign, or a state the story scopes out.

Evidence is the `path:line` of the markup or style, with the state it breaks.

## Output

Output only a JSON list. Each item: {"lens": "impeccable", "severity": "P1"|"P2"|"P3", "path": "file", "line": 0, "summary": "one sentence", "fix": "one sentence", "evidence": "the path:line of the markup or style, with the state it breaks"}. The path is the repository file the finding is about, or the plan document path when no code file applies. The line is 1-based, 0 when none. P1 breaks an acceptance criterion or corrupts data, P2 is a gap a reviewer would send back, P3 is worth a sentence. A clean result is []. Write no prose around the list.
