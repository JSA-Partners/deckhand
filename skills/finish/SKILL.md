---
name: finish
description: "Read the branch, record what it taught, pass the gates, and open the pull request. Used by /deckhand:next."
arguments: issue
effort: high
user-invocable: false
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(git *) Read Write Edit Grep Glob Agent AskUserQuestion
---

# Finish $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" finish context $issue`

Read the branch first. Ask the user to run `tuicr -r origin/main..HEAD --stdout` and paste the
export, or to say there are no comments. Fix every comment, committing each with deckhand:commit,
then ask for `tuicr -r <clean sha>..HEAD --stdout` over the new commits. Repeat until a pass is
clean; that commit is the one --approved names.

Then run the deckhand:document skill: what the branch taught goes into the docs before a gate runs,
and the files it lists go out with the printed lines.

Show the `## Pull request` block above and take one approval of it. The repository squashes, so that
title and body are the commit that lands on main, and that approval is the loop's human gate. Take Actual
in the estimate's own units and the check commands from CLAUDE.md or the detected list, then run
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" finish apply $issue --actual P --approved <the clean sha> --check "<cmd>"`,
adding `--breaking "<text>"` when a client of this change must react, which puts a `!` in that title
and a `BREAKING CHANGE` footer in that body. If it refuses, fix the gate it names and run it again.
