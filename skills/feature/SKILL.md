---
name: feature
description: "Plan one feature from its frame to its approval, drafting and saving each section without asking."
effort: high
allowed-tools: Bash(deckhand *) Read Grep Glob Agent AskUserQuestion Skill(superpowers:*) Skill(impeccable:*)
---

# Feature

!`deckhand feature context`

Above: the open feature, its phase, next step and input folder.
Work one feature at a time. Keep the given option order; mark none recommended.

Open a feature with `deckhand feature apply new --name N --weeks 1|2 [--reference REF]`; pick or
park one with `deckhand feature apply pick --project ID --weeks 1|2` or
`deckhand feature apply park --project ID`. Read code only with Read and Grep in the planning
worktree; refresh it with `deckhand feature apply fetch` on resuming.
Shape it with superpowers:brainstorming before its Frame; use impeccable:impeccable for any story a
person sees.

`deckhand plan context` lists the sections and each answered spike's KEY. Draft and save every
section, asking nothing, with `deckhand plan apply SECTION --file PATH --drafted`.

At Facts, ask as a spike each fact that needs a command run, its one-line question in a file:
`deckhand hand apply spike --repository LABEL --file QUESTION.md`. Keep drafting what does not rest
on it, then end the turn. On `deckhand: a spike was answered`, read it with
`deckhand plan context spike KEY` and fold it into Facts.

Plan the stories from the fields `deckhand stories context` prints: a tier, a state chart at tier
2 or 3, after-merge chores, and decision items with drafted answers. Save the map with
`deckhand stories apply --file MAP.json`, which names each REF.

For every decision, run `deckhand decide context KIND SUBJECT`, pass its JSON unchanged to the
dialog, then `deckhand decide apply NONCE` once answered. feature-approval, finding and
review-cap take the project id.

A saved Contract starts the drafts. Take each finished Draft with `deckhand draft apply REF take`,
unasked. Ask each expected item with `deckhand decide context decision-item ITEM --story REF`.

Settle each gap `deckhand feature context` names. For the feature review, pass the
lens and target `deckhand feature-review context` prints to deckhand:reviewer, deckhand:confidence
scores the list; save it with `deckhand feature-review apply round --findings FILE --target HASH`,
HASH the target_hash line. Decide a proposed finding with
`deckhand decide context finding PROJECT --finding ID`, the cap with
`deckhand decide context review-cap PROJECT --stage feature`. Once no gap remains, and again
after revising for a Change, show `deckhand plan context approval` and decide feature-approval.
