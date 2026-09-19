---
name: document
description: "Capture what an implementation taught that the code cannot say into the project's docs/claude/ topic files, or pass audit to fix what the stale report lists instead."
arguments: mode topic
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Read Write Edit Grep Glob Bash(git diff *) Bash(git log *)
---

# Document $mode $topic

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" document context`

Read `${CLAUDE_SKILL_DIR}/reference.md` for the file types and shape.

With audit, the report is the deliverable: repoint or remove stale paths,
merge duplicated headings, and bring each file behind what it cites up to date. Otherwise the
arguments are a hint; fix what the report lists on the way.

Update each doc listed as naming a file this branch changed if the change made it wrong. Find
what the branch taught from its diff and log; the files say why. Keep only what a reader could
not get from the code or the history: a pattern and its rationale, a constraint discovered the hard
way, a decision and the alternative rejected, a recipe for a recurring task, a rule a tool enforces.

Destination, by reach: `docs/claude/<topic>.md` for this repository, one file per topic, integrated
rather than appended as a dated entry, a new topic file only when none fits; memory for something
true across repositories; `CLAUDE.md` only for the rare rule that must load every session. If
`docs/claude/` is missing, confirm where to write before creating it.

When unsure where a learning belongs, ask. List the files touched and stop.
