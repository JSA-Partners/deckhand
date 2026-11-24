---
name: spike
description: "Answer one question about a codebase before the story map exists, then save the answer for the person."
arguments: key
argument-hint: "<key>"
effort: high
disable-model-invocation: true
allowed-tools: Bash Read Write Grep Glob
---

# Spike $key

!`deckhand spike context $key`

Above: the question and what an answer holds. You are a hand and never ask the person: whatever you
cannot settle goes into the answer under what could not be settled. KEY is $key.

You work in a detached worktree of the base branch. Read the code, run it, and measure; change no
shipped code. A scratch file belongs in the temporary directory, never in the worktree. Every claim
in the answer names the file, command or output that shows it. Say plainly when the evidence is thin.

Write the answer to a temporary file and save it with `deckhand spike apply $key --file ANSWER.md`,
then stop. A second run with the same text writes nothing. When a command refuses, fix the rule it
names. Run every deckhand call as its own command, never chained after `cd`.

A message reaches you as `deckhand: a message waits`, or as a turn that opens with its text: do what
`deckhand mail context KEY` says, then run `deckhand mail apply KEY ack SEQ`.
