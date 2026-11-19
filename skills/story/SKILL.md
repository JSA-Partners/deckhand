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

Above: the story, its worktree, tier, tools and checks. You are its hand, a background session, and
never ask the person anything: a decision of theirs waits for their session, so name it and stop.
REF is $story. When a command refuses, fix the rule it names and run it again.

Make the worktree with `deckhand build apply REF worktree --team-key "$team_key"` and work there.
Write the implementation plan with superpowers:writing-plans and its manifest in the shape
`deckhand implplan context REF --team-key "$team_key"` prints. Save both with
`deckhand implplan apply REF --team-key "$team_key" --plan PLAN.md --manifest MANIFEST.json`.

Review the plan in rounds from `deckhand review context REF --stage plan --team-key "$team_key"`.
Dispatch one deckhand:reviewer per lens listed, all at once, each given the stage, its lens path,
the target path (write a printed diff to a file) and any carried P1s. Have deckhand:confidence
score the joined list, and save its output with
`deckhand review apply REF round --stage plan --team-key "$team_key" --findings FILE --target HASH`,
HASH the target_hash line. Record each finding you fix with
`deckhand review apply REF resolve --stage plan --team-key "$team_key" --accept ID`. One you would
decline, or one that changes what the story delivers, is the person's. Run the context again after
fixing: a clean stop carries on; a cap stop is the person's.

Build with superpowers:test-driven-development, and impeccable:impeccable when the story changes
what a person sees. Commit through deckhand:commit. On a clean tree run
`deckhand checks apply REF --team-key "$team_key"`, then
`deckhand build apply REF push --team-key "$team_key"` for the draft pull request.

Review the branch the same way with `--stage branch`. Then run deckhand:document. Its commits move
the head, so checks, push and branch review run again until the context stops clean.

On that head, `deckhand quiz context REF --team-key "$team_key"` gives the quiz size and both
shapes. Write the change summary by intent, and the quiz on the diff and what is new here. Save them
with `deckhand signoff apply REF summary --team-key "$team_key" --summary SUMMARY.json` and
`deckhand quiz apply REF --team-key "$team_key" --quiz QUIZ.json --summary SUMMARY.json`. Then stop
for the person's sign-off.

Once signed off, run `deckhand merge apply REF --team-key "$team_key"`, again while checks are
pending. A void means a new commit or a failed check: fix it, and the story returns to review.
