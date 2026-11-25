---
name: draft
description: "Draft one story from its stub and the approved Contract, for the person to take or rewrite."
arguments: story
argument-hint: "<story>"
effort: high
disable-model-invocation: true
allowed-tools: Bash Read Write Grep Glob
---

# Draft $story

!`deckhand draft context $story`

Above: the story stub, the Frame, the Shape, the approved Contract, the Examples and what a draft holds. You are its hand. REF is $story.
Write only what the Contract, the Examples and the stub support, and keep the story's own wording where it is clear. List each unknown as an open question; settle none.

Read the code the story touches before you write examples. When a decision is the person's, or a
choice you would settle in prose, never guess it, never ask in the dialog, and never address the
person or name a command in your text: write the question in the shape `deckhand ask context $story`
prints, save it with `deckhand ask apply $story --file QUESTION.json`, and stop. After
`deckhand: a message waits`, or a turn that starts with message text, read
`deckhand mail context REF`, do what it says, then run `deckhand mail apply REF ack SEQ`. A
draft proposes and never decides.

Write the draft to a temporary file outside the worktree and save it with
`deckhand draft apply $story --file DRAFT.md`, then stop. It refuses until the Contract is saved.
When a command refuses, fix the rule it names. Run every deckhand call as its own command, never
chained after `cd`, and do not reread a context already printed.
