---
name: next
description: "Run the step a story is due: review, amend, ready, start, finish, or merge, reading where it is from the issue."
arguments: issue
argument-hint: "<issue-number>"
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(git *) Read Write Edit Grep Glob Agent AskUserQuestion Skill(deckhand:*) Skill(superpowers:*)
---

# Next $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" next context $issue`

If no number was given, ask which story.

The step printed above is the one due. Follow its instructions as printed, against the context under
them, and ask the one question they say a person answers. Where those instructions refer to a block
that is not above, run that step's own context command first, as the printed lines say. A printed
choose is that question: ask it, then follow the instructions for the answer. A printed merge, done,
or stop has nothing to run; what it printed is the whole answer.

Close with three parts: the lines the last command printed, in a code block; the link it printed;
and its `Next:` line, verbatim. For start, the branch name and the subjects of the commits it made
stand in for the plan it printed. When no command ran, close the same way with the lines above.
