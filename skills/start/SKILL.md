---
name: start
description: "Branch an approved story, or pick one up where it stopped, and implement its plan."
arguments: issue
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(git *) Read Write Edit Grep Glob Agent AskUserQuestion
---

# Start $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" start apply $issue`

If it refused, say why and stop. Otherwise run superpowers:subagent-driven-development against the
plan above, skipping tasks the commit list already covers, and commit each task with the
deckhand:commit skill. Reasoning that would become a long comment belongs in docs/claude/ via the
deckhand:document skill, not in the code.

Read the plan against the code before you implement it. Every drift line is a reference the plan
made that no longer resolves; open what it names, and if a task's approach no longer works, run
/deckhand:amend $issue so the story and the plan agree before you write anything.

When execution stops on something the story did not anticipate, ask one question: is it required
for the acceptance criteria as written to become true? Yes, /deckhand:amend $issue. No,
/deckhand:amend $issue --new-issue. Then carry on.
