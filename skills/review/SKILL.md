---
name: review
description: "Review a planned story with every applicable lens, then post the findings."
arguments: issue
context: fork
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Read Write Agent
---

# Review $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" review context $issue`

If no findings path was printed above, say so and stop. Otherwise run the deckhand:reviewer
agent with the body and the brief above; then the deckhand:skeptic agent with the reviewer's lines
and the body. The working directory is the repository the story belongs to, so file and line
evidence is welcome where a lens asks for it. Write the skeptic's lines to the findings file and run
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" review apply $issue <findings>`. Say what it printed.
