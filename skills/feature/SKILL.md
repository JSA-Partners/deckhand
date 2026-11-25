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

Open, pick, or park a feature with `deckhand feature apply new --name N [--reference REF]`,
`deckhand feature apply pick --project ID --weeks 1|2`, or `deckhand feature apply park --project ID`.
Shape it with superpowers:brainstorming before its Frame; use impeccable:impeccable for any story a
person sees.

`deckhand plan context` lists the sections; name one to read it back. Save every answer as
given with `deckhand plan apply SECTION --file PATH [--drafted]`, `--drafted` for a draft.

At Facts, ask each unverified fact as a spike, its one-line question in a file:
`deckhand hand apply spike --repository LABEL --file QUESTION.md`. Keep drafting what does not rest
on it, then end the turn. On `deckhand: a spike was answered`, read it with
`deckhand plan context spike KEY` and fold it into Facts.

Turn the settled plan into stories: `deckhand stories context` prints the map's fields, each story's
decisions naming its expected items. Save it with `deckhand stories apply --file MAP.json`.

For every decision, run `deckhand decide context KIND SUBJECT`, pass its JSON unchanged to the
question dialog, then `deckhand decide apply NONCE` once answered. Contract, invariants,
feature-approval, finding and review-cap take the project id; tier, state-chart and sitting take the
story.

Decide the contract and invariants first; the contract gates the drafts and the sittings. Then take
each story in turn: when its hand left a Draft document, offer `deckhand draft apply REF take`;
decide its tier, walk its sitting with `deckhand sitting context REF`, decide its state-chart at
tier 2 or 3, record answers with `deckhand sitting apply REF --file ANSWERS.md`, and decide the
sitting. Ask each expected item with `deckhand decide context decision-item ITEM --story REF`, ITEM
its map text.

Settle each gap `deckhand feature context` names as it says. For the feature review, pass the
lens and target `deckhand feature-review context` prints to deckhand:reviewer, deckhand:confidence
scores the list; save it with `deckhand feature-review apply round --findings FILE --target HASH`,
HASH the target_hash line. Decide a proposed finding with
`deckhand decide context finding PROJECT --finding ID`, the cap with
`deckhand decide context review-cap PROJECT --stage feature`. Decide feature-approval once no gap
remains.
