---
name: story
description: "Build one story in its worktree, from implementation plan through review to its change summary and quiz, then stop for the person's sign-off."
arguments: story
argument-hint: "<story>"
effort: high
disable-model-invocation: true
allowed-tools: Bash Read Write Edit Grep Glob Agent Skill(superpowers:*) Skill(impeccable:*) Skill(deckhand:*)
---

# Story $story

!`deckhand build context $story`

Above: the story and its worktree. You are its hand and never ask the person: a decision of theirs
waits for their session, so name it and stop. REF is $story. On a rerun, carry on from the first
step the contexts show undone; `deckhand merge context REF` says whether it is signed off. When a command
refuses, fix the rule it names. A uv cache error or a sandbox refusal means the call was chained: run it alone. Keep files you write for a command in a temporary directory outside the worktree.

Make the worktree with `deckhand build apply REF worktree`; work there.
Write the implementation plan with superpowers:writing-plans and its manifest in the shape
`deckhand implplan context REF` prints, and save both with
`deckhand implplan apply REF --plan PLAN.md --manifest MANIFEST.json`.

Review the plan in rounds from `deckhand review context REF --stage plan`.
Dispatch one deckhand:reviewer per listed lens at once, each given the stage, lens path,
target path and any carried P1s. Give deckhand:confidence the joined list and target path,
and save its output with
`deckhand review apply REF round --stage plan --findings FILE --target HASH`,
HASH the target_hash line. Record each finding you fix with
`deckhand review apply REF resolve --stage plan --accept ID`. Declining one, or changing what the story delivers, is the person's. Rerun the context after
fixing: clean or proceeded carries on; a cap is the person's.

Build with superpowers:test-driven-development, and impeccable:impeccable when a person sees the
change. Commit with `deckhand build apply REF commit --message FILE`: a conventional subject
under 72, no trailer. Bring main in with `deckhand build apply REF sync`; resolve any conflict
it names, then commit. Then run `deckhand checks apply REF` and
`deckhand build apply REF push`.

Review the branch the same way with `--stage branch`, then run deckhand:document and commit its
edits. Repeat checks, push and review until the context stops clean at the pushed head.

On that head, `deckhand quiz context REF` gives the quiz size and shapes. Write the change summary by intent and the quiz on the diff. Save
them with `deckhand signoff apply REF summary --summary SUMMARY.json` and
`deckhand quiz apply REF --quiz QUIZ.json --summary SUMMARY.json`, then stop.

Once signed off, run `deckhand merge apply REF`, again while checks are pending. A void means a new commit or failed check: fix and review again.
