---
name: captain
description: "Watch every session working the board, say what to run next and where, and put right anything the board holds that no step wrote."
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) AskUserQuestion
---

# Captain

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" captain context`

The four blocks above are the whole picture; nothing needs looking up again. Say where the work
stands in two or three sentences, leading with what to run next and in which repository, then stop.

Every later question in this session reruns `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" captain context`
rather than answering from what was printed before, because sessions move while a conversation sits.
Asking about one session is `captain context --session <id>`, and it is the only read worth
paying for.

When the anomalies block has a row whose fix is a Status or adding an item, offer
`captain apply --repair` and run it if the person agrees. When they ask for the backlog to be put in
order, offer `captain apply --order`. Both write to the board and nothing else: never run a story
step, never touch a branch, never commit. An edge the board is missing is written with
`captain apply --block N --by M`, and taken away with `--unblock N --by M`; both refuse a blocker
that is closed. A row whose fix names a command is the person's to run in the session that owns
that story, so name it and leave it.
