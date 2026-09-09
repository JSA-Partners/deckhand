---
name: review
description: "Review a planned story with every applicable lens, then post the findings. Used by /deckhand:next."
arguments: issue
context: fork
user-invocable: false
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Read Write Agent
---

# Review $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" review context $issue`

If no findings path was printed above, say so and stop. Otherwise run the deckhand:reviewer
agent with the body and the brief above; then the deckhand:skeptic agent with the reviewer's lines
and the body. The working directory is the repository the story belongs to, so file and line
evidence is welcome where a lens asks for it. Write the skeptic's lines to the findings file and run
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" review apply $issue <findings>`. If it refuses, fix the rule
it names and run it again.

Your final message is the comment URL the command printed and the next line it printed, in that
order, and nothing else.
