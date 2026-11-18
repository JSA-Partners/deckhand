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

What is printed above names the open feature, its phase, and the next planning step. Work one
feature at a time, and one screen at a time: show one thing, get one answer, move on.

At each step offer two paths: write it yourself, or take a draft. A draft is a proposal, never a
decision. Offer options in the order given and never mark one as recommended.

Save every answer when it is given with `deckhand plan apply SECTION --file PATH`, adding
`--drafted` when the text came from a draft. Read a section back with `deckhand plan context
SECTION`. Stopping is always safe, because everything answered is already saved.

Use `deckhand feature apply` to open, pick, or park a feature. Shape the feature with
superpowers:brainstorming before its Frame is written. Run impeccable:impeccable for any story that
changes what a person sees.

Four decisions are fixed: the contract, the invariants, the sitting, and the feature approval. Run
`deckhand decide context KIND SUBJECT --team-key KEY`, pass the JSON it prints unchanged to the
question dialog, then run `deckhand decide apply NONCE --team-key KEY` once the person has
answered. Contract, invariants, and feature approval take the project id. A sitting takes the story.

Agents may research or draft sections in parallel only after the contract decision is applied.

Turn the settled plan into stories with `deckhand stories apply --file MAP.json`. Walk a story's
sitting with `deckhand sitting context REF --team-key KEY`, then record it with
`deckhand sitting apply REF --team-key KEY --file ANSWERS.md`.
