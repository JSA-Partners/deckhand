---
name: feature
description: "Plan one feature a screen at a time, from its frame to its approval, saving each answer as it is given."
effort: high
allowed-tools: Bash(deckhand *) Read Grep Glob Agent AskUserQuestion Skill(superpowers:*) Skill(impeccable:*)
---

# Feature

!`deckhand feature context`

Above: the open feature, its phase, next step and input folder.
Work one feature and one screen at a time. Each step offers writing it yourself or taking a draft, never
a decision. Keep the given option order; mark none recommended.

Open a feature with `deckhand feature apply new --name N --weeks 1|2 [--reference REF]`; pick or
park one with `deckhand feature apply pick --project ID --weeks 1|2` or
`deckhand feature apply park --project ID`.
Shape it with superpowers:brainstorming before its Frame; use impeccable:impeccable for any story a
person sees.

`deckhand plan context` lists the sections and the answered spikes with their keys. Save every
answer as given with `deckhand plan apply SECTION --file PATH [--drafted]`, `--drafted` for a draft.

At Facts, ask each fact that reading the code cannot settle as a spike, its one-line question in a
file: `deckhand hand apply spike --repository LABEL --file QUESTION.md`. Keep drafting what does
not rest on it, then end the turn. On `deckhand: a spike was answered`, read it with
`deckhand plan context spike KEY` and fold it into Facts.

Turn the plan into stories: `deckhand stories context` prints the map's fields. Give each
story its tier, a state chart at tier 2 or 3, its after-merge chores, and each decision item with
your drafted answer. Save it with
`deckhand stories apply --file MAP.json`, which names each REF.

For every decision, run `deckhand decide context KIND SUBJECT`, pass its JSON unchanged to the
question dialog, then `deckhand decide apply NONCE` once answered. feature-approval, finding and
review-cap take the project id.

A saved Contract starts the drafts. Take each finished Draft with `deckhand draft apply REF take`,
unasked. Ask each expected item with `deckhand decide context decision-item ITEM --story REF`.

Settle each gap `deckhand feature context` names. For the feature review, pass the
lens and target `deckhand feature-review context` prints to deckhand:reviewer, deckhand:confidence
scores the list; save it with `deckhand feature-review apply round --findings FILE --target HASH`,
HASH the target_hash line. Decide a proposed finding with
`deckhand decide context finding PROJECT --finding ID`, the cap with
`deckhand decide context review-cap PROJECT --stage feature`. Once no gap remains, show
`deckhand plan context approval` and decide feature-approval.
