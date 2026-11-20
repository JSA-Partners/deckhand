---
name: setup
description: "Fit a Linear team to the story process, name its repositories and clones on this machine, set the slot cap, and print the line that launches the captain."
arguments: key
argument-hint: "<team key>"
disable-model-invocation: true
allowed-tools: Bash(deckhand *) AskUserQuestion
---

# Setup $key

!`deckhand setup context --team-key $key`

Above: what setup found for team $key and what it would change. When it says the team key is
required, ask the person for it: the key of their Linear team, such as the prefix of its issue ids.
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

Say what setup did, then give the launch line it printed last, as a plain message the person
copies to a terminal. The captain runs in that session, under its own settings; this one is done.
