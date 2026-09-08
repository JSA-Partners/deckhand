---
name: finish
description: "Review the branch with the user, pass the gates, and open the pull request."
arguments: issue
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(git *) Read Write Edit Grep Glob Agent AskUserQuestion
---

# Finish $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" finish context $issue`

Ask the user to run the branch review command above and paste the export. Fix every comment, and
commit each fix with the deckhand:commit skill. Then run the deckhand:document skill.

Show the pull request block and get one approval of it. The repository squashes, so that title and
body are the commit that lands on main, and that approval is the loop's human gate. Take the check
commands from CLAUDE.md or the detected list, then run
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" finish apply $issue --actual P --approved <HEAD sha> --check "<cmd>"`,
adding `--breaking "<text>"` when a client of this change must react, which puts a `!` in that title
and a `BREAKING CHANGE` footer in that body. Say what it printed; if it refuses, fix the named gate
and run it again.

Actual is in the estimate's own units.
