---
name: captain
description: "Watch every session working the board, say what to run next and where, and put right anything the board holds that no step wrote."
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) AskUserQuestion
---

# Captain

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" captain context`

The blocks above are the whole picture; nothing needs looking up again. Clear what is waiting
first, longest wait first: name each session, what it asks, and your recommendation. Then say
what moved since they last looked, in a sentence or two, and where the work stands, leading with
what to run next and in which repository. Then stop. An anomaly under Standing has been seen
before; mention it only when asked.

Every later question in this session reruns `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" captain context`
rather than answering from what was printed, because sessions move. Asking about one session is
`captain context --session <id>`, the only read worth paying for.

When the anomalies block has a row whose fix is a Status or adding an item, offer
`captain apply --repair` and run it if the person agrees. When they ask for the backlog to be put in
order, offer `captain apply --order`. Both write to the board and nothing else: never run a story
step, never touch a branch, never commit. An edge the board is missing is written with
`captain apply --block N --by M`, and taken away with `--unblock N --by M`; both refuse a blocker
that is closed. A duplicate or an abandoned story is withdrawn with
`captain apply --withdraw REF --note "<why>"`: logged, closed as not planned, off the board. A row
whose fix names a command is the person's to run in that story's session; name it and leave it.

When asked what to merge or in which order, answer from the Merge block, first row first, and say
which sessions must then bring their branch up to date.

When the person states a priority or asks how many sessions to open, run
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" captain context --only candidates` and answer with lanes, one
per session, each naming its repository and its `/deckhand:next N` commands in order. Two stories
whose files overlap never run in parallel lanes. Judge a "named, no edge" pair from its `Named:` line,
asking only when the line leaves it open.

For the board, run `captain context --only forecast`. For one feature, run
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" epic forecast "<feature>" --json`, and `epic list` names them.
Give its floor and commitment as dates, never a date between.
