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

Above: the story, its worktree, and what the person approved (Contract, Examples, Invariants, scope,
decisions); build to it, in its words. REF is $story. Never ask or address the person, nor name a
command in text: their decision, or a choice you would settle in prose, goes to
`deckhand ask apply REF --file QUESTION.json`, shaped as `deckhand ask context REF` prints; then
stop. A Claude Code config change adds `--edit`. On `deckhand: a message waits`, or a turn opening
with message text, do what `deckhand mail context REF` says, then `deckhand mail apply REF ack SEQ`.
A rerun resumes at the first step the contexts show undone; `deckhand merge context REF` shows a
sign-off. Fix the rule a refusing command names; on RATELIMITED, wait in-turn and rerun; a raw
write fails. Run each deckhand call alone, never after `cd`. Input files go in a temporary
directory outside the worktree. Never narrate reviewer returns or reread a context.

Plan with superpowers:writing-plans and a manifest shaped as
`deckhand implplan context REF` prints; save both with `deckhand implplan apply REF --plan PLAN.md --manifest MANIFEST.json`.

Review the plan in rounds from `deckhand review context REF --stage plan`: one deckhand:reviewer per
listed lens at once, each given its stage, lens path, target path and carried findings, then
deckhand:confidence on the joined list and target. Save with
`deckhand review apply REF round --stage plan --findings FILE --target HASH`. Record a fix with
`deckhand review apply REF resolve --stage plan --accept ID`, or propose a decline with `--propose-decline ID --reason TEXT`; declining, a scope change, a cap and any open finding are the person's; no docs decision excuses one. After fixing, rerun the context: clean, cap-clear or proceeded carries on.

Build with superpowers:test-driven-development, and impeccable:impeccable when a person sees
it. Commit with `deckhand build apply REF commit --message FILE`. Sync main with
`deckhand build apply REF sync`, resolve conflicts, commit. Then `deckhand checks apply REF` and `deckhand build apply REF push`.

Review the branch likewise with `--stage branch`, then deckhand:document; commit its
edits. Repeat checks, push and branch review until clean, cap-clear or
proceeded at the pushed head.

`deckhand quiz context REF` gives quiz size and shapes. Write the change summary by intent
and the quiz on the hunks tuicr shows; save with
`deckhand signoff apply REF summary --summary SUMMARY.json` and
`deckhand quiz apply REF --quiz QUIZ.json --summary SUMMARY.json`, then stop.

After sign-off, run `deckhand merge apply REF`; fix and review a void. Once merged, take main with
`deckhand after apply REF sync` and do what you can of `deckhand after context REF` with
`deckhand after apply REF done N --note TEXT`, others with `person N --why TEXT`.
