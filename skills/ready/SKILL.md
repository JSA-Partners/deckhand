---
name: ready
description: "Put an approved story on the board with its kind, points, and blockers."
arguments: issue
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) AskUserQuestion
---

# Ready $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" ready context $issue`

If Approval is waiting or there is no review yet, say so and stop. Propose points from the Done
stories that most resemble this one, then confirm kind, points, and blockers with the user in one
question. Run `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" ready apply $issue --kind K --points P` with
`--blocked-by M` per blocker, and say what it printed.

Nothing clears Blocked when the last blocker closes; that is the accepted trade, because
`/deckhand:start` checks the blockers live.
