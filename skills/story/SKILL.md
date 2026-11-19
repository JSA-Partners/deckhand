---
name: story
description: "Build one story in its worktree, from implementation plan through review to its change summary and quiz, then stop for the person's sign-off."
arguments: story team_key
argument-hint: "<story> <team-key>"
effort: high
disable-model-invocation: true
allowed-tools: Bash Read Write Edit Grep Glob Agent Skill(superpowers:*) Skill(impeccable:*) Skill(deckhand:*)
---

# Story $story

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" build context $story --team-key "$team_key"`

Above: the story and its worktree. You are its hand and never ask the person: a decision of theirs
waits for their session, so name it and stop. REF is $story. On a rerun, carry on from the first
step the contexts show undone;
`deckhand merge context REF --team-key "$team_key"` says whether it is signed off. When a command
refuses, fix the rule it names. Keep every file you write for a command in a temporary directory
outside the worktree, which stays clean.

Make the worktree with `deckhand build apply REF worktree --team-key "$team_key"` and work there.
Write the implementation plan with superpowers:writing-plans and its manifest in the shape
`deckhand implplan context REF --team-key "$team_key"` prints, and save both with
`deckhand implplan apply REF --team-key "$team_key" --plan PLAN.md --manifest MANIFEST.json`.

Review the plan in rounds from `deckhand review context REF --stage plan --team-key "$team_key"`.
Dispatch one deckhand:reviewer per listed lens at once, each given the stage, its lens path,
the target path and any carried P1s. Give deckhand:confidence the joined list and the target path,
and save its output with
`deckhand review apply REF round --stage plan --team-key "$team_key" --findings FILE --target HASH`,
HASH the target_hash line. Record each finding you fix with
`deckhand review apply REF resolve --stage plan --team-key "$team_key" --accept ID`. Declining one,
or one that changes what the story delivers, is the person's. Rerun the context after
fixing: a clean stop carries on; a cap is the person's.

Build with superpowers:test-driven-development, and impeccable:impeccable when a person sees the
change. Commit with deckhand:commit `--yes`. Then run
`deckhand checks apply REF --team-key "$team_key"` and
`deckhand build apply REF push --team-key "$team_key"`.

Review the branch the same way with `--stage branch`, then run deckhand:document and commit its
edits. Repeat checks, push and branch review until the context stops clean at the pushed head.

On that head, `deckhand quiz context REF --team-key "$team_key"` gives the quiz size and both
shapes. Write the change summary by intent and the quiz on the diff and what is new. Save
them with `deckhand signoff apply REF summary --team-key "$team_key" --summary SUMMARY.json` and
`deckhand quiz apply REF --team-key "$team_key" --quiz QUIZ.json --summary SUMMARY.json`, then stop.

Once signed off, run `deckhand merge apply REF --team-key "$team_key"`, again while checks are
pending. A void means a new commit or failed check: fix it and review again.
