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
decisions); build to it, in its wording. REF is $story. Never ask the person: save a decision of
theirs with `deckhand ask apply REF --file QUESTION.json`, shaped as `deckhand ask context REF`
prints, and stop; the answer wakes you. A change to Claude Code's own config adds `--edit`. On a
rerun, resume at the first step the contexts show undone; `deckhand merge context REF` says whether
it is signed off. When a command refuses, fix the rule it names. A raw write fails; use deckhand. A
uv cache error or a sandbox refusal means the call was chained: run it alone. Keep input files in a
temporary directory outside the worktree.

Work in the worktree above. Plan with superpowers:writing-plans and a manifest shaped as
`deckhand implplan context REF` prints; save both with `deckhand implplan apply REF --plan PLAN.md --manifest MANIFEST.json`.

Review the plan in rounds from `deckhand review context REF --stage plan`: one deckhand:reviewer per
listed lens at once, each given the stage, lens path, target path and any carried findings, then
deckhand:confidence on the joined list and target path. Save it with
`deckhand review apply REF round --stage plan --findings FILE --target HASH`. Record a fix with
`deckhand review apply REF resolve --stage plan --accept ID`, or propose a decline with `--propose-decline ID --reason TEXT`; declining, changing what the story delivers, a cap and any finding left open are the person's; never write a docs decision to excuse one. Rerun the context after fixing: clean, cap-clear or proceeded carries on.

Build with superpowers:test-driven-development, and impeccable:impeccable when a person sees the
change. Commit with `deckhand build apply REF commit --message FILE`. Bring main in with
`deckhand build apply REF sync`, resolve any conflict, and commit. Then run `deckhand checks apply REF` and `deckhand build apply REF push`.

Review the branch the same way with `--stage branch`, then run deckhand:document and commit its
edits. Repeat checks, push and branch review until the context stops clean, cap-clear or
proceeded at the pushed head.

There, `deckhand quiz context REF` gives the quiz size and shapes. Write the change summary by intent
and the quiz on the hunks the tuicr review shows; save them with
`deckhand signoff apply REF summary --summary SUMMARY.json` and
`deckhand quiz apply REF --quiz QUIZ.json --summary SUMMARY.json`, then stop.

Once signed off, run `deckhand merge apply REF`. A void means a new commit or failed check: fix, review again. Once merged, do what you can of
`deckhand after context REF`, each with `deckhand after apply REF done N --note TEXT`, the rest with
`person N --why TEXT`.
