---
name: amend
description: "Apply a discovery or the ticked review findings to a story, or split one out into a new blocked issue. Used by /deckhand:next."
arguments: issue
user-invocable: false
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Read Write Edit Grep Glob
---

# Amend $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" amend context $issue`

The ticked review findings and the comments under Feedback are the amendment; otherwise state the
discovery in one sentence and name the criterion it serves. Once the board says In Progress the Plan
cannot change; record the discovery under Notes or use --new-issue. Edit the body into the draft,
keeping every heading, and run `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" amend apply $issue <draft> --note "<one line>"`.
For work that belongs in its own story, overwrite the draft with its whole body, all five sections,
and run `... amend apply $issue <draft> --new-issue "<title>"`. If it refuses, fix the rule it names
and rerun.

A ticked Plan finding is a rewrite with superpowers:writing-plans, inside the draft, still the whole
body. Keep its format: linear `### Task N` blocks with `**Files:**` and checkbox steps holding real
code and exact commands. More than eight tasks means more than one story; narrow Scope with the
user.

An amend serves the acceptance criteria as written; anything else is a new issue, reviewed later on
its own.
