---
name: feature
description: "Plan one feature a screen at a time, from its frame to its approval, saving each answer as it is given."
arguments: team_key
argument-hint: "<team-key>"
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(deckhand *) Read Write Edit Grep Glob Agent AskUserQuestion Skill(superpowers:*) Skill(impeccable:*)
---

# Feature $team_key

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" feature context --team-key "$team_key"`

Above: the open feature, its phase and next step. Work one feature and one screen at a time: show
one thing, get one answer, move on. Each step offers writing it yourself or taking a draft,
a proposal, never a decision. Keep the given option order; mark none recommended.

Open, pick, or park a feature with
`deckhand feature apply new --team-key "$team_key" --name N [--reference REF]`,
`deckhand feature apply pick --team-key "$team_key" --project ID --weeks 1|2`, or
`deckhand feature apply park --team-key "$team_key" --project ID`. Shape it with
superpowers:brainstorming before its Frame; use impeccable:impeccable for any story a person sees.

`deckhand plan context --team-key "$team_key"` lists the sections; name one to read it back. Save
every answer when it is given with
`deckhand plan apply SECTION --team-key "$team_key" --file PATH [--drafted]`, `--drafted` for a
draft. Stopping is safe: every answer is already saved.

Turn the settled plan into stories: `deckhand stories context --team-key "$team_key"` prints the
map's fields, each story's decisions naming its expected items. Save it with
`deckhand stories apply --team-key "$team_key" --file MAP.json`, which names each REF.

For every decision, run `deckhand decide context KIND SUBJECT --team-key "$team_key"`, pass its JSON
unchanged to the question dialog, then run `deckhand decide apply NONCE --team-key "$team_key"` once
answered. Contract, invariants and feature-approval take the project id; tier, state-chart and
sitting take the story; finding takes a story and `--finding ID`.

Decide the contract and invariants first; the contract gates the sittings. Agents may research and
draft in parallel once the map is saved. Then take each story in turn: decide its tier, walk its
sitting with `deckhand sitting context REF --team-key "$team_key"`, decide its state-chart at tier 2
or 3, record answers with
`deckhand sitting apply REF --team-key "$team_key" --file ANSWERS.md`, and decide the sitting. Ask
each expected item with
`deckhand decide context decision-item ITEM --story REF --team-key "$team_key"`, ITEM its map text.

Then decide each gap `deckhand feature context --team-key "$team_key"` names. For the feature
review, pass the lens and target `deckhand feature-review context --team-key "$team_key"` prints to
deckhand:reviewer, have deckhand:confidence score the list, and save it with
`deckhand feature-review apply round --team-key "$team_key" --findings FILE --target HASH`, HASH the
target_hash line. A P1 is a gap: fix the Contract or story and review again, or the person declines
it. Decide the feature-approval once no gap is left.
