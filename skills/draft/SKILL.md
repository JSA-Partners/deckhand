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

Read the code the story touches before you write examples. When a decision is the person's, never
guess it and never ask in the dialog: write the question in the shape `deckhand ask context $story`
prints, save it with `deckhand ask apply $story --file QUESTION.json`, and stop. The person's answer
wakes you. A draft proposes and never decides.

Write the draft to a temporary file outside the worktree and save it with
`deckhand draft apply $story --file DRAFT.md`, then stop. It refuses until the Contract is approved.
When a command refuses, fix the rule it names. A uv cache error or a sandbox refusal means the call
was chained: run each deckhand command as one bare call.
