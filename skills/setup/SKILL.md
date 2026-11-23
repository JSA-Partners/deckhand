---
name: setup
description: "Fit a Linear team to the story process, name its repositories and clones on this machine, set the slot cap, and print the line that launches the captain."
disable-model-invocation: true
allowed-tools: Bash(deckhand *) AskUserQuestion
---

# Setup

!`deckhand setup context`

Above: what setup found for the team in config and what it would change, or what setup needs.
Without a team, ask the person for its key: the prefix of their Linear team's issue ids.
When it names a missing credentials file, tell the person which file and stop; it holds a secret,
so they write it themselves, owner-only, and never paste it here.

Then settle, one question at a time:

- Each repository the team works in, with a short label: `--repository LABEL=owner/name`. The label
  becomes a Linear label in the Repository group.
- Where each one is cloned on this machine: `--clone LABEL=PATH`, an existing directory.
- How many hands may run at once: `--slot-cap N`, a positive number. Each hand is a Claude Code
  session; start low.

Run `deckhand setup context --team-key KEY --repository LABEL=owner/name` with every flag settled,
show the person the planned changes and the files it would write, and on their yes run
`deckhand setup apply --team-key KEY --repository LABEL=owner/name` with the same flags. A rerun
changes only what differs, so running it again is safe.

The context also names the tool that draws the person's status line and how setup would add the
captain's one-line segment to it. Ask whether they want it, with what it would change; on their
yes run `deckhand setup apply --statusline`. It backs up each file first, and a rerun changes
nothing. `deckhand setup apply --statusline-remove` puts every file back as it was. When managed
settings block the status line, say so and leave it.

Say what setup did, then give the launch line it printed last, as a plain message the person
copies to a terminal. The captain runs in that session, under its own settings; this one is done.
