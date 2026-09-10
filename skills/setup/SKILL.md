---
name: setup
description: "Link this repository to its GitHub project, set squash merges, fix its fields, and list what the API cannot do."
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(gh project *) AskUserQuestion
---

# Setup

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" setup context`

Settle which project this repository works against, then apply.

- Exactly one linked project: say which one and run `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" setup apply`. Offer to work against a different one instead with `--project N`.
- Otherwise ask which open project to use, offering to create one with `gh project create --owner <owner> --title <name>` when there is none, and run `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" setup apply --project N`. Add `--owner O` when the project belongs to someone other than the repository owner, `@me` for your own.

When several projects stay linked afterwards, tell the user to pin the number they chose in the `env` block of `.claude/settings.json` as `DECKHAND_PROJECT`, so every later command resolves the same project.

Tell the person what setup did and what the checklist says is left, each with its page and click,
in plain words, and which of those actions is permanent. Say "setup complete" when nothing is left.
