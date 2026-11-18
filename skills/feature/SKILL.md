---
name: feature
description: "Plan one feature a screen at a time, from its frame to its approval, saving each answer as it is given."
arguments: team_key
argument-hint: "<team-key>"
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Read Write Edit Grep Glob Agent AskUserQuestion Skill(superpowers:*) Skill(impeccable:*)
---

# Feature $team_key

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" feature context --team-key "$team_key"`

Above: the open feature, its phase and the next planning step. Work one screen at a time: show
one thing, get one answer, move on.

At each step offer two paths: write it yourself, or take a draft. A draft is a proposal, never a
decision. Offer options in the given order; never mark one recommended.

Open, pick, or park a feature with one of
`deckhand feature apply new --team-key "$team_key" --name N [--reference REF]`,
`deckhand feature apply pick --team-key "$team_key" --project ID --weeks 1|2`, or
`deckhand feature apply park --team-key "$team_key" --project ID`. Shape the feature with
superpowers:brainstorming before writing its Frame. Run impeccable:impeccable for any story that
changes what a person sees.

`deckhand plan context --team-key "$team_key"` lists the sections; name one to read it back. Save
each answer as given with
`deckhand plan apply SECTION --team-key "$team_key" --file PATH [--drafted]`, `--drafted` for a
draft. Stopping is safe.

Then write stories. `deckhand stories context --team-key "$team_key"` prints the
story map's fields; list in each story's decisions the items it expects. Save the map
with `deckhand stories apply --team-key "$team_key" --file MAP.json`, which names each story's REF.

For every decision, run
`deckhand decide context KIND SUBJECT --team-key "$team_key"`, pass the JSON it prints unchanged to
the question dialog, then run `deckhand decide apply NONCE --team-key "$team_key"` once
answered. Contract, invariants, and feature-approval take the project id; tier, state-chart, and
sitting take the story; finding takes a story and `--finding ID`.

Decide the contract and the invariants first. Agents may research and draft in parallel once the story map
is saved; the contract decision gates the sitting. Then, per story: decide its tier, then walk its sitting with `deckhand sitting context REF --team-key "$team_key"`. When the sitting shows tier 2 or
3, decide its state-chart too. Record the answers with
`deckhand sitting apply REF --team-key "$team_key" --file ANSWERS.md`, then decide the sitting.

Ask each decision item a story expects with
`deckhand decide context decision-item ITEM --story REF --team-key "$team_key"`, ITEM its text in
the map.

Then run `deckhand feature context --team-key "$team_key"` and decide each gap. For the
feature review, run `deckhand feature-review context --team-key "$team_key"` and pass its lens and
target to deckhand:reviewer; save each round's JSON with
`deckhand feature-review apply round --team-key "$team_key" --findings FILE --target HASH`. Decide
the feature-approval once no gap is left.
