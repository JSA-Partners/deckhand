---
name: amend
description: "Apply a discovery or the ticked review findings to a story, or split one out into a new blocked issue."
arguments: issue
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Read Write Edit Grep Glob
---

# Amend $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" amend context $issue`

If the latest review has ticked findings, those are the amendment; otherwise state the discovery in
one sentence and name the acceptance criterion it serves. Edit the body into the draft, keeping
every heading, and run `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" amend apply $issue <draft> --note "<one line>"`.
For work that belongs in its own story, overwrite the draft with that new story's whole body, all
five sections of it, and run `... amend apply $issue <draft> --new-issue "<title>"`. Say what it
printed. If it refuses, fix the named rule in the draft and run it again.

A ticked finding about the Plan section is a rewrite of that section with superpowers:writing-plans,
inside the draft, which is still the whole body. Keep its format: linear `### Task N` blocks with
`**Files:**` and checkbox steps holding real code and exact commands. More than about eight tasks
means more than one story; narrow Scope with the user rather than trimming to fit.

An amend is for what the acceptance criteria as written need to become true. Anything else is a new
issue, and it goes through its own review later; do not review it now.
