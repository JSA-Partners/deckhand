---
name: ready
description: "Put a reviewed story on the board with its kind, points, and blockers. Used by /deckhand:next."
arguments: issue
user-invocable: false
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) AskUserQuestion
---

# Ready $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" ready context $issue`

Propose points from the Done stories that most resemble this one, then ask one question with three
choices. Board it: confirm kind, points, and blockers in that same question, then run
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" ready apply $issue --kind K --points P` with `--blocked-by M`
per blocker. Review it again: run the deckhand:review skill for $issue. Not yet: stop here.
