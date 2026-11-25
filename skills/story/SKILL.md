---
name: story
description: "Build one story in its worktree, from implementation plan through review to its change summary, then stop for the person's sign-off."
arguments: story
argument-hint: "<story>"
effort: high
disable-model-invocation: true
allowed-tools: Bash Read Write Edit Grep Glob Agent Skill(superpowers:*) Skill(impeccable:*) Skill(deckhand:*)
---

# Story $story

!`deckhand build context $story`

Above: your worktree and what the person approved (Contract, Examples, Invariants, scope,
decisions); build to it, in its words. REF is $story. Never ask or address the person, nor name a
command in text: their decision, or a choice you would settle in prose, goes to
`deckhand ask apply REF --file QUESTION.json`, shaped as `deckhand ask context REF` prints; then
stop. A Claude Code config change adds `--edit`. On `deckhand: a message waits`, or a turn opening
with message text, do what `deckhand mail context REF` says, then `deckhand mail apply REF ack SEQ`.
A rerun resumes at the first step the contexts show undone; `deckhand merge context REF` shows a
sign-off. Fix the rule a refusing command names; on RATELIMITED, wait in-turn and rerun.
Run each deckhand call alone, never after `cd`. Input files go in a temporary
directory outside the worktree. Never narrate reviewer returns or reread a context.

Plan with superpowers:writing-plans and a manifest shaped as
`deckhand implplan context REF` prints; save both with `deckhand implplan apply REF --plan PLAN.md --manifest MANIFEST.json`.

Review the plan in rounds from `deckhand review context REF --stage plan`: one deckhand:reviewer per
lens at once, each given its stage, scope, lens, target and spec paths, and carried findings, then
deckhand:confidence on the joined list, target and spec. Save with
`deckhand review apply REF round --stage plan --findings FILE --target HASH`. Resolve a round in one
`deckhand review apply REF resolve --stage plan --accept ID --propose-accept ID --propose-decline ID --reason TEXT`,
repeating ID flags under one `--reason`; a delivery-changing finding takes a proposal, never
`--accept`. Declines, scope changes, caps and open findings are the person's; no docs decision
excuses one. Fix, then rerun context.

Build with superpowers:test-driven-development, and impeccable:impeccable when a person sees it;
commit with `deckhand build apply REF commit --message FILE`, then deckhand:document, committing its
edits. Sync with `deckhand build apply REF sync`, committing resolved conflicts, then
`deckhand checks apply REF` and `deckhand build apply REF push`.

Review the branch likewise with `--stage branch`. After each round fix, commit, rerun checks and
push; every new head owes a round until clean or proceeded.

Write the change summary by intent, shaped as `deckhand signoff context REF` prints, with
`to_check` the riskiest lines and why each matters; save with
`deckhand signoff apply REF summary --summary SUMMARY.json`, then stop.

After sign-off, run `deckhand merge apply REF`; fix and review a void. Once merged, take main with
`deckhand after apply REF sync` and do what you can of `deckhand after context REF` with
`deckhand after apply REF done N --note TEXT`, others with `person N --why TEXT`.
