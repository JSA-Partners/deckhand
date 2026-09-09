---
name: start
description: "Branch a boarded story and implement its plan. Used by /deckhand:next."
arguments: issue
effort: high
user-invocable: false
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(git *) Read Write Edit Grep Glob Agent AskUserQuestion
---

# Start $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" start context $issue`

Read the plan against the code before writing. A drift line is a reference that no longer resolves:
open it. So is a task whose approach no longer works. Either one is for the deckhand:amend skill
while the story is Backlog; a Story or Scope that no longer holds is for deckhand:review instead.

Then run `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" start apply $issue`; if it refuses, say why and stop.
Run superpowers:subagent-driven-development on the plan, skipping tasks the commits cover,
committing each with deckhand:commit. A deviation the acceptance criteria require is posted with
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" comment $issue "Deviation: <what and why>"`; work they do not
require goes to deckhand:amend with --new-issue.

Carry on to the end of the plan and stop there. The branch is read by whoever finishes it, so say
the branch name and the subjects of the commits you made.
