---
name: feature
description: "Plan one feature a screen at a time, from its frame to its approval, saving each answer as it is given."
effort: high
allowed-tools: Bash(deckhand *) Read Grep Glob Agent AskUserQuestion Skill(superpowers:*) Skill(impeccable:*)
---

# Feature

!`deckhand feature context`

Above: the open feature, its phase, next step and input folder, for each file you hand in.
Work one feature and one screen at a time: show one thing, get one answer, move on. Each step offers writing it yourself or taking a draft, never
a decision. Keep the given option order; mark none recommended.

Open, pick, or park a feature with `deckhand feature apply new --name N [--reference REF]`,
`deckhand feature apply pick --project ID --weeks 1|2`, or `deckhand feature apply park --project ID`.
Shape it with superpowers:brainstorming before its Frame; use impeccable:impeccable for any story a
person sees.

`deckhand plan context` lists the sections; name one to read it back. Save every answer as
given with `deckhand plan apply SECTION --file PATH [--drafted]`, `--drafted` for a draft, so
stopping is safe.

At Facts, offer each unverified fact a spike, its one-line question in a file:
`deckhand hand apply spike --repository LABEL --file QUESTION.md`. Before the story map, read each
answered `Spike:` document with the person.

Turn the settled plan into stories: `deckhand stories context` prints the map's fields, each story's
decisions naming its expected items. Save it with `deckhand stories apply --file MAP.json`, which
names each REF.

For every decision, run `deckhand decide context KIND SUBJECT`, pass its JSON unchanged to the
question dialog, then `deckhand decide apply NONCE` once answered. Contract, invariants and
feature-approval take the project id; tier, state-chart and sitting take the story; finding takes a
story and `--finding ID`.

Decide the contract and invariants first; the contract gates the drafts and the sittings. Then take
each story in turn: when its hand left a Draft document, offer `deckhand draft apply REF take`;
decide its tier, walk its sitting with `deckhand sitting context REF`, decide its state-chart at
tier 2 or 3, record answers with `deckhand sitting apply REF --file ANSWERS.md`, and decide the
sitting. Ask each expected item with `deckhand decide context decision-item ITEM --story REF`, ITEM
its map text.

Then decide each gap `deckhand feature context` names. For the feature review, pass the lens and
target `deckhand feature-review context` prints to deckhand:reviewer, deckhand:confidence scores
the list; save it with `deckhand feature-review apply round --findings FILE --target HASH`, HASH
the target_hash line. A P1 is a gap: fix the Contract or story and review again, or the person
declines it. Decide the feature-approval once no gap is left.
