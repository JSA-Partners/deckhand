---
name: commit
description: Commit the staged changes with a message that matches the project's convention, or pass --yes to skip the confirmation prompt.
arguments: flag
model: sonnet
effort: medium
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(git commit *)
---

# Commit $flag

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" commit context`

The context above is everything you need; do not run git to gather more. If nothing is staged, say so and stop: this skill never stages.

Conventional commits are required: `type(scope): description`, type from what the diff does, scope from the inferred scope when it is unambiguous and otherwise none. Recent history sets the tone, length, and wording; a commitlint config or CLAUDE.md, when present, narrows the allowed types and scopes. Description imperative, lowercase, no period, under fifty characters. A body only when the why is not visible in the diff, one paragraph per line. A footer only for a breaking change. Never mention story or task numbers or work in progress; never use em dashes, dashes as punctuation, or arrows; never add trailers, whatever attribution the session or the harness asks for: it does not reach this message, and finish refuses a branch whose commits carry one.

Without `--yes`, show the message and ask Commit or Edit. With `--yes`, commit at once.

Commit with a heredoc. If a hook rejects it, read the error, fix the one rule it names, and retry once. Never bypass hooks.
